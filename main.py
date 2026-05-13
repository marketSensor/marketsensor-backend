"""
MarketSense — main.py  v3
Endpoints :
  GET  /                 → healthcheck
  GET  /api/indicators   → indicateurs (cache 1h)
  POST /api/signals      → frontend reporte les signaux, déclenche alertes
  GET  /api/history      → historique des signaux (max 90 jours)
  GET  /api/calendar     → calendrier macro à venir
"""

import time
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime   import datetime, timezone, date

from fastapi              import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses    import JSONResponse
from pydantic             import BaseModel

import indicators
import alerts

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("marketsense")

# ── État global ───────────────────────────────────────────────────
_cache: dict           = {"data": None, "ts": 0.0, "loading": False}
_analytics_cache: dict = {"data": None, "ts": 0.0, "loading": False}
_prev_signals: dict = {}                       # derniers signaux connus
_history: list      = []                       # [(ts, {bourse, crypto, matieres})]
CACHE_TTL          = 3600    # indicateurs : 1h
ANALYTICS_TTL      = 14400   # analytics   : 4h
HISTORY_MAX_ITEMS   = 90 * 24                  # ~90 jours à 1 rafraîch./heure


# ── Calendrier macro (événements programmés) ──────────────────────
MACRO_CALENDAR = [
    # ── Fed (FOMC) 2026 ──────────────────────────────────────────
    {"date": "2026-06-16", "event": "Réunion Fed (FOMC)",            "impact": "high",   "category": "bourse"},
    {"date": "2026-07-28", "event": "Réunion Fed (FOMC)",            "impact": "high",   "category": "bourse"},
    {"date": "2026-09-15", "event": "Réunion Fed (FOMC)",            "impact": "high",   "category": "bourse"},
    {"date": "2026-10-27", "event": "Réunion Fed (FOMC)",            "impact": "high",   "category": "bourse"},
    {"date": "2026-12-15", "event": "Réunion Fed (FOMC)",            "impact": "high",   "category": "bourse"},
    # ── Fed 2027 ─────────────────────────────────────────────────
    {"date": "2027-01-26", "event": "Réunion Fed (FOMC)",            "impact": "high",   "category": "bourse"},
    {"date": "2027-03-16", "event": "Réunion Fed (FOMC)",            "impact": "high",   "category": "bourse"},
    {"date": "2027-05-04", "event": "Réunion Fed (FOMC)",            "impact": "high",   "category": "bourse"},
    # ── CPI USA 2026 ─────────────────────────────────────────────
    {"date": "2026-05-13", "event": "CPI USA (inflation)",           "impact": "high",   "category": "bourse"},
    {"date": "2026-06-10", "event": "CPI USA (inflation)",           "impact": "high",   "category": "bourse"},
    {"date": "2026-07-15", "event": "CPI USA (inflation)",           "impact": "high",   "category": "bourse"},
    {"date": "2026-08-12", "event": "CPI USA (inflation)",           "impact": "high",   "category": "bourse"},
    {"date": "2026-09-09", "event": "CPI USA (inflation)",           "impact": "high",   "category": "bourse"},
    {"date": "2026-10-14", "event": "CPI USA (inflation)",           "impact": "high",   "category": "bourse"},
    {"date": "2026-11-12", "event": "CPI USA (inflation)",           "impact": "high",   "category": "bourse"},
    {"date": "2026-12-10", "event": "CPI USA (inflation)",           "impact": "high",   "category": "bourse"},
    # ── CPI USA 2027 ─────────────────────────────────────────────
    {"date": "2027-01-13", "event": "CPI USA (inflation)",           "impact": "high",   "category": "bourse"},
    {"date": "2027-02-10", "event": "CPI USA (inflation)",           "impact": "high",   "category": "bourse"},
    {"date": "2027-03-10", "event": "CPI USA (inflation)",           "impact": "high",   "category": "bourse"},
    # ── NFP — Emploi USA 2026 ─────────────────────────────────────
    {"date": "2026-06-05", "event": "NFP — Emploi USA",             "impact": "medium", "category": "bourse"},
    {"date": "2026-07-02", "event": "NFP — Emploi USA",             "impact": "medium", "category": "bourse"},
    {"date": "2026-08-07", "event": "NFP — Emploi USA",             "impact": "medium", "category": "bourse"},
    {"date": "2026-09-04", "event": "NFP — Emploi USA",             "impact": "medium", "category": "bourse"},
    {"date": "2026-10-02", "event": "NFP — Emploi USA",             "impact": "medium", "category": "bourse"},
    {"date": "2026-11-06", "event": "NFP — Emploi USA",             "impact": "medium", "category": "bourse"},
    {"date": "2026-12-04", "event": "NFP — Emploi USA",             "impact": "medium", "category": "bourse"},
    # ── Jackson Hole 2026 ────────────────────────────────────────
    {"date": "2026-08-21", "event": "Symposium Jackson Hole (Fed)",  "impact": "high",   "category": "bourse"},
    # ── OPEC+ 2026 ───────────────────────────────────────────────
    {"date": "2026-06-01", "event": "Réunion OPEC+",                "impact": "high",   "category": "matieres"},
    {"date": "2026-11-02", "event": "Réunion OPEC+",                "impact": "high",   "category": "matieres"},
    # ── Ethereum ─────────────────────────────────────────────────
    {"date": "2026-09-15", "event": "Ethereum — mise à jour réseau", "impact": "medium", "category": "crypto"},
    # ── Bitcoin ──────────────────────────────────────────────────
    {"date": "2028-04-17", "event": "Halving Bitcoin #5 (estimé)",   "impact": "high",   "category": "crypto"},
    # ── BCE (Banque Centrale Européenne) 2026 ────────────────────
    {"date": "2026-06-04", "event": "Réunion BCE",                  "impact": "high",   "category": "bourse"},
    {"date": "2026-07-23", "event": "Réunion BCE",                  "impact": "high",   "category": "bourse"},
    {"date": "2026-09-10", "event": "Réunion BCE",                  "impact": "high",   "category": "bourse"},
    {"date": "2026-10-22", "event": "Réunion BCE",                  "impact": "high",   "category": "bourse"},
    {"date": "2026-12-10", "event": "Réunion BCE",                  "impact": "high",   "category": "bourse"},
    # ── Or & Métaux ───────────────────────────────────────────────
    {"date": "2026-06-03", "event": "World Gold Council — rapport T1", "impact": "medium", "category": "matieres"},
    {"date": "2026-09-01", "event": "World Gold Council — rapport T2", "impact": "medium", "category": "matieres"},
    # ── Uranium ───────────────────────────────────────────────────
    {"date": "2026-09-07", "event": "World Nuclear Association Symposium", "impact": "medium", "category": "matieres"},
]


