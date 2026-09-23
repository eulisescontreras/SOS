import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Recipient of SOS alerts (used by the real email backend, configured later on the server).
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")

# Public URL of the app, used to build the link sent in the alert (e.g. https://sos.example.com).
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")

# Where each person's folder is stored.
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))

# Alert delivery backend: "log" (default) writes to console + data/outbox.log.
NOTIFIER_BACKEND = os.environ.get("NOTIFIER_BACKEND", "log")
