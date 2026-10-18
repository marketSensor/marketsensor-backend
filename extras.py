"""
Indicateurs complémentaires — sources gratuites vérifiées depuis les serveurs GitHub.

  Macro / crédit : FRED (clé gratuite)         Sentiment : CNN Fear & Greed
  Volatilité     : yfinance (^SKEW, ^VIX3M)    Largeur   : yfinance (RSP/SPY, XLY/XLP)
  Crypto         : Coin Metrics (communauté), OKX, DefiLlama

Chaque fonction renvoie un dict {id: indicateur} ; un indicateur indisponible est simplement omis
(le site garde alors sa valeur par défaut « simulée » et le moteur de signaux l'ignore).
"""
import requests
import yfinance as yf

import indicators
from indicators import _ind, _norm, HEADERS, TIMEOUT


_ASOF = {}   # série FRED -> date de la dernière observation utilisée


def _fred(series_id: str, limit: int = 1):
    """Comme indicators._fred, mais mémorise la date de la dernière observation valide."""
    if not indicators.FRED_KEY:
        return None
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": series_id, "api_key": indicators.FRED_KEY, "file_type": "json",
                "sort_order": "desc", "limit": max(limit, 5) + 5},
        timeout=TIMEOUT, headers=HEADERS)
    obs = [(o["date"], float(o["value"])) for o in r.json().get("observations", []) if o["value"] != "."]
    if not obs:
        return None
    _ASOF[series_id] = obs[0][0]
    return [v for _, v in obs[:limit]]


_SERIES_OF = {"yield_curve": "T10Y3M", "hy_spread": "BAMLH0A0HYM2", "fin_stress": "STLFSI4",
              "sahm": "SAHMREALTIME", "fed_liq": "WALCL", "infl_be": "T10YIE", "erp": "DFII10"}


def _last_close(ticker: str, period="3mo"):
    h = yf.Ticker(ticker).history(period=period)["Close"].dropna()
    return h if len(h) else None


def _perf(series, days: int):
    """Performance (%) sur ~days séances."""
    if series is None or len(series) <= days:
        return None
    return (float(series.iloc[-1]) / float(series.iloc[-days - 1]) - 1) * 100


def _try(label, fn, out):
    try:
        r = fn()
        if r:
            for k, ind in r.items():
                if k in _SERIES_OF and _SERIES_OF[k] in _ASOF:
                    ind["asof"] = _ASOF[_SERIES_OF[k]]   # date de la donnée source
            out.update(r)
    except Exception as e:
        print(f"[extras] {label}: {e}")


# ── Macro & crédit (FRED) ────────────────────────────────────────────────────

def _yield_curve():
    v = _fred("T10Y3M")
    if not v:
        return None
    c = v[0]
    sig = "sell" if c < 0 else "buy" if c >= 0.5 else "neutral"
    return {"yield_curve": _ind(
        _norm(c, -1.5, 3), f"{c:+.2f} pt", "", sig,
        f"Courbe des taux 10 ans − 3 mois à {c:+.2f} pt (FRED) — "
        + ("inversée : historiquement le meilleur signal avancé de récession." if c < 0
           else "pente positive, pas de signal de récession." if c >= 0.5
           else "courbe quasi plate, à surveiller."))}


def _hy_spread():
    v = _fred("BAMLH0A0HYM2")
    if not v:
        return None
    s = v[0]
    sig = "buy" if s < 4 else "sell" if s > 5.5 else "neutral"
    return {"hy_spread": _ind(
        _norm(s, 2.5, 9), f"{s:.2f} %", "", sig,
        f"Écart de crédit obligations haut rendement à {s:.2f} % (ICE BofA via FRED) — "
        + ("appétit pour le risque intact, financement facile." if s < 4
           else "stress de crédit marqué : les marchés d'actions suivent souvent." if s > 5.5
           else "tension modérée sur le crédit."))}


def _fin_stress():
    v = _fred("STLFSI4")
    if not v:
        return None
    s = v[0]
    sig = "buy" if s < 0 else "sell" if s > 1.5 else "neutral"
    return {"fin_stress": _ind(
        _norm(s, -1.5, 3), f"{s:+.2f}", "", sig,
        f"Indice de stress financier (St. Louis Fed) à {s:+.2f} — "
        + ("conditions financières détendues (0 = moyenne)." if s < 0
           else "stress élevé, risque systémique à surveiller." if s > 1.5
           else "stress légèrement supérieur à la moyenne."))}


def _sahm():
    v = _fred("SAHMREALTIME")
    if not v:
        return None
    s = v[0]
    sig = "sell" if s >= 0.5 else "buy" if s < 0.3 else "neutral"
    return {"sahm": _ind(
        _norm(s, 0, 1), f"{s:.2f} pt", "", sig,
        f"Règle de Sahm à {s:.2f} pt (FRED) — "
        + ("seuil de 0,5 franchi : la récession est généralement déjà engagée." if s >= 0.5
           else "chômage stable, pas de signal de récession." if s < 0.3
           else "le chômage monte, proche du seuil d'alerte (0,5)."))}


