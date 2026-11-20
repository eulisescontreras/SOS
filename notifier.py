"""Alert delivery.

send_alert() is the single entry point used by the app. Today only the "log"
backend exists; a real email backend (SMTP, SES, ...) can be added here later
by implementing a function with the same signature and selecting it through
NOTIFIER_BACKEND.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

import config


def build_message(alert: dict) -> tuple[str, str]:
    """Return (subject, body) for an alert."""
    subject = f"🚨 SOS de {alert['name']}"
    if alert.get("lat") is not None and alert.get("lng") is not None:
        location = (
            f"Coordenadas: {alert['lat']}, {alert['lng']}"
            f" (precisión ±{alert.get('accuracy') or '?'} m)\n"
            f"Mapa: {alert['maps_url']}"
        )
    else:
        location = f"Ubicación no disponible ({alert.get('location_error') or 'sin detalle'})"
    body = (
        f"{alert['name']} activó el botón SOS.\n\n"
        f"Fecha/hora (UTC): {alert['time']}\n"
        f"{location}\n\n"
        f"Información y archivos de la persona:\n{alert['profile_url']}\n"
    )
    return subject, body


def _send_log(to: str, subject: str, body: str) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "to": to,
        "subject": subject,
        "body": body,
    }
    with open(config.DATA_DIR / "outbox.log", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # The console may not be UTF-8 (e.g. Windows cp1252): replace what it cannot encode.
    line = f"[SOS] to={to or '(ALERT_EMAIL not set)'} | {subject}\n{body}\n"
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    sys.stdout.write(line.encode(encoding, "replace").decode(encoding))
    sys.stdout.flush()


def _send_resend(to: str, subject: str, body: str) -> None:
    """Enviar por la API de Resend (HTTPS 443).

    Este VPS tiene bloqueada la salida SMTP (587/465/25 a smtp.gmail.com no
    responden), asi que el correo clasico no es una opcion: Resend viaja por
    HTTPS. Se deja SIEMPRE el registro en outbox.log antes de intentar el envio,
    para que una alerta nunca se pierda si la API falla.
    """
    _send_log(to, subject, body)
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key or not to:
        raise RuntimeError("resend: falta RESEND_API_KEY o ALERT_EMAIL")
    # ALERT_EMAIL admite VARIOS destinatarios separados por coma.
    destinos = [d.strip() for d in to.split(",") if d.strip()]
    payload = json.dumps({
        "from": os.environ.get("ALERT_FROM", "SOS <onboarding@resend.dev>"),
        "to": destinos,
        "subject": subject,
        "text": body,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        # ⚠️ El User-Agent NO es decorativo: Cloudflare esta delante de la API de
        # Resend y rechaza `Python-urllib/3.x` con "error code: 1010" (403).
        headers={"Authorization": "Bearer " + api_key,
                 "Content-Type": "application/json",
                 "User-Agent": "sos-app/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            detalle = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        # El cuerpo de la respuesta dice EXACTAMENTE por que rechaza; sin el, un
        # 403 no se distingue de otro y no hay forma de corregirlo.
        cuerpo = err.read().decode("utf-8", "replace")
        raise RuntimeError("resend %s: %s" % (err.code, cuerpo)) from None
    sys.stdout.write("[SOS] resend ok: %s\n" % detalle)
    sys.stdout.flush()


BACKENDS = {"log": _send_log, "resend": _send_resend}


def send_alert(alert: dict) -> None:
    subject, body = build_message(alert)
    backend = BACKENDS.get(config.NOTIFIER_BACKEND, _send_log)
    backend(config.ALERT_EMAIL, subject, body)


def send_photo(name: str, photo_url: str) -> None:
    """Aviso aparte con la foto del SOS (un enlace protegido por la view_key).
    Usa el MISMO backend y destinatario que la alerta de ubicacion."""
    subject = f"📷 Foto del SOS de {name}"
    body = (
        f"{name} envió una foto con su SOS.\n\n"
        f"Ver la foto (enlace privado):\n{photo_url}\n"
    )
    backend = BACKENDS.get(config.NOTIFIER_BACKEND, _send_log)
    backend(config.ALERT_EMAIL, subject, body)
