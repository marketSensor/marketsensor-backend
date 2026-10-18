"""
Test rétroactif des signaux : que faisait le S&P 500 APRÈS chaque signal d'achat / de vente ?

Pour chaque indicateur reconstructible sur longue période (prix Yahoo, séries FRED), on rejoue le signal
avec exactement les mêmes seuils que le site, puis on mesure le rendement du S&P 500 sur les 3 mois (63 séances)
et les 12 mois (252 séances) suivants.

Limites (affichées sur le site) :
  - test « dans l'échantillon » : les seuils n'ont pas été optimisés ici, mais ils sont connus d'avance ;
  - les observations se chevauchent : l'échantillon réel est bien plus petit que le nombre de jours ;
  - séries FRED utilisées avec leurs valeurs révisées (pas celles publiées en temps réel) ;
  - le passé ne garantit rien, et les indicateurs crypto / matières premières ne sont pas testés ici.

Résultat : backtest.json (régénéré une fois par semaine).
"""
import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf

FRED_KEY = os.getenv("FRED_API_KEY", "")
HORIZONS = (63, 252)
MIN_STATE_DAYS = 250        # en dessous, l'état n'est pas jugé

# poids du site (data.js) pour le vote composite
WEIGHTS = {"rsi_spx": 2, "mm50": 2, "mm200": 3, "vix": 2, "skew": 1, "vix_term": 1, "breadth": 2,
           "rotation": 1, "yield_curve": 1, "hy_spread": 2, "fin_stress": 1, "sahm": 1}

LABELS = {"rsi_spx": "RSI S&P 500", "mm50": "S&P 500 vs MM50", "mm200": "S&P 500 vs MM200", "vix": "VIX",
          "skew": "SKEW", "vix_term": "Structure du VIX", "breadth": "Largeur de marché",
          "rotation": "Rotation cyclique/défensif", "yield_curve": "Courbe des taux", "hy_spread": "Écart de crédit HY",
          "fin_stress": "Stress financier", "sahm": "Règle de Sahm"}


def _close(ticker: str) -> pd.Series:
    h = yf.Ticker(ticker).history(period="max", auto_adjust=True)["Close"].dropna()
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    return h[~h.index.duplicated()]


def _fred(series: str, start="1990-01-01") -> pd.Series:
    r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                     params={"series_id": series, "api_key": FRED_KEY, "file_type": "json",
                             "observation_start": start}, timeout=30)
    r.raise_for_status()
    rows = [(o["date"], float(o["value"])) for o in r.json()["observations"] if o["value"] != "."]
    return pd.Series([v for _, v in rows], index=pd.to_datetime([d for d, _ in rows]))