def _fed_liquidity():
    v = _fred("WALCL", 14)
    if not v or len(v) < 14:
        return None
    chg = (v[0] / v[13] - 1) * 100
    sig = "buy" if chg > 1 else "sell" if chg < -1 else "neutral"
    return {"fed_liq": _ind(
        _norm(chg, -4, 4), f"{chg:+.1f} % / 3 mois", "", sig,
        f"Bilan de la Fed {chg:+.1f} % sur 3 mois (FRED) — "
        + ("injection de liquidités, favorable aux actifs risqués." if chg > 1
           else "resserrement de liquidité, vent contraire pour les actifs." if chg < -1
           else "bilan stable, liquidité neutre."))}


def _breakeven():
    v = _fred("T10YIE")
    if not v:
        return None
    b = v[0]
    sig = "buy" if b > 2.6 else "sell" if b < 1.8 else "neutral"
    return {"infl_be": _ind(
        _norm(b, 1, 3.5), f"{b:.2f} %", "", sig,
        f"Inflation anticipée à 10 ans à {b:.2f} % (FRED) — "
        + ("anticipations élevées, favorable aux matières premières et à l'or." if b > 2.6
           else "anticipations basses, peu de soutien pour les matières premières." if b < 1.8
           else "anticipations ancrées près de la cible de 2 %."))}


def _erp():
    """Prime de risque actions façon Shiller : rendement du CAPE (1/CAPE) moins le taux réel 10 ans."""
    cape = indicators._scrape_cape()
    tips = _fred("DFII10")
    if not cape or not tips:
        return None
    erp = 100 / cape - tips[0]
    sig = "buy" if erp > 4 else "sell" if erp < 1.5 else "neutral"
    return {"erp": _ind(
        _norm(erp, -1, 6), f"{erp:+.1f} %", "", sig,
        f"Prime de risque actions à {erp:+.1f} % (rendement CAPE {100/cape:.1f} % − taux réel {tips[0]:.1f} %) — "
        + ("les actions rémunèrent bien le risque par rapport aux obligations." if erp > 4
           else "les actions offrent peu de rémunération supplémentaire : valorisation tendue." if erp < 1.5
           else "rémunération du risque dans la moyenne."))}


# ── Sentiment & volatilité ───────────────────────────────────────────────────

def _cnn_fear_greed():
    r = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                     timeout=TIMEOUT, headers=HEADERS)
    fg = r.json()["fear_and_greed"]
    s = round(float(fg["score"]))
    rating = str(fg.get("rating", "")).lower()
    sig = "buy" if s < 25 else "sell" if s > 75 else "neutral"
    return {"fg_spx": _ind(
        s, str(s), "/100", sig,
        f"CNN Fear & Greed à {s}/100 ({rating}) — "
        + ("peur extrême : historiquement, zone d'achat contrariante." if s < 25
           else "avidité extrême : risque de correction." if s > 75
           else "sentiment intermédiaire."))}


def _vix_term():
    vix, vix3m = _last_close("^VIX", "1mo"), _last_close("^VIX3M", "1mo")
    if vix is None or vix3m is None:
        return None
    ratio = float(vix.iloc[-1]) / float(vix3m.iloc[-1])
    sig = "sell" if ratio > 1.05 else "buy" if ratio < 0.9 else "neutral"
    return {"vix_term": _ind(
        _norm(ratio, 0.7, 1.2), f"{ratio:.2f}", "", sig,
        f"Structure par terme du VIX (VIX/VIX3M) à {ratio:.2f} — "
        + ("inversée : panique à court terme sur le marché." if ratio > 1.05
           else "contango marqué : marché calme, peu de stress immédiat." if ratio < 0.9
           else "structure normale."))}


def _skew():
    s = _last_close("^SKEW", "1mo")
    if s is None:
        return None
    k = round(float(s.iloc[-1]))
    sig = "sell" if k > 150 else "buy" if k < 120 else "neutral"
    return {"skew": _ind(
        _norm(k, 110, 170), str(k), "", sig,
        f"Indice SKEW (Cboe) à {k} — "
        + ("forte demande de protection contre un krach : les institutionnels se couvrent." if k > 150
           else "peu de couverture contre les chocs extrêmes." if k < 120
           else "demande de protection normale."))}


# ── Largeur de marché & rotation ─────────────────────────────────────────────

def _breadth():
    rsp, spy = _last_close("RSP", "6mo"), _last_close("SPY", "6mo")
    a, b = _perf(rsp, 63), _perf(spy, 63)
    if a is None or b is None:
        return None
    d = a - b
    sig = "buy" if d > 0 else "sell" if d < -3 else "neutral"
    return {"breadth": _ind(
        _norm(d, -8, 5), f"{d:+.1f} pt / 3 mois", "", sig,
        f"S&P 500 équipondéré (RSP) vs pondéré (SPY) : {d:+.1f} pt sur 3 mois — "
        + ("la hausse est large, signe de santé du marché." if d > 0
           else "la hausse repose sur quelques méga-capitalisations : marché fragile." if d < -3
           else "écart modéré, concentration légèrement élevée."))}


