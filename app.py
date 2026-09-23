import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

import config
import notifier

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024

USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


# ---------- storage helpers ----------

def users_dir() -> Path:
    return Path(config.DATA_DIR) / "users"


def user_dir(user_id: str) -> Path:
    if not USER_ID_RE.match(user_id or ""):
        abort(404)
    return users_dir() / user_id


def load_info(user_id: str) -> dict:
    path = user_dir(user_id) / "info.json"
    if not path.is_file():
        abort(404)
    return json.loads(path.read_text(encoding="utf-8"))


def save_info(user_id: str, info: dict) -> None:
    path = user_dir(user_id) / "info.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def check_edit_key(info: dict, key: str) -> None:
    if not key or not secrets.compare_digest(info["edit_key"], key):
        abort(403)


def check_view_key(info: dict, key: str) -> None:
    if not key or not secrets.compare_digest(info["view_key"], key):
        abort(404)


def save_uploads(user_id: str) -> list[str]:
    files_dir = user_dir(user_id) / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in request.files.getlist("files"):
        if not f or not f.filename:
            continue
        name = secure_filename(f.filename) or "archivo"
        target = files_dir / name
        # Never overwrite an existing file: add a numeric suffix.
        n = 1
        while target.exists():
            target = files_dir / f"{Path(name).stem}_{n}{Path(name).suffix}"
            n += 1
        f.save(target)
        saved.append(target.name)
    return saved


def list_files(user_id: str) -> list[str]:
    files_dir = user_dir(user_id) / "files"
    if not files_dir.is_dir():
        return []
    return sorted(p.name for p in files_dir.iterdir() if p.is_file())


def profile_url(user_id: str, info: dict) -> str:
    return f"{config.BASE_URL}/p/{user_id}?k={info['view_key']}"


def public_profile(user_id: str, info: dict) -> dict:
    return {
        "user_id": user_id,
        "name": info["name"],
        "notes": info["notes"],
        "files": list_files(user_id),
        "profile_url": profile_url(user_id, info),
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def to_float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---------- pages ----------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/p/<user_id>")
def view_profile(user_id):
    info = load_info(user_id)
    key = request.args.get("k", "")
    check_view_key(info, key)
    alerts = []
    alerts_path = user_dir(user_id) / "alerts.jsonl"
    if alerts_path.is_file():
        lines = alerts_path.read_text(encoding="utf-8").splitlines()
        alerts = [json.loads(line) for line in lines[-20:] if line.strip()][::-1]
    return render_template(
        "profile.html",
        user_id=user_id,
        info=info,
        files=list_files(user_id),
        alerts=alerts,
        key=key,
    )


@app.get("/p/<user_id>/files/<path:name>")
def download_file(user_id, name):
    info = load_info(user_id)
    check_view_key(info, request.args.get("k", ""))
    safe = secure_filename(name)
    if not safe or safe != name:
        abort(404)
    return send_from_directory(user_dir(user_id) / "files", safe)


# ---------- API ----------

@app.post("/api/register")
def register():
    name = (request.form.get("name") or "").strip()
    if not name:
        return jsonify(error="El nombre es obligatorio"), 400
    user_id = secrets.token_urlsafe(12)
    info = {
        "name": name[:120],
        "notes": (request.form.get("notes") or "").strip()[:5000],
        "edit_key": secrets.token_urlsafe(32),
        "view_key": secrets.token_urlsafe(32),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    user_dir(user_id).mkdir(parents=True, exist_ok=False)
    save_info(user_id, info)
    save_uploads(user_id)
    return jsonify(edit_key=info["edit_key"], **public_profile(user_id, info)), 201


@app.get("/api/profile/<user_id>")
def get_profile(user_id):
    info = load_info(user_id)
    check_edit_key(info, request.args.get("edit_key", ""))
    return jsonify(public_profile(user_id, info))


@app.post("/api/profile/<user_id>")
def update_profile(user_id):
    info = load_info(user_id)
    check_edit_key(info, request.form.get("edit_key", ""))
    name = (request.form.get("name") or "").strip()
    if not name:
        return jsonify(error="El nombre es obligatorio"), 400
    info["name"] = name[:120]
    info["notes"] = (request.form.get("notes") or "").strip()[:5000]
    info["updated_at"] = now_iso()
    files_dir = user_dir(user_id) / "files"
    for fname in request.form.getlist("delete"):
        safe = secure_filename(fname)
        if safe and safe == fname and (files_dir / safe).is_file():
            (files_dir / safe).unlink()
    save_info(user_id, info)
    save_uploads(user_id)
    return jsonify(public_profile(user_id, info))


@app.post("/api/sos")
def sos():
    data = request.get_json(silent=True) or {}
    user_id = str(data.get("user_id", ""))
    info = load_info(user_id)
    check_edit_key(info, str(data.get("edit_key", "")))
    lat, lng = to_float(data.get("lat")), to_float(data.get("lng"))
    alert = {
        "time": now_iso(),
        "name": info["name"],
        "lat": lat,
        "lng": lng,
        "accuracy": to_float(data.get("accuracy")),
        "location_error": str(data.get("location_error") or "")[:200] or None,
        "maps_url": f"https://maps.google.com/?q={lat},{lng}" if lat is not None and lng is not None else None,
        "profile_url": profile_url(user_id, info),
    }
    with open(user_dir(user_id) / "alerts.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(alert, ensure_ascii=False) + "\n")
    try:
        notifier.send_alert(alert)
    except Exception:
        # The alert is already stored in alerts.jsonl; report the delivery failure so the client retries.
        app.logger.exception("SOS delivery failed for user %s", user_id)
        return jsonify(error="La alerta se guardó pero no se pudo enviar"), 502
    return jsonify(ok=True, time=alert["time"])


@app.errorhandler(413)
def too_large(_e):
    return jsonify(error=f"Archivos demasiado grandes (máximo {config.MAX_UPLOAD_MB} MB)"), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
