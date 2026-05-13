"""
MarketSense — backend FastAPI
Déploiement : Railway · Render · Fly.io

Le serveur répond IMMÉDIATEMENT au healthcheck Railway.
Le chargement des données se fait en tâche de fond après démarrage.

Endpoints :
  GET /               → healthcheck (toujours 200, même à froid)
  GET /api/indicators → indicateurs de marché (cache 1h)
"""

import time
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import indicators

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("marketsense")

# ── Cache en mémoire ─────────────────────────────────────────────
_cache: dict = {"data": None, "ts": 0.0, "loading": False}
CACHE_TTL = 3600  # 1 heure


# ── Refresh (thread séparé pour ne pas bloquer la boucle async) ──
async def _refresh():
    """Lance le fetch dans un thread et met le cache à jour."""
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
    """Rafraîchit le cache toutes les heures en arrière-plan."""
    await asyncio.sleep(5)          # laisser le serveur démarrer
    await _refresh()                # premier chargement
    while True:
        await asyncio.sleep(CACHE_TTL)
        await _refresh()


# ── Lifespan ─────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ⚠️  NE PAS await ici — le serveur doit répondre immédiatement
    # Le chargement démarre en arrière-plan, le healthcheck passe à froid
    task = asyncio.create_task(_auto_refresh_loop())
    log.info("Serveur prêt — chargement des données en arrière-plan…")
    yield
    task.cancel()


# ── App ──────────────────────────────────────────────────────────
app = FastAPI(
    title="MarketSense API",
    description="Indicateurs de marché en temps réel pour MarketSense",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


# ── Routes ───────────────────────────────────────────────────────
@app.get("/", tags=["health"])
async def root():
    """Healthcheck — répond toujours 200, même quand le cache est vide."""
    age = round(time.time() - _cache["ts"]) if _cache["data"] else None
    return {
        "status":  "ok",
        "name":    "MarketSense API",
        "ready":   _cache["data"] is not None,
        "loading": _cache["loading"],
        "cache_age_s":       age,
        "next_refresh_in_s": max(0, CACHE_TTL - age) if age is not None else None,
    }


@app.get("/api/indicators", tags=["indicators"])
async def get_indicators(force: bool = False):
    """
    Retourne tous les indicateurs.
    - Cache serveur 1h, rafraîchi automatiquement.
    - ?force=true pour forcer un rechargement immédiat.
    - Si le cache est vide (premier démarrage), répond 202 et attend.
    """
    # Forcer un refresh manuel
    if force:
        await _refresh()

    # Cache vide → on attend que le chargement de fond se termine (max 90s)
    if not _cache["data"]:
        log.info("Cache vide, attente du chargement initial…")
        for _ in range(90):
            await asyncio.sleep(1)
            if _cache["data"]:
                break
        if not _cache["data"]:
            return JSONResponse(
                {"error": "Données en cours de chargement, réessayez dans quelques secondes."},
                status_code=503,
            )

    # Cache périmé → refresh en arrière-plan (sans bloquer la réponse)
    age = time.time() - _cache["ts"]
    if age > CACHE_TTL and not _cache["loading"]:
        asyncio.create_task(_refresh())

    return JSONResponse(_cache["data"])
