"""
Test rétroactif des signaux : que faisait l'actif APRÈS chaque signal d'achat / de vente ?

Trois actifs : S&P 500 (indicateurs bourse), Bitcoin (indicateurs crypto), or (indicateurs matières premières).
Pour chaque indicateur reconstructible sur longue période (prix Yahoo, séries FRED, Coin Metrics, Alternative.me),
on rejoue le signal avec exactement les seuils du site, puis on mesure le rendement de l'actif sur les 3 mois
et les 12 mois suivants.

Calibration walk-forward (S&P 500) : les poids sont ré-estimés chaque année avec les seules données passées,
puis le vote qui en résulte est comparé, hors échantillon, au vote à poids fixes du site.

Limites (affichées sur le site) :
  - les observations se chevauchent : l'échantillon réel est bien plus petit que le nombre de jours ;
  - séries FRED utilisées avec leurs valeurs révisées (pas celles publiées en temps réel) ;
  - les seuils du site étaient connus d'avance mais une règle (VIX) a été corrigée après un premier test ;
  - le passé ne garantit rien.

Résultat : backtest.json (régénéré une fois par semaine, ou dès que VERSION change).
"""
import os
import warnings
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf

VERSION = 4               # à incrémenter quand les règles de signaux changent (force le recalcul)
warnings.filterwarnings("ignore")
FRED_KEY = os.getenv("FRED_API_KEY", "")
MIN_STATE_DAYS = 250      # en dessous, l'état n'est pas jugé
H_LABEL = {"3m": "3 mois", "12m": "12 mois"}

# poids du site (data.js) pour le vote composite de chaque actif
WEIGHTS = {
    "spx": {"rsi_spx": 2, "mm50": 2, "mm200": 3, "vix": 2, "skew": 1, "vix_term": 1, "breadth": 2,
            "rotation": 1, "yield_curve": 1, "hy_spread": 2, "fin_stress": 1, "sahm": 1},
    "btc": {"rsi": 2, "macd": 2, "mayer": 2, "mvrv_ratio": 3, "cfg": 3},
    "gold": {"rsi": 2, "macd": 2, "mm50": 1, "mm200": 3, "realrates": 3},
}
LABELS = {
    "spx": {"rsi_spx": "RSI S&P 500", "mm50": "S&P 500 vs MM50", "mm200": "S&P 500 vs MM200", "vix": "VIX",
            "skew": "SKEW", "vix_term": "Structure du VIX", "breadth": "Largeur de marché",
            "rotation": "Rotation cyclique/défensif", "yield_curve": "Courbe des taux",
            "hy_spread": "Écart de crédit HY", "fin_stress": "Stress financier", "sahm": "Règle de Sahm"},
    "btc": {"rsi": "RSI Bitcoin", "macd": "MACD Bitcoin", "mayer": "Mayer Multiple", "mvrv_ratio": "MVRV Bitcoin",
            "cfg": "Crypto Fear & Greed"},
    "gold": {"rsi": "RSI Or", "macd": "MACD Or", "mm50": "Or vs MM50", "mm200": "Or vs MM200",
             "realrates": "Taux réels (TIPS 10 ans)"},
}
ASSETS = {  # clé -> (libellé de la cible, horizons en séances, échelle des seuils de verdict)
    "spx": ("S&P 500 (^GSPC), hors dividendes", {"3m": 63, "12m": 252}, 1.0),
    "btc": ("Bitcoin (BTC-USD)", {"3m": 90, "12m": 365}, 5.0),
    "gold": ("Or (GC=F, futures)", {"3m": 63, "12m": 252}, 1.0),
}
# id d'indicateur du site -> (actif, clé de test), par onglet
BT_MAP = {
    "bourse": {k: ["spx", k] for k in LABELS["spx"]},
    "crypto": {"btcrsi": ["btc", "rsi"], "btcmacd": ["btc", "macd"], "mayer": ["btc", "mayer"],
               "mvrv_ratio": ["btc", "mvrv_ratio"], "cfg": ["btc", "cfg"]},
    "matieres": {"rsi_gold": ["gold", "rsi"], "macd_gold": ["gold", "macd"], "mm50_gold": ["gold", "mm50"],
                 "mm200_gold": ["gold", "mm200"], "realrates": ["gold", "realrates"]},
}


# ── Données ──────────────────────────────────────────────────────────────────

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


def _coinmetrics(metric: str) -> pd.Series:
    r = requests.get("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
                     params={"assets": "btc", "metrics": metric, "frequency": "1d",
                             "start_time": "2011-01-01", "page_size": 10000}, timeout=40)
    r.raise_for_status()
    rows = [(d["time"][:10], float(d[metric])) for d in r.json()["data"] if d.get(metric) is not None]
    return pd.Series([v for _, v in rows], index=pd.to_datetime([t for t, _ in rows]))