# ── Refresh ───────────────────────────────────────────────────────
async def _refresh():
    if _cache["loading"]:
        return
    _cache["loading"] = True
    log.info("Chargement des indicateurs…")
    try:
        data = await asyncio.to_thread(indicators.get_all)
        _cache["data"] = data
        _cache["ts"]   = time.time()
        nb_b = len(data.get("bourse",   {}))
        nb_c = len(data.get("crypto",   {}))
        nb_m = len(data.get("matieres", {}))
        log.info(f"Cache OK — bourse:{nb_b} crypto:{nb_c} matieres:{nb_m}")
    except Exception as e:
        log.error(f"Erreur refresh : {e}")
    finally:
        _cache["loading"] = False


async def _auto_refresh_loop():
    await asyncio.sleep(5)
    await _refresh()
    while True:
        await asyncio.sleep(CACHE_TTL)
        await _refresh()


# ── Lifespan ─────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_auto_refresh_loop())
    log.info("Serveur prêt — données en cours de chargement en arrière-plan.")
    yield
    task.cancel()


# ── App ──────────────────────────────────────────────────────────
app = FastAPI(title="MarketSense API", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ── Models ───────────────────────────────────────────────────────
class SignalReport(BaseModel):
    bourse:   str   # "buy" | "sell" | "neutral"
    crypto:   str
    matieres: str
    bourse_bp:   int = 0
    bourse_sp:   int = 0
    crypto_bp:   int = 0
    crypto_sp:   int = 0
    matieres_bp: int = 0
    matieres_sp: int = 0


# ── Routes ───────────────────────────────────────────────────────
@app.get("/", tags=["health"])
async def root():
    age = round(time.time() - _cache["ts"]) if _cache["data"] else None
    return {
        "status":  "ok",
        "name":    "MarketSense API",
        "version": "3.0.0",
        "ready":   _cache["data"] is not None,
        "loading": _cache["loading"],
        "cache_age_s":       age,
        "alerts_configured": alerts.configured(),
        "history_points":    len(_history),
    }


@app.get("/api/indicators", tags=["indicators"])
async def get_indicators(force: bool = False):
    if force:
        await _refresh()

    if not _cache["data"]:
        for _ in range(90):
            await asyncio.sleep(1)
            if _cache["data"]:
                break
        if not _cache["data"]:
            return JSONResponse({"error": "Données en cours de chargement."}, status_code=503)

    age = time.time() - _cache["ts"]
    if age > CACHE_TTL and not _cache["loading"]:
        asyncio.create_task(_refresh())

    return JSONResponse(_cache["data"])


@app.post("/api/signals", tags=["alerts"])
async def post_signals(report: SignalReport):
    """
    Le frontend reporte les signaux calculés.
    Déclenche une alerte email si un signal a changé depuis le dernier appel.
    Stocke l'entrée dans l'historique.
    """
    global _prev_signals, _history

    ts  = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    new = {"bourse": report.bourse, "crypto": report.crypto, "matieres": report.matieres}

    # Détecter les changements
    changes = [
        {"tab": tab, "from": _prev_signals[tab], "to": sig}
        for tab, sig in new.items()
        if tab in _prev_signals and _prev_signals[tab] != sig
    ]

    # Envoyer l'alerte si changement (dans un thread pour ne pas bloquer)
    if changes:
        log.info(f"[signals] Changement détecté : {changes}")
        asyncio.create_task(asyncio.to_thread(alerts.send_alert, changes, ts))

    # Stocker dans l'historique
    _history.append({
        "ts": ts,
        "bourse":   {"sig": report.bourse,   "bp": report.bourse_bp,   "sp": report.bourse_sp},
        "crypto":   {"sig": report.crypto,   "bp": report.crypto_bp,   "sp": report.crypto_sp},
        "matieres": {"sig": report.matieres, "bp": report.matieres_bp, "sp": report.matieres_sp},
    })
    if len(_history) > HISTORY_MAX_ITEMS:
        _history.pop(0)

    _prev_signals = new
    return {"ok": True, "changes": len(changes), "ts": ts}


@app.get("/api/history", tags=["history"])
async def get_history(limit: int = 30, tab: str = ""):
    """
    Retourne les N derniers points d'historique des signaux.
    ?tab=crypto pour filtrer sur un onglet.
    """
    items = _history[-limit:]
    if tab and tab in ("bourse", "crypto", "matieres"):
        items = [{"ts": h["ts"], tab: h[tab]} for h in items]
    return {"history": items, "total": len(_history)}


@app.get("/api/calendar", tags=["calendar"])
async def get_calendar(days: int = 60):
    """Retourne les événements macro des N prochains jours."""
    today = date.today()
    upcoming = []
    for ev in MACRO_CALENDAR:
        ev_date = date.fromisoformat(ev["date"])
        delta   = (ev_date - today).days
        if 0 <= delta <= days:
            upcoming.append({**ev, "days_from_now": delta})
    upcoming.sort(key=lambda x: x["date"])
    return {"events": upcoming, "count": len(upcoming)}

@app.get("/api/analytics", tags=["analytics"])
async def get_analytics_endpoint(force: bool = False):
    """
    Analytics avancées : corrélations, divergences, backtesting.
    Cache 4h — calcul ~30-60s au premier appel.
    """
    global _analytics_cache
    now = time.time()
    stale = not _analytics_cache["data"] or (now - _analytics_cache["ts"]) > ANALYTICS_TTL

    if force or stale:
        if not _analytics_cache["loading"]:
            _analytics_cache["loading"] = True
            try:
                data = await asyncio.to_thread(indicators.get_analytics)
                _analytics_cache["data"] = data
                _analytics_cache["ts"]   = time.time()
                log.info(f"[analytics] Cache mis à jour — {len(data.get('backtest', []))} backtest(s)")
            except Exception as e:
                log.error(f"[analytics] Erreur : {e}")
            finally:
                _analytics_cache["loading"] = False
        else:
            # Déjà en cours de calcul — attendre max 90s
            for _ in range(90):
                await asyncio.sleep(1)
                if not _analytics_cache["loading"]:
                    break

    if not _analytics_cache["data"]:
        return JSONResponse({"error": "Analytics en cours de calcul, réessayez dans 30s."}, status_code=503)

    return JSONResponse(_analytics_cache["data"])

