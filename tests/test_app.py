import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402
import config  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "BASE_URL", "https://sos.test")
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def register(client, name="Ana Pérez", notes="Alérgica a la penicilina", files=None):
    data = {"name": name, "notes": notes}
    if files:
        data["files"] = files
    return client.post("/api/register", data=data, content_type="multipart/form-data")


def test_register_creates_folder_and_files(client, tmp_path):
    r = register(client, files=[(io.BytesIO(b"hola"), "receta.txt")])
    assert r.status_code == 201
    body = r.get_json()
    folder = tmp_path / "users" / body["user_id"]
    info = json.loads((folder / "info.json").read_text(encoding="utf-8"))
    assert info["name"] == "Ana Pérez"
    assert info["notes"] == "Alérgica a la penicilina"
    assert (folder / "files" / "receta.txt").read_bytes() == b"hola"
    assert body["files"] == ["receta.txt"]
    assert body["edit_key"] == info["edit_key"] != info["view_key"]
    assert body["profile_url"] == f"https://sos.test/p/{body['user_id']}?k={info['view_key']}"


def test_register_requires_name(client):
    assert register(client, name="  ").status_code == 400


def test_malicious_filename_is_sanitized(client, tmp_path):
    body = register(client, files=[(io.BytesIO(b"x"), "../../evil.txt")]).get_json()
    assert body["files"] == ["evil.txt"]
    assert not (tmp_path / "evil.txt").exists()


def test_duplicate_filename_is_not_overwritten(client):
    body = register(client, files=[(io.BytesIO(b"1"), "a.txt"), (io.BytesIO(b"2"), "a.txt")]).get_json()
    assert body["files"] == ["a.txt", "a_1.txt"]


def test_edit_requires_valid_key(client):
    body = register(client).get_json()
    r = client.post(f"/api/profile/{body['user_id']}", data={"name": "X", "edit_key": "wrong"})
    assert r.status_code == 403
    r = client.get(f"/api/profile/{body['user_id']}?edit_key=wrong")
    assert r.status_code == 403


