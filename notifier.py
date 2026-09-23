"""Alert delivery.

send_alert() is the single entry point used by the app. Today only the "log"
backend exists; a real email backend (SMTP, SES, ...) can be added here later
by implementing a function with the same signature and selecting it through
NOTIFIER_BACKEND.
"""
import json
import sys
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


BACKENDS = {"log": _send_log}


def send_alert(alert: dict) -> None:
    subject, body = build_message(alert)
    backend = BACKENDS.get(config.NOTIFIER_BACKEND, _send_log)
    backend(config.ALERT_EMAIL, subject, body)
