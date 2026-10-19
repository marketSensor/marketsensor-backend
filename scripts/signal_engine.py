"""
Port Python du calcul de signaux du frontend (app.js : Calc, applyPatch, fetchLiveData, computeReco).

Règles reprises à l'identique :
  - chaque indicateur a un poids `w`, un signal par défaut et une source ('live' par défaut pour 125/132) ;
  - un indicateur patché (backend ou source navigateur) passe en 'live' avec son nouveau signal ;
  - signal d'un onglet = vote pondéré des indicateurs 'live' : achat si bp>=45, vente si sp>=35, sinon neutre.
Différence assumée : ici tous les groupes sont actifs (le navigateur peut en désactiver).
"""
import json
import math
import time
from pathlib import Path

import requests

TABS = ("bourse", "crypto", "matieres")
_DEFAULTS = json.loads(Path(__file__).with_name("indicator_defaults.json").read_text(encoding="utf-8"))


def js_round(x: float) -> int:
    """Math.round() de JavaScript (arrondi du .5 vers le haut), pas l'arrondi bancaire de Python."""
    return math.floor(x + 0.5)


# ── Calc (copie de app.js) ───────────────────────────────────────
def rsi(prices, p=14):
    if not prices or len(prices) < p + 1:
        return None
    g = l = 0.0
    for i in range(1, p + 1):
        d = prices[i] - prices[i - 1]
        if d > 0:
            g += d
        else:
            l -= d
    ag, al = g / p, l / p
    for i in range(p + 1, len(prices)):
        d = prices[i] - prices[i - 1]
        ag = (ag * (p - 1) + max(0, d)) / p
        al = (al * (p - 1) + max(0, -d)) / p
    return 100 if al == 0 else js_round(100 - 100 / (1 + ag / al))


def ema(prices, p):
    if not prices or len(prices) < p:
        return None
    k = 2 / (p + 1)
    e = sum(prices[:p]) / p
    for i in range(p, len(prices)):
        e = prices[i] * k + e * (1 - k)
    return e


def sma(prices, p):
    if not prices or len(prices) < p:
        return None
    s = prices[-p:]
    return sum(s) / len(s)


def macd_sign(prices):
    e12, e26 = ema(prices, 12), ema(prices, 26)
    return (e12 - e26) if (e12 and e26) else None


def rsi_sig(v):
    return "buy" if v < 35 else "sell" if v > 65 else "neutral"


def fng_sig(v):
    return "buy" if v < 30 else "sell" if v > 70 else "neutral"


# ── Sources que le navigateur interrogeait lui-même ──────────────
_UA = {"User-Agent": "MarketSense/1.0 (+https://github.com/marketSensor)"}


def _get_json(url: str, label: str, tries: int = 2):
    for n in range(tries):
        try:
            r = requests.get(url, headers=_UA, timeout=15)
            if r.status_code == 429 and n + 1 < tries:
                time.sleep(8)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[sources] {label}: {e}")
    return None


def fetch_browser_sources() -> dict:
    fng = _get_json("https://api.alternative.me/fng/?limit=1", "FNG")
    cg = _get_json("https://api.coingecko.com/api/v3/global", "CG global")
    time.sleep(2)  # CoinGecko est strict sur le débit
    px = _get_json("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
                   "?vs_currency=usd&days=365&interval=daily", "BTC prices")
    bc = _get_json("https://api.blockchain.info/stats", "Blockchain")
    return {
        "fng": float(fng["data"][0]["value"]) if fng and fng.get("data") else None,
        "btc_dom": cg["data"]["market_cap_percentage"]["btc"] if cg and cg.get("data") else None,
        "prices": [p[1] for p in px["prices"]] if px and px.get("prices") else None,
        "hash_rate": bc.get("hash_rate") if bc else None,
    }


def browser_signal_patches(src: dict) -> dict:
    """Équivalent de fetchLiveData() pour les indicateurs crypto du navigateur -> {tab: {id: {sig, raw}}}"""
    crypto = {}
    v = src.get("fng")
    if v is not None:
        crypto["cfg"] = {"sig": fng_sig(v), "raw": str(int(v))}
    dom = src.get("btc_dom")
    if dom is not None:
        dom = js_round(dom * 10) / 10
        crypto["btcdom"] = {"sig": "sell" if dom > 58 else "buy" if dom < 42 else "neutral", "raw": f"{dom:.1f}"}
    prices = src.get("prices")
    if prices and len(prices) >= 30:
        r = rsi(prices, 14)
        if r is not None:
            crypto["btcrsi"] = {"sig": rsi_sig(r), "raw": str(r)}
        m = macd_sign(prices)
        if m is not None:
            crypto["btcmacd"] = {"sig": "buy" if m > 0 else "sell", "raw": "Positif" if m > 0 else "Négatif"}
        mm111 = sma(prices, 111) if len(prices) >= 111 else None
        mm350 = sma(prices, 350) if len(prices) >= 350 else None
        if mm111 and mm350:
            ratio = mm111 / (2 * mm350)
            crypto["picycle"] = {"sig": "sell" if ratio >= 0.96 else "buy", "raw": f"{ratio * 100:.0f} %"}
    hr = src.get("hash_rate")
    if hr:
        eh = hr / 1e9 if hr > 1e8 else hr / 1e6 if hr > 1e5 else hr
        crypto["hashrate"] = {"sig": "buy" if eh > 400 else "neutral", "raw": f"{js_round(eh)} EH/s"}
    return {"crypto": crypto}


# ── Recommandation (applyPatch + computeReco) ────────────────────
def effective_weights(inds):
    """Poids effectifs : le poids cumulé d'une famille d'indicateurs corrélés est plafonné (champ fc)."""
    fam = {}
    for i in inds:
        if i.get("f"):
            fam[i["f"]] = fam.get(i["f"], 0) + i["w"]
    return [i["w"] * i["fc"] / fam[i["f"]] if i.get("f") and fam[i["f"]] > i["fc"] else i["w"] for i in inds]


def _vote(inds):
    live = [i for i in inds if i["source"] == "live"]
    b = s = t = 0.0
    for i, w in zip(live, effective_weights(live)):
        t += w
        if i["sig"] == "buy":
            b += w
        elif i["sig"] == "sell":
            s += w
    bp = js_round(b / t * 100) if t else 0
    sp = js_round(s / t * 100) if t else 0
    return {"sig": "buy" if bp >= 45 else "sell" if sp >= 35 else "neutral", "bp": bp, "sp": sp}


def compute_reco(patches_in_order: list[dict]) -> dict:
    """
    patches_in_order : liste de {tab: {id: {"sig": ...}}} appliquée dans l'ordre (backend puis navigateur),
    comme app.js. Retourne {tab: {"sig","bp","sp","horizons":{"c":{...},"l":{...}}}}
    (c = court terme, l = long terme ; le signal principal combine les deux).
    """
    out = {}
    for tab in TABS:
        inds = [dict(i) for g in _DEFAULTS[tab] for i in g["indicators"]]
        for patches in patches_in_order:
            for ind_id, patch in (patches.get(tab) or {}).items():
                target = next((i for i in inds if i["id"] == ind_id), None)  # findInd : premier trouvé
                if target is not None:
                    target["sig"] = patch.get("sig", target["sig"])
                    target["source"] = "live"
        out[tab] = {**_vote(inds),
                    "horizons": {h: _vote([i for i in inds if i.get("h") == h]) for h in ("c", "l")}}
    return out