def test_edit_updates_adds_and_deletes_files(client, tmp_path):
    body = register(client, files=[(io.BytesIO(b"old"), "viejo.txt")]).get_json()
    uid, key = body["user_id"], body["edit_key"]
    r = client.post(
        f"/api/profile/{uid}",
        data={"name": "Ana P.", "notes": "nuevo", "edit_key": key, "delete": "viejo.txt",
              "files": [(io.BytesIO(b"new"), "nuevo.pdf")]},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert r.get_json()["files"] == ["nuevo.pdf"]
    got = client.get(f"/api/profile/{uid}?edit_key={key}").get_json()
    assert got["name"] == "Ana P." and got["notes"] == "nuevo"
    assert "edit_key" not in got


def test_delete_cannot_escape_folder(client, tmp_path):
    body = register(client).get_json()
    info_path = tmp_path / "users" / body["user_id"] / "info.json"
    client.post(f"/api/profile/{body['user_id']}",
                data={"name": "A", "edit_key": body["edit_key"], "delete": "../info.json"})
    assert info_path.is_file()


def test_sos_logs_alert_and_notifies(client, tmp_path):
    body = register(client).get_json()
    r = client.post("/api/sos", json={"user_id": body["user_id"], "edit_key": body["edit_key"],
                                      "lat": 10.4806, "lng": -66.9036, "accuracy": 12.5})
    assert r.status_code == 200
    alerts = (tmp_path / "users" / body["user_id"] / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
    alert = json.loads(alerts[-1])
    assert alert["maps_url"] == "https://maps.google.com/?q=10.4806,-66.9036"
    outbox = json.loads((tmp_path / "outbox.log").read_text(encoding="utf-8").splitlines()[-1])
    assert "Ana Pérez" in outbox["subject"]
    assert "10.4806, -66.9036" in outbox["body"]
    assert body["profile_url"] in outbox["body"]


def test_sos_without_location_still_sends(client, tmp_path):
    body = register(client).get_json()
    r = client.post("/api/sos", json={"user_id": body["user_id"], "edit_key": body["edit_key"],
                                      "location_error": "permiso de ubicación denegado"})
    assert r.status_code == 200
    outbox = json.loads((tmp_path / "outbox.log").read_text(encoding="utf-8").splitlines()[-1])
    assert "Ubicación no disponible (permiso de ubicación denegado)" in outbox["body"]


def test_sos_rejects_bad_key_and_bad_id(client):
    body = register(client).get_json()
    assert client.post("/api/sos", json={"user_id": body["user_id"], "edit_key": "x"}).status_code == 403
    assert client.post("/api/sos", json={"user_id": "../../etc", "edit_key": "x"}).status_code == 404


def test_profile_page_and_download(client):
    body = register(client, files=[(io.BytesIO(b"datos"), "info.txt")]).get_json()
    uid = body["user_id"]
    k = body["profile_url"].split("k=")[1]
    page = client.get(f"/p/{uid}?k={k}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Ana Pérez" in html and "Alérgica a la penicilina" in html and "info.txt" in html
    assert client.get(f"/p/{uid}?k=wrong").status_code == 404
    assert client.get(f"/p/{uid}").status_code == 404
    assert client.get(f"/p/{uid}/files/info.txt?k={k}").data == b"datos"
    assert client.get(f"/p/{uid}/files/info.txt?k=wrong").status_code == 404
    # The edit key must never be reachable through the view link.
    assert client.get(f"/p/{uid}/files/..%2Finfo.json?k={k}").status_code == 404
    assert body["edit_key"] not in html


def test_edit_key_does_not_open_view_page(client):
    body = register(client).get_json()
    assert client.get(f"/p/{body['user_id']}?k={body['edit_key']}").status_code == 404


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200 and b"sos-btn" in r.data


def test_sos_delivery_failure_returns_502_but_keeps_alert(client, tmp_path, monkeypatch):
    body = register(client).get_json()

    def boom(_alert):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(app_module.notifier, "send_alert", boom)
    r = client.post("/api/sos", json={"user_id": body["user_id"], "edit_key": body["edit_key"], "lat": 1, "lng": 2})
    assert r.status_code == 502 and "error" in r.get_json()
    assert (tmp_path / "users" / body["user_id"] / "alerts.jsonl").is_file()


# ---------- foto del SOS ----------

def test_foto_se_guarda_y_notifica(client, tmp_path):
    body = register(client).get_json()
    uid, key = body["user_id"], body["edit_key"]
    r = client.post("/api/foto", data={"user_id": uid, "edit_key": key,
                    "foto": (io.BytesIO(b"\xff\xd8\xff\xee jpeg"), "sos.jpg", "image/jpeg")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    out = r.get_json()
    assert out["ok"] is True and out["email"] is True
    fotos = list((tmp_path / "users" / uid / "sos_fotos").iterdir())
    assert len(fotos) == 1 and fotos[0].suffix == ".jpg"
    outbox = json.loads((tmp_path / "outbox.log").read_text(encoding="utf-8").splitlines()[-1])
    assert "Foto del SOS de Ana Pérez" in outbox["subject"]
    assert out["photo_url"] in outbox["body"]


def test_foto_rechaza_clave_mala_y_no_imagen(client, tmp_path):
    body = register(client).get_json()
    uid = body["user_id"]
    mala = client.post("/api/foto", data={"user_id": uid, "edit_key": "x",
                       "foto": (io.BytesIO(b"x"), "sos.jpg", "image/jpeg")},
                       content_type="multipart/form-data")
    assert mala.status_code == 403
    noimg = client.post("/api/foto", data={"user_id": uid, "edit_key": body["edit_key"],
                        "foto": (io.BytesIO(b"PK\x03\x04"), "x.zip", "application/zip")},
                        content_type="multipart/form-data")
    assert noimg.status_code == 400
    assert not (tmp_path / "users" / uid / "sos_fotos").exists()


def test_foto_se_descarga_solo_con_view_key(client, tmp_path):
    body = register(client).get_json()
    uid, key = body["user_id"], body["edit_key"]
    out = client.post("/api/foto", data={"user_id": uid, "edit_key": key,
                      "foto": (io.BytesIO(b"\xff\xd8\xff datos"), "sos.jpg", "image/jpeg")},
                      content_type="multipart/form-data").get_json()
    ruta = out["photo_url"].split("https://sos.test")[1]
    assert client.get(ruta).data == b"\xff\xd8\xff datos"
    sin_k = ruta.split("?")[0]
    assert client.get(sin_k).status_code == 404
    assert client.get(sin_k + "?k=wrong").status_code == 404
    # la edit_key NO abre la foto (solo la view_key del enlace)
    assert client.get(sin_k + "?k=" + key).status_code == 404


def test_foto_delivery_falla_pero_la_guarda(client, tmp_path, monkeypatch):
    body = register(client).get_json()
    monkeypatch.setattr(app_module.notifier, "send_photo", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    r = client.post("/api/foto", data={"user_id": body["user_id"], "edit_key": body["edit_key"],
                    "foto": (io.BytesIO(b"\xff\xd8\xff"), "sos.jpg", "image/jpeg")},
                    content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["email"] is False
    assert list((tmp_path / "users" / body["user_id"] / "sos_fotos").iterdir())


# ---------- recuperar por nombre (el telefono perdio el localStorage) ----------

def test_recuperar_devuelve_la_ficha_con_su_clave(client):
    creado = register(client, name="Ana Pérez").get_json()
    r = client.post("/api/recuperar", json={"name": "Ana Pérez"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["user_id"] == creado["user_id"]
    assert body["edit_key"] == creado["edit_key"]
    assert body["name"] == "Ana Pérez"
    assert body["profile_url"] == creado["profile_url"]


def test_recuperar_ignora_mayusculas_y_espacios_de_mas(client):
    creado = register(client, name="Ana Pérez").get_json()
    r = client.post("/api/recuperar", json={"name": "  ana   PÉREZ "})
    assert r.status_code == 200
    assert r.get_json()["user_id"] == creado["user_id"]


def test_recuperar_sin_coincidencia_da_404(client):
    register(client, name="Ana Pérez")
    r = client.post("/api/recuperar", json={"name": "Juan Otro"})
    assert r.status_code == 404


def test_recuperar_con_nombre_vacio_da_400(client):
    r = client.post("/api/recuperar", json={"name": "   "})
    assert r.status_code == 400


def test_recuperar_con_homonimos_no_entrega_ninguna_ficha(client):
    a = register(client, name="Ana Pérez").get_json()
    b = register(client, name="Ana Pérez").get_json()
    assert a["user_id"] != b["user_id"]
    r = client.post("/api/recuperar", json={"name": "Ana Pérez"})
    assert r.status_code == 409
    assert "edit_key" not in r.get_json()


def test_recuperado_puede_mandar_sos(client):
    register(client, name="Ana Pérez")
    p = client.post("/api/recuperar", json={"name": "Ana Pérez"}).get_json()
    r = client.post("/api/sos", json={
        "user_id": p["user_id"], "edit_key": p["edit_key"],
        "lat": 10.5, "lng": -66.9, "accuracy": 10,
    })
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_recuperar_ignora_tildes_y_signos(client):
    creado = register(client, name="Ana Pérez").get_json()
    for escrito in ["ana perez", "ANA PEREZ", "  Ana   Pérez  ", "Ana, Perez", "ána pérez"]:
        r = client.post("/api/recuperar", json={"name": escrito})
        assert r.status_code == 200, escrito
        assert r.get_json()["user_id"] == creado["user_id"], escrito


def test_recuperar_no_busca_por_trozos(client):
    """Un apellido suelto NO puede devolver la ficha: seria la de otra persona."""
    register(client, name="Ana Pérez")
    for trozo in ["perez", "ana", "Ana P"]:
        assert client.post("/api/recuperar", json={"name": trozo}).status_code == 404, trozo