def _crypto_fng() -> pd.Series:
    r = requests.get("https://api.alternative.me/fng/", params={"limit": 0, "format": "json"}, timeout=40)
    r.raise_for_status()
    d = r.json()["data"]
    s = pd.Series([float(x["value"]) for x in d],
                  index=pd.to_datetime([int(x["timestamp"]) for x in d], unit="s").normalize())
    return s[~s.index.duplicated()].sort_index()


# ── Signaux ──────────────────────────────────────────────────────────────────

def _rsi(close: pd.Series, n=14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _macd_hist(close: pd.Series) -> pd.Series:
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    return macd - macd.ewm(span=9, adjust=False).mean()


def _spearman(a: pd.Series, b: pd.Series) -> float:
    """Corrélation de rang (sans scipy) : Pearson sur les rangs."""
    return a.rank().corr(b.rank())


def _sig(series: pd.Series, buy, sell) -> pd.Series:
    """buy / sell : fonctions booléennes sur la série -> +1 / -1 / 0 (NaN conservé)."""
    out = pd.Series(0.0, index=series.index)
    out[buy(series)] = 1.0
    out[sell(series)] = -1.0
    out[series.isna()] = np.nan
    return out


def _technical(close: pd.Series, mm=("mm50", "mm200")) -> dict:
    sg = {"rsi": _sig(_rsi(close), lambda s: s < 35, lambda s: s > 65),
          "macd": _sig(_macd_hist(close), lambda s: s > 0, lambda s: s <= 0)}
    for name in mm:
        n = int(name[2:])
        sg[name] = _sig(close / close.rolling(n).mean() - 1, lambda s: s > 0, lambda s: s <= 0)
    return sg


# ── Statistiques ─────────────────────────────────────────────────────────────

def _stats(sig: pd.Series, fwd: dict) -> dict:
    res = {"n": {}, "mean": {}, "hit": {}}
    for hk, f in fwd.items():
        df = pd.DataFrame({"s": sig, "f": f}).dropna()
        res["mean"][hk], res["hit"][hk] = {}, {}
        for name, v in (("buy", 1.0), ("neutral", 0.0), ("sell", -1.0)):
            x = df.loc[df["s"] == v, "f"]
            if hk == "3m":
                res["n"][name] = int(len(x))
            ok = len(x) >= MIN_STATE_DAYS
            res["mean"][hk][name] = round(float(x.mean() * 100), 2) if ok else None
            res["hit"][hk][name] = round(float(((x > 0) if v > 0 else (x < 0)).mean() * 100), 1) if ok and v != 0 else None
    return res


def _verdict(st: dict, scale: float) -> tuple[str, dict]:
    edge = {}
    for hk in H_LABEL:
        m = st["mean"][hk]
        b = m["buy"]
        s = m["sell"] if m["sell"] is not None else m["neutral"]   # peu de ventes : on compare à « neutre »
        edge[hk] = round(b - s, 2) if b is not None and s is not None else None
    e3, e12 = edge["3m"], edge["12m"]
    if e3 is None or e12 is None:
        return "insuffisant", edge
    if e3 > 1 * scale and e12 > 3 * scale:
        return "discriminant", edge
    if e3 < -1 * scale and e12 < -3 * scale:
        return "inverse", edge
    return "peu_informatif", edge


def _vote(df: pd.DataFrame, w: pd.Series, min_weight: float) -> pd.Series:
    """Même règle que le site : achat si >= 45 % du poids disponible est acheteur, vente si >= 35 %."""
    w = w.reindex(df.columns).fillna(0)
    avail = df.notna().mul(w).sum(axis=1)
    buy_p = df.eq(1.0).mul(w).sum(axis=1) / avail.replace(0, np.nan) * 100
    sell_p = df.eq(-1.0).mul(w).sum(axis=1) / avail.replace(0, np.nan) * 100
    out = pd.Series(0.0, index=df.index)
    out[sell_p >= 35] = -1.0
    out[buy_p >= 45] = 1.0
    out[avail < min_weight] = np.nan      # trop peu d'indicateurs disponibles à cette date
    return out


def _evaluate(asset: str, close: pd.Series, sigs: dict) -> dict:
    target, hdays, scale = ASSETS[asset]
    fwd = {hk: close.shift(-d) / close - 1 for hk, d in hdays.items()}
    ind = {}
    for name, sg in sigs.items():
        valid = sg.dropna()
        if valid.empty:
            continue
        st = _stats(sg, fwd)
        verdict, edge = _verdict(st, scale)
        ind[name] = {"label": LABELS[asset][name], "since": valid.index[0].strftime("%Y-%m"),
                     "verdict": verdict, "edge": edge, **st}
    df = pd.DataFrame({k: v for k, v in sigs.items() if k in WEIGHTS[asset]})
    w = pd.Series(WEIGHTS[asset])
    comp = _vote(df, w, min_weight=0.5 * w.reindex(df.columns).sum())
    cst = _stats(comp, fwd)
    cverdict, cedge = _verdict(cst, scale)
    return {"label": target, "since": close.index[0].strftime("%Y-%m"), "horizon_days": hdays,
            "baseline": {hk: round(float(f.dropna().mean() * 100), 2) for hk, f in fwd.items()},
            "indicators": ind,
            "composite": {"label": "Vote pondéré des indicateurs testables", "verdict": cverdict,
                          "edge": cedge, **cst}}


# ── Calibration walk-forward (S&P 500) ───────────────────────────────────────

def _calibrate(sigs: dict, close: pd.Series) -> dict:
    """Chaque année Y, poids = corrélation de rang (signal, rendement futur 3 mois) mesurée AVANT Y
    (moins 63 séances pour éviter tout recouvrement), partie positive seulement ; le vote de l'année Y
    utilise ces poids. Comparé au vote à poids fixes du site, sur la même période hors échantillon."""
    fwd3 = close.shift(-63) / close - 1
    fwd12 = close.shift(-252) / close - 1
    df = pd.DataFrame({k: v for k, v in sigs.items() if k in WEIGHTS["spx"]})
    static_w = pd.Series(WEIGHTS["spx"], dtype=float)
    min_w = 0.5 * static_w.sum()
    cal = pd.Series(np.nan, index=df.index)
    first_oos = 2000
    ics_last = {}
    for year in range(first_oos, df.index[-1].year + 1):
        cut = pd.Timestamp(year=year, month=1, day=1)
        train = df.index < (cut - pd.Timedelta(days=95))
        w = {}
        for c in df.columns:
            x = pd.DataFrame({"s": df.loc[train, c], "f": fwd3[train]}).dropna()
            ic = _spearman(x["s"], x["f"]) if len(x) >= 750 else np.nan
            w[c] = max(0.0, ic) if ic == ic else static_w[c] * 0.0
            ics_last[c] = None if ic != ic else round(float(ic), 3)
        wser = pd.Series(w)
        if wser.sum() <= 0:
            continue
        mask = (df.index >= cut) & (df.index < cut + pd.DateOffset(years=1))
        cal[mask] = _vote(df[mask], wser, min_weight=0.5 * wser.sum())
    oos = df.index >= pd.Timestamp(year=first_oos, month=1, day=1)
    static = _vote(df, static_w, min_w)
    static[~oos] = np.nan
    cal[~oos] = np.nan
    fwd = {"3m": fwd3, "12m": fwd12}
    out = {}
    for name, sg in (("poids_du_site", static), ("poids_calibres", cal)):
        st = _stats(sg, fwd)
        _, edge = _verdict(st, 1.0)
        out[name] = {"edge": edge, "mean": st["mean"], "hit": st["hit"], "n": st["n"]}
    # poids suggérés = corrélation sur l'ensemble de la période, ramenée à 0-3 (information seulement)
    full = {}
    for c in df.columns:
        x = pd.DataFrame({"s": df[c], "f": fwd3}).dropna()
        full[c] = round(float(_spearman(x["s"], x["f"])), 3) if len(x) >= 750 else None
    pos = [v for v in full.values() if v is not None and v > 0]
    top = max(pos) if pos else 0
    suggested = {c: (int(round(3 * max(0, v) / top)) if v is not None and top else None) for c, v in full.items()}
    e_s, e_c = out["poids_du_site"]["edge"], out["poids_calibres"]["edge"]
    better = None
    if None not in (e_s["3m"], e_s["12m"], e_c["3m"], e_c["12m"]):
        better = bool(e_c["12m"] > e_s["12m"] + 1 and e_c["3m"] >= e_s["3m"])
    return {"methode": "Poids réestimés chaque année avec les seules données antérieures (corrélation de rang signal / "
                       "rendement à 3 mois), comparés aux poids fixes du site, de 2000 à aujourd'hui.",
            "hors_echantillon_depuis": first_oos, **out,
            "correlation_rang_sur_toute_la_periode": full, "poids_suggeres_0_a_3": suggested,
            "poids_actuels_du_site": WEIGHTS["spx"], "calibration_meilleure_hors_echantillon": better}


# ── Orchestration ────────────────────────────────────────────────────────────

def run() -> dict:
    t0 = time.time()
    assets = {}

    def attempt(label, fn, store=None):
        try:
            return fn()
        except Exception as e:
            print(f"[backtest] {label}: {type(e).__name__}: {e}")
            return None

    # S&P 500
    spx = _close("^GSPC")
    spx = spx[spx.index >= "1990-01-01"]
    idx = spx.index

    def al(s):  # aligne sur les séances du S&P 500 (valeur connue la plus récente)
        return s.reindex(idx.union(s.index)).ffill().reindex(idx)

    sg = {"rsi_spx": _sig(_rsi(spx), lambda s: s < 35, lambda s: s > 65),
          "mm50": _sig(spx / spx.rolling(50).mean() - 1, lambda s: s > 0, lambda s: s <= 0),
          "mm200": _sig(spx / spx.rolling(200).mean() - 1, lambda s: s > 0, lambda s: s <= 0)}

    def put(name, fn):
        r = attempt(name, fn)
        if r is not None:
            sg[name] = r
    put("vix", lambda: _sig(al(_close("^VIX")), lambda s: s > 30, lambda s: s < 0))   # VIX > 30 = achat contrariant
    put("skew", lambda: _sig(al(_close("^SKEW")), lambda s: s < 120, lambda s: s > 150))
    put("vix_term", lambda: _sig(al(_close("^VIX")) / al(_close("^VIX3M")), lambda s: s < 0.9, lambda s: s > 1.05))

    def rel(a, b):
        pa, pb = al(_close(a)), al(_close(b))
        return (pa / pa.shift(63) - 1 - (pb / pb.shift(63) - 1)) * 100
    put("breadth", lambda: _sig(rel("RSP", "SPY"), lambda s: s > 0, lambda s: s < -3))
    put("rotation", lambda: _sig(rel("XLY", "XLP"), lambda s: s > 3, lambda s: s < -3))
    if FRED_KEY:
        put("yield_curve", lambda: _sig(al(_fred("T10Y3M")), lambda s: s >= 0.5, lambda s: s < 0))
        put("hy_spread", lambda: _sig(al(_fred("BAMLH0A0HYM2")), lambda s: s < 4, lambda s: s > 5.5))
        put("fin_stress", lambda: _sig(al(_fred("STLFSI4")), lambda s: s < 0, lambda s: s > 1.5))
        put("sahm", lambda: _sig(al(_fred("SAHMREALTIME")), lambda s: s < 0.3, lambda s: s >= 0.5))
    assets["spx"] = _evaluate("spx", spx, sg)
    calibration = attempt("calibration", lambda: _calibrate(sg, spx))

    # Bitcoin
    btc = attempt("BTC prix", lambda: _close("BTC-USD"))
    if btc is not None and len(btc) > 800:
        bi = btc.index
        bs = {"rsi": _technical(btc, mm=())["rsi"], "macd": _technical(btc, mm=())["macd"],
              "mayer": _sig(btc / btc.rolling(200).mean(), lambda s: s < 0.8, lambda s: s > 2.4)}

        def balign(s):
            return s.reindex(bi.union(s.index)).ffill().reindex(bi)
        r = attempt("MVRV", lambda: balign(_coinmetrics("CapMVRVCur")))
        if r is not None:
            bs["mvrv_ratio"] = _sig(r, lambda s: s < 1, lambda s: s > 3)
        r = attempt("Crypto F&G", lambda: balign(_crypto_fng()))
        if r is not None:
            bs["cfg"] = _sig(r, lambda s: s < 30, lambda s: s > 70)
        assets["btc"] = _evaluate("btc", btc, bs)

    # Or
    gold = attempt("Or prix", lambda: _close("GC=F"))
    if gold is not None and len(gold) > 1500:
        gs = _technical(gold)
        if FRED_KEY:
            r = attempt("taux réels", lambda: _fred("DFII10", "2003-01-01"))
            if r is not None:
                gi = gold.index
                r = r.reindex(gi.union(r.index)).ffill().reindex(gi)
                gs["realrates"] = _sig(r, lambda s: s < 0.5, lambda s: s > 2)
        assets["gold"] = _evaluate("gold", gold, gs)

    print(f"[backtest] {sum(len(a['indicators']) for a in assets.values())} indicateurs testés "
          f"sur {len(assets)} actifs en {time.time() - t0:.0f}s")
    return {
        "version": VERSION,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "horizons": H_LABEL,
        "assets": assets,
        "map": BT_MAP,
        "calibration": calibration,
        "caveat": ("Les observations se chevauchent, les séries sont révisées et une règle (VIX) a été corrigée après un "
                   "premier test : les résultats sont indicatifs. Le test mesure si un signal a eu un sens par le passé, "
                   "pas s'il en aura un demain."),
    }


def reliability(bt: dict, tab: str, ind_id: str):
    """Verdict de fiabilité passée d'un indicateur du site, ou None s'il n'est pas testé."""
    try:
        asset, key = ((bt or {}).get("map") or BT_MAP)[tab][ind_id]
        return bt["assets"][asset]["indicators"][key]["verdict"]
    except Exception:
        return None