def _rotation():
    xly, xlp = _last_close("XLY", "6mo"), _last_close("XLP", "6mo")
    a, b = _perf(xly, 63), _perf(xlp, 63)
    if a is None or b is None:
        return None
    d = a - b
    sig = "buy" if d > 3 else "sell" if d < -3 else "neutral"
    return {"rotation": _ind(
        _norm(d, -10, 10), f"{d:+.1f} pt / 3 mois", "", sig,
        f"Consommation cyclique (XLY) vs défensive (XLP) : {d:+.1f} pt sur 3 mois — "
        + ("les investisseurs préfèrent les secteurs de croissance : appétit pour le risque." if d > 3
           else "rotation vers les secteurs défensifs : prudence des investisseurs." if d < -3
           else "pas de rotation nette."))}


# ── Crypto ───────────────────────────────────────────────────────────────────

def _mvrv_ratio():
    r = requests.get(
        "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
        "?assets=btc&metrics=CapMVRVCur&frequency=1d&page_size=1&paging_from=end",
        timeout=TIMEOUT, headers=HEADERS)
    m = float(r.json()["data"][-1]["CapMVRVCur"])
    sig = "buy" if m < 1 else "sell" if m > 3 else "neutral"
    return {"mvrv_ratio": _ind(
        _norm(m, 0.5, 4), f"{m:.2f}", "x", sig,
        f"MVRV Bitcoin à {m:.2f} (Coin Metrics) — "
        + ("le prix est sous le coût moyen d'acquisition : zone historique d'accumulation." if m < 1
           else "marché en euphorie : sommets de cycle historiques au-dessus de 3 à 3,5." if m > 3
           else "prix au-dessus du coût moyen, sans excès."))}


def _okx_long_short():
    r = requests.get("https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio"
                     "?ccy=BTC&period=1D", timeout=TIMEOUT, headers=HEADERS)
    ratio = float(r.json()["data"][0][1])
    sig = "sell" if ratio > 2 else "buy" if ratio < 0.8 else "neutral"
    return {"ls_ratio": _ind(
        _norm(ratio, 0.5, 3), f"{ratio:.2f}", "", sig,
        f"Ratio comptes long/short BTC à {ratio:.2f} (OKX) — "
        + ("positions longues surreprésentées : risque de liquidations en cascade." if ratio > 2
           else "majorité de vendeurs à découvert : un rebond forcé est possible." if ratio < 0.8
           else "positionnement équilibré."))}


def _stablecoins():
    r = requests.get("https://stablecoins.llama.fi/stablecoincharts/all",
                     timeout=TIMEOUT + 8, headers=HEADERS)
    rows = r.json()

    def total(row):
        t = row.get("totalCirculatingUSD") or row.get("totalCirculating") or {}
        return float(t.get("peggedUSD", 0))

    if len(rows) < 32:
        return None
    now, before = total(rows[-1]), total(rows[-31])
    if not before:
        return None
    chg = (now / before - 1) * 100
    sig = "buy" if chg > 2 else "sell" if chg < -1 else "neutral"
    return {"stablecoins": _ind(
        _norm(chg, -3, 6), f"{chg:+.1f} % / 30 j", "", sig,
        f"Stablecoins en circulation : {now/1e9:.0f} Md$, {chg:+.1f} % sur 30 jours (DefiLlama) — "
        + ("de l'argent frais entre sur le marché crypto." if chg > 2
           else "des capitaux quittent l'écosystème crypto." if chg < -1
           else "offre stable, pas de flux notable."))}


# ── Points d'entrée ──────────────────────────────────────────────────────────

def bourse_extras() -> dict:
    out = {}
    for label, fn in [("courbe des taux", _yield_curve), ("spread HY", _hy_spread),
                      ("stress financier", _fin_stress), ("Sahm", _sahm),
                      ("liquidité Fed", _fed_liquidity), ("prime de risque", _erp), ("CNN F&G", _cnn_fear_greed),
                      ("VIX terme", _vix_term), ("SKEW", _skew),
                      ("largeur", _breadth), ("rotation", _rotation)]:
        _try(label, fn, out)
    return out


def crypto_extras() -> dict:
    out = {}
    for label, fn in [("MVRV", _mvrv_ratio), ("OKX long/short", _okx_long_short),
                      ("stablecoins", _stablecoins)]:
        _try(label, fn, out)
    return out


def matieres_extras() -> dict:
    out = {}
    for label, fn in [("inflation anticipée", _breakeven), ("liquidité Fed", _fed_liquidity)]:
        _try(label, fn, out)
    return out
