"""
MarketSense — alerts.py
Envoie un email HTML quand le signal global d'un onglet change.

Variables d'environnement Railway :
  SMTP_HOST      smtp.gmail.com
  SMTP_PORT      587
  SMTP_USER      votre@gmail.com
  SMTP_PASSWORD  mot-de-passe d'application Google (pas le mdp normal)
  ALERT_EMAILS   destinataire@mail.com,autre@mail.com  (séparés par virgule)
"""

import os
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text       import MIMEText
from datetime              import datetime, timezone

log = logging.getLogger("marketsense")

SMTP_HOST     = os.getenv("SMTP_HOST",     "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER",     "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
ALERT_EMAILS  = [e.strip() for e in os.getenv("ALERT_EMAILS", "").split(",") if e.strip()]

SIG_LABEL = {"buy": "🟢 Acheter", "sell": "🔴 Vendre", "neutral": "🟡 Attendre"}
SIG_COLOR = {"buy": "#1fd97e",    "sell": "#ff4f4f",    "neutral": "#f5a623"}
TAB_LABEL = {"bourse": "Bourse", "crypto": "Crypto", "matieres": "Matières premières"}
TAB_ICON  = {"bourse": "📈",      "crypto": "₿",        "matieres": "🥇"}


def configured() -> bool:
    return bool(SMTP_USER and SMTP_PASSWORD and ALERT_EMAILS)


# ── HTML template ─────────────────────────────────────────────────
def _html(changes: list[dict], ts: str) -> str:
    ts_fr = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%d/%m/%Y à %H:%M UTC")

    rows = ""
    for c in changes:
        tab   = TAB_LABEL.get(c["tab"], c["tab"])
        icon  = TAB_ICON.get(c["tab"], "")
        frm   = SIG_LABEL.get(c["from"], c["from"])
        to    = SIG_LABEL.get(c["to"],   c["to"])
        color = SIG_COLOR.get(c["to"],   "#e4e8f2")
        rows += f"""
        <tr style="border-bottom:1px solid rgba(255,255,255,0.06)">
          <td style="padding:14px 20px;font-weight:600;color:#e4e8f2">{icon} {tab}</td>
          <td style="padding:14px 20px;color:#5a6480;text-decoration:line-through">{frm}</td>
          <td style="padding:14px 20px;color:#3e4560;font-size:18px">→</td>
          <td style="padding:14px 20px;font-weight:700;font-size:17px;color:{color}">{to}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>MarketSense — Alerte signal</title>
</head>
<body style="margin:0;padding:0;background:#080a0f;font-family:'Segoe UI',Arial,sans-serif;color:#e4e8f2">
  <div style="max-width:580px;margin:40px auto;padding:0 16px">

    <!-- Header -->
    <div style="background:#0f1218;border:1px solid rgba(255,255,255,0.08);border-radius:16px 16px 0 0;padding:24px 28px;display:flex;align-items:center;gap:14px">
      <div style="width:40px;height:40px;background:#3b7fff;border-radius:10px;display:grid;place-items:center;font-size:20px;flex-shrink:0">📊</div>
      <div>
        <div style="font-size:18px;font-weight:700;letter-spacing:-0.3px">MarketSense</div>
        <div style="font-size:12px;color:#5a6480;margin-top:2px">Alerte de changement de signal</div>
      </div>
    </div>

    <!-- Body -->
    <div style="background:#111820;border:1px solid rgba(255,255,255,0.08);border-top:none;border-radius:0 0 16px 16px;padding:28px">
      <h2 style="margin:0 0 6px;font-size:20px;font-weight:600">Signal modifié !</h2>
      <p style="margin:0 0 24px;color:#7b849e;font-size:14px">Détecté le {ts_fr}</p>

      <table style="width:100%;border-collapse:collapse;background:#080a0f;border-radius:12px;overflow:hidden">
        <thead>
          <tr style="background:#0f1218">
            <th style="padding:10px 20px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.09em;color:#3e4560;font-weight:600">Onglet</th>
            <th style="padding:10px 20px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.09em;color:#3e4560;font-weight:600">Avant</th>
            <th style="padding:10px 20px"></th>
            <th style="padding:10px 20px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.09em;color:#3e4560;font-weight:600">Maintenant</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>

      <div style="margin-top:24px;padding:16px;background:#080a0f;border-radius:10px;border:1px solid rgba(255,255,255,0.06)">
        <p style="margin:0;font-size:13px;color:#5a6480;line-height:1.7">
          💡 <strong style="color:#7b849e">Conseil :</strong> Un changement de signal ne signifie pas qu'il faut agir immédiatement.
          Consultez l'ensemble des indicateurs avant toute décision d'investissement.
        </p>
      </div>

      <p style="margin:20px 0 0;font-size:12px;color:#3e4560;text-align:center;line-height:1.7">
        Vous recevez cet email car vous êtes abonné aux alertes MarketSense.<br>
        Pour vous désabonner, ouvrez l'application → ⚙ Paramètres → retirez votre email.
      </p>
    </div>
  </div>
</body>
</html>"""


def _text(changes: list[dict], ts: str) -> str:
    ts_fr = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%d/%m/%Y à %H:%M UTC")
    lines = [f"MarketSense — Alerte signal ({ts_fr})", ""]
    for c in changes:
        tab = TAB_LABEL.get(c["tab"], c["tab"])
        frm = SIG_LABEL.get(c["from"], c["from"])
        to  = SIG_LABEL.get(c["to"],   c["to"])
        lines.append(f"• {tab}: {frm}  →  {to}")
    lines += ["", "Ouvrez MarketSense pour consulter les indicateurs."]
    return "\n".join(lines)


# ── Public entry point ────────────────────────────────────────────
def send_alert(changes: list[dict], timestamp: str) -> bool:
    """
    Envoie l'alerte. Retourne True si succès.
    Appelé depuis asyncio.to_thread() pour ne pas bloquer la boucle.
    """
    if not configured():
        log.warning("[alerts] SMTP non configuré — alerte ignorée.")
        return False
    if not changes:
        return False

    tabs = ", ".join(TAB_LABEL.get(c["tab"], c["tab"]) for c in changes)
    n    = len(changes)

    try:
        msg             = MIMEMultipart("alternative")
        msg["Subject"]  = f"🔔 MarketSense — Signal modifié sur {n} onglet{'s' if n > 1 else ''} ({tabs})"
        msg["From"]     = f"MarketSense <{SMTP_USER}>"
        msg["To"]       = ", ".join(ALERT_EMAILS)
        msg.attach(MIMEText(_text(changes, timestamp), "plain", "utf-8"))
        msg.attach(MIMEText(_html(changes, timestamp), "html",  "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(SMTP_USER, SMTP_PASSWORD)
            srv.sendmail(SMTP_USER, ALERT_EMAILS, msg.as_string())

        log.info(f"[alerts] Email envoyé → {', '.join(ALERT_EMAILS)}  ({tabs})")
        return True

    except Exception as e:
        log.error(f"[alerts] Erreur envoi : {e}")
        return False