def _rsi(close: pd.Series, n=14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _sig(series: pd.Series, buy, sell) -> pd.Series:
    """buy / sell : fonctions booléennes sur la série -> +1 / -1 / 0 (NaN conservé)."""
    out = pd.Series(0.0, index=series.index)
    out[buy(series)] = 1.0
    out[sell(series)] = -1.0
    out[series.isna()] = np.nan
    return out


def _stats(sig: pd.Series, fwd: dict) -> dict:
    res = {"n": {}, "mean": {}, "hit": {}}
    for h, f in fwd.items():
        df = pd.DataFrame({"s": sig, "f": f}).dropna()
        res["mean"][str(h)], res["hit"][str(h)] = {}, {}
        for name, v in (("buy", 1.0), ("neutral", 0.0), ("sell", -1.0)):
            x = df.loc[df["s"] == v, "f"]
            if h == HORIZONS[0]:
                res["n"][name] = int(len(x))
            res["mean"][str(h)][name] = round(float(x.mean() * 100), 2) if len(x) >= MIN_STATE_DAYS else None
            if len(x) >= MIN_STATE_DAYS and v != 0:
                res["hit"][str(h)][name] = round(float(((x > 0) if v > 0 else (x < 0)).mean() * 100), 1)
            else:
                res["hit"][str(h)][name] = None
    return res


def _verdict(st: dict) -> tuple[str, dict]:
    edge = {}
    for h in HORIZONS:
        m = st["mean"][str(h)]
        b = m["buy"]
        s = m["sell"] if m["sell"] is not None else m["neutral"]   # pas assez de signaux de vente : on compare à « neutre »
        edge[str(h)] = round(b - s, 2) if b is not None and s is not None else None
    e63, e252 = edge[str(HORIZONS[0])], edge[str(HORIZONS[1])]
    if e63 is None or e252 is None:
        return "insuffisant", edge
    if e63 > 1 and e252 > 3:
        return "discriminant", edge
    if e63 < -1 and e252 < -3:
        return "inverse", edge
    return "peu_informatif", edge


def run() -> dict:
    t0 = time.time()
    spx = _close("^GSPC")
    spx = spx[spx.index >= "1990-01-01"]
    fwd = {h: spx.shift(-h) / spx - 1 for h in HORIZONS}
    idx = spx.index

    def al(s):  # aligne sur les séances du S&P 500 (valeur connue la plus récente)
        return s.reindex(idx.union(s.index)).ffill().reindex(idx)

    sigs, since = {}, {}

    sigs["rsi_spx"] = _sig(_rsi(spx), lambda s: s < 35, lambda s: s > 65)
    sigs["mm50"] = _sig(spx / spx.rolling(50).mean() - 1, lambda s: s > 0, lambda s: s <= 0)
    sigs["mm200"] = _sig(spx / spx.rolling(200).mean() - 1, lambda s: s > 0, lambda s: s <= 0)

    def attempt(name, fn):
        try:
            sigs[name] = fn()
        except Exception as e:
            print(f"[backtest] {name}: {e}")

    attempt("vix", lambda: _sig(al(_close("^VIX")), lambda s: s > 30, lambda s: s < 0))   # règle du site : VIX > 30 = achat contrariant
    attempt("skew", lambda: _sig(al(_close("^SKEW")), lambda s: s < 120, lambda s: s > 150))
    attempt("vix_term", lambda: _sig(al(_close("^VIX")) / al(_close("^VIX3M")),
                                     lambda s: s < 0.9, lambda s: s > 1.05))

    def rel(a, b):
        pa, pb = al(_close(a)), al(_close(b))
        return (pa / pa.shift(63) - 1 - (pb / pb.shift(63) - 1)) * 100
    attempt("breadth", lambda: _sig(rel("RSP", "SPY"), lambda s: s > 0, lambda s: s < -3))
    attempt("rotation", lambda: _sig(rel("XLY", "XLP"), lambda s: s > 3, lambda s: s < -3))

    if FRED_KEY:
        attempt("yield_curve", lambda: _sig(al(_fred("T10Y3M")), lambda s: s >= 0.5, lambda s: s < 0))
        attempt("hy_spread", lambda: _sig(al(_fred("BAMLH0A0HYM2")), lambda s: s < 4, lambda s: s > 5.5))
        attempt("fin_stress", lambda: _sig(al(_fred("STLFSI4")), lambda s: s < 0, lambda s: s > 1.5))
        attempt("sahm", lambda: _sig(al(_fred("SAHMREALTIME")), lambda s: s < 0.3, lambda s: s >= 0.5))

    out_ind = {}
    for name, sg in sigs.items():
        valid = sg.dropna()
        if valid.empty:
            continue
        st = _stats(sg, fwd)
        verdict, edge = _verdict(st)
        out_ind[name] = {"label": LABELS[name], "since": valid.index[0].strftime("%Y-%m"),
                         "verdict": verdict, "edge": edge, **st}

    # vote composite (mêmes règles que le site : achat si >=45 % du poids, vente si >=35 %)
    df = pd.DataFrame({k: v for k, v in sigs.items() if k in WEIGHTS})
    w = pd.Series({k: WEIGHTS[k] for k in df.columns})
    avail = df.notna().mul(w).sum(axis=1)
    buy_p = (df.eq(1.0).mul(w).sum(axis=1) / avail.replace(0, np.nan)) * 100
    sell_p = (df.eq(-1.0).mul(w).sum(axis=1) / avail.replace(0, np.nan)) * 100
    comp = pd.Series(0.0, index=df.index)
    comp[sell_p >= 35] = -1.0
    comp[buy_p >= 45] = 1.0
    comp[avail < 6] = np.nan          # trop peu d'indicateurs disponibles à cette date
    cst = _stats(comp, fwd)
    cverdict, cedge = _verdict(cst)

    base = {str(h): round(float(fwd[h].dropna().mean() * 100), 2) for h in HORIZONS}
    print(f"[backtest] {len(out_ind)} indicateurs testés en {time.time() - t0:.0f}s")
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "target": "S&P 500 (^GSPC), rendement hors dividendes",
        "horizons": {"63": "3 mois", "252": "12 mois"},
        "baseline": base,
        "indicators": out_ind,
        "composite": {"label": "Vote pondéré des indicateurs testables", "verdict": cverdict, "edge": cedge, **cst},
        "caveat": ("Test dans l'échantillon, observations qui se chevauchent, séries révisées. "
                   "Il mesure si un signal a eu un sens par le passé, pas s'il en aura un demain."),
    }
