#!/usr/bin/env python3
"""Envoie l'email d'alerte via alerts.py (inchangé). Appelé après la publication des données."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import alerts  # noqa: E402

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not alerts.configured():
    print("::warning::SMTP non configuré (secrets SMTP_USER, SMTP_PASSWORD, ALERT_EMAILS) : alerte non envoyée.")
    sys.exit(0)
sys.exit(0 if alerts.send_alert(payload["changes"], payload["ts"]) else 1)
