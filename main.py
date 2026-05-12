"""
MarketSense — backend FastAPI
Déploiement : Railway · Render · Fly.io (voir README)

Endpoints :
  GET /              → health check
  GET /api/indicators → tous les indicateurs (cache 1h)
"""

import time
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import indicators

# ── Logging ─────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("marketsense")

# ── Cache en mémoire ─────────────────────────────────────────────
_cache: dict = {"data": None, "ts": 0.0}
CACHE_TTL = 3600  # 1 heure


# ── Lifespan : warm-up au démarrage ──────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Warm-up : chargement des indicateurs au démarrage…")
    try:
        await _refresh_cache()
        log.info("Warm-up terminé.")
    except Exception as e:
        log.warning(f"Warm-up échoué (données live indisponibles au démarrage) : {e}")
    yield


# ── App ──────────────────────────────────────────────────────────
app = FastAPI(
    title="MarketSense API",
    description="Indicateurs de marché en temps réel pour MarketSense",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # restreindre à votre domaine en production
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


# ── Helpers ──────────────────────────────────────────────────────
async def _refresh_cache():
    global _cache
    log.info("Rafraîchissement du cache…")
    data = await asyncio.to_thread(indicators.get_all)
    _cache = {"data": data, "ts": time.time()}
    log.info(f"Cache mis à jour — {len(data.get('bourse', {}))} indicateurs bourse, "
             f"{len(data.get('matieres', {}))} matières premières.")
    return data


# ── Routes ───────────────────────────────────────────────────────
@app.get("/", tags=["health"])
async def root():
    return {
        "status": "ok",
        "name": "MarketSense API",
        "cache_age_s": round(time.time() - _cache["ts"]) if _cache["data"] else None,
        "next_refresh_in_s": max(0, round(CACHE_TTL - (time.time() - _cache["ts"]))) if _cache["data"] else 0,
    }


@app.get("/api/indicators", tags=["indicators"])
async def get_indicators(force: bool = False):
    """
    Retourne tous les indicateurs de marché.
    Cache serveur de 1 heure.
    Passez `?force=true` pour forcer un rafraîchissement immédiat.
    """
    now = time.time()
    cache_stale = not _cache["data"] or (now - _cache["ts"]) > CACHE_TTL

    if force or cache_stale:
        try:
            data = await _refresh_cache()
        except Exception as e:
            log.error(f"Erreur lors du rafraîchissement : {e}")
            if _cache["data"]:
                log.info("Retour au cache existant.")
                return JSONResponse(_cache["data"])
            raise HTTPException(status_code=503, detail="Données indisponibles, réessayez dans quelques secondes.")
    else:
        data = _cache["data"]

    return JSONResponse(data)
