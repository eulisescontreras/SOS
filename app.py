import json
import re
import secrets
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

import config
import notifier

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024

USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SOS_PHOTO_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}\.jpg$")


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


def clave_nombre(nombre) -> str:
    """Clave de comparacion de un nombre: el MISMO nombre, escrito de cualquier forma.

    En una emergencia nadie teclea su nombre igual que el dia que se registro, y
    en el movil las tildes se pierden. Se ignoran mayusculas, tildes, espacios de
    mas y signos de puntuacion — pero NO se busca por trozos: "perez" no puede
    devolver la ficha de "Ana Perez", porque seria entregar los datos de otra
    persona.
    """
    texto = unicodedata.normalize("NFD", str(nombre))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = "".join(c if c.isalnum() or c.isspace() else " " for c in texto)
    return " ".join(texto.split()).casefold()


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


@app.get("/p/<user_id>/foto/<name>")
def download_foto(user_id, name):
    """Sirve una foto del SOS. Mismo candado que los archivos: la view_key del
    enlace que viaja en el correo. Sin ella NO se entrega (404)."""
    info = load_info(user_id)
    check_view_key(info, request.args.get("k", ""))
    if not SOS_PHOTO_RE.match(name or ""):
        abort(404)
    return send_from_directory(user_dir(user_id) / "sos_fotos", name)


# ---------- API ----------

@app.post("/api/recuperar")
def recuperar():
    """Recuperar la ficha por NOMBRE cuando el telefono perdio el localStorage.

    El id y las claves viven solo en el navegador: si se borran (cambio de
    telefono, modo incognito, limpieza del navegador) la persona quedaba fuera de
    su propia ficha y tenia que registrarse otra vez, duplicandola.

    Se exige que el nombre identifique a UNA sola persona: con dos homonimos no
    hay forma de saber cual es, y devolver el primero entregaria la ficha de un
    tercero.
    """
    data = request.get_json(silent=True) or {}
    buscado = clave_nombre(data.get("name", ""))
    if not buscado:
        return jsonify(error="Escribe tu nombre"), 400
    encontrados = []
    base = users_dir()
    if base.is_dir():
        for carpeta in sorted(base.iterdir()):
            if not carpeta.is_dir() or not USER_ID_RE.match(carpeta.name):
                continue
            path = carpeta / "info.json"
            if not path.is_file():
                continue
            try:
                info = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if clave_nombre(info.get("name", "")) == buscado:
                encontrados.append((carpeta.name, info))
                if len(encontrados) > 1:
                    break
    if not encontrados:
        return jsonify(error="No encontramos a nadie con ese nombre"), 404
    if len(encontrados) > 1:
        return jsonify(error="Hay varias personas con ese nombre"), 409
    user_id, info = encontrados[0]
    return jsonify(edit_key=info["edit_key"], **public_profile(user_id, info))


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


@app.post("/api/foto")
def sos_foto():
    """Foto OPCIONAL del SOS. Va SEPARADA de /api/sos a proposito: la alerta de
    ubicacion ya se envio y es el camino critico; la camara nunca la bloquea.
    La persona dio permiso de camara de forma VISIBLE en su telefono."""
    user_id = str(request.form.get("user_id", ""))
    info = load_info(user_id)
    check_edit_key(info, request.form.get("edit_key", ""))
    f = request.files.get("foto")
    if not f or not f.filename:
        return jsonify(error="No se recibió la foto"), 400
    if not (f.mimetype or "").startswith("image/"):
        return jsonify(error="El archivo no es una imagen"), 400
    fotos_dir = user_dir(user_id) / "sos_fotos"
    fotos_dir.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3) + ".jpg"
    f.save(fotos_dir / name)
    photo_url = f"{config.BASE_URL}/p/{user_id}/foto/{name}?k={info['view_key']}"
    try:
        notifier.send_photo(info["name"], photo_url)
    except Exception:
        # La foto YA quedo guardada; solo fallo el correo. No es un error para el cliente.
        app.logger.exception("SOS photo delivery failed for user %s", user_id)
        return jsonify(ok=True, photo_url=photo_url, email=False)
    return jsonify(ok=True, photo_url=photo_url, email=True)


@app.errorhandler(413)
def too_large(_e):
    return jsonify(error=f"Archivos demasiado grandes (máximo {config.MAX_UPLOAD_MB} MB)"), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
