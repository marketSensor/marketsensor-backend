"""
Suivi des VRAIS résultats du site : chaque jour, on enregistre le verdict publié et le prix de l'actif,
puis, une fois l'horizon écoulé (1, 3, 6, 12 mois), on mesure ce que l'actif a fait depuis.

  bourse -> S&P 500 (^GSPC)   crypto -> Bitcoin (BTC-USD)   matières premières -> or (GC=F)

Contrairement au test rétroactif, il n'y a ici aucun biais de réglage : ce sont les signaux réellement
publiés, jugés à terme. Il faut des mois pour qu'il devienne significatif ; le site l'indique.
Résultat : trackrecord.json (snapshots + résumé).
"""
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

TAB_ASSET = {"bourse": ("^GSPC", "S&P 500"), "crypto": ("BTC-USD", "Bitcoin"), "matieres": ("GC=F", "Or")}
HORIZONS = {"1m": 30, "3m": 90, "6m": 180, "12m": 365}
MIN_SIGNIFICANT = 30          # nombre de signaux évalués en dessous duquel le suivi est jugé trop mince
MAX_SNAPSHOTS = 3000


def _series(ticker: str) -> pd.Series:
    h = yf.Ticker(ticker).history(period="5y", auto_adjust=True)["Close"].dropna()
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    return h[~h.index.duplicated()]


def _price_on_or_before(s: pd.Series, d: date):
    x = s[s.index <= pd.Timestamp(d)]
    return float(x.iloc[-1]) if len(x) and (pd.Timestamp(d) - x.index[-1]).days <= 5 else None


def _price_on_or_after(s: pd.Series, d: date):
    x = s[s.index >= pd.Timestamp(d)]
    return float(x.iloc[0]) if len(x) and (x.index[0] - pd.Timestamp(d)).days <= 5 else None


def _summary(snaps: list, series: dict) -> dict:
    last_dates = {t: series[t].index[-1].date() for t in series if len(series[t])}
    tabs = {}
    evaluated = {h: 0 for h in HORIZONS}
    for tab in TAB_ASSET:
        if tab not in series:
            continue
        views = {"global": {}, "court": {}, "long": {}}
        for view, key in (("global", "v"), ("court", "c"), ("long", "l")):
            for hk, days in HORIZONS.items():
                buckets = {"buy": [], "neutral": [], "sell": []}
                for s in snaps:
                    sig = (s["sig"].get(tab) or {}).get(key)
                    p0 = (s.get("price") or {}).get(tab)
                    if sig not in buckets or not p0:
                        continue
                    target = datetime.fromisoformat(s["date"]).date() + timedelta(days=days)
                    if target > last_dates[tab]:
                        continue
                    p1 = _price_on_or_after(series[tab], target)
                    if p1:
                        buckets[sig].append(p1 / p0 - 1)
                cell = {}
                for sig, rets in buckets.items():
                    n = len(rets)
                    cell[sig] = {"n": n,
                                 "mean_pct": round(sum(rets) / n * 100, 2) if n else None,
                                 "hit_pct": (round(sum(1 for r in rets if (r > 0 if sig == "buy" else r < 0)) / n * 100, 1)
                                             if n and sig != "neutral" else None)}
                views[view][hk] = cell
                if view == "global":
                    evaluated[hk] += sum(c["n"] for c in cell.values())
        tabs[tab] = views
    first = snaps[0]["date"] if snaps else None
    days_back = (date.today() - datetime.fromisoformat(first).date()).days if first else 0
    return {"depuis": first, "jours_de_recul": days_back, "snapshots": len(snaps),
            "signaux_evalues": evaluated, "assets": {t: v[1] for t, v in TAB_ASSET.items()},
            "seuil_significatif": MIN_SIGNIFICANT,
            "significatif": any(n >= MIN_SIGNIFICANT for n in evaluated.values()),
            "tabs": tabs}


def update(out, write_json, read_json, iso, reco, history):
    path = out / "trackrecord.json"
    data = read_json(path, {"snapshots": []})
    snaps = data.get("snapshots", [])
    series = {}
    for tab, (tk, _) in TAB_ASSET.items():
        try:
            series[tab] = _series(tk)
        except Exception as e:
            print(f"[suivi] {tk}: {e}")
    if not series:
        print("[suivi] aucun prix disponible : suivi non mis à jour")
        return

    def prices_on(d):
        return {t: _price_on_or_before(s, d) for t, s in series.items()}

    if not snaps:     # première exécution : on reprend l'historique des verdicts déjà publiés (1 par jour)
        seen = set()
        for h in history or []:
            d = h["ts"][:10]
            if d in seen:
                continue
            seen.add(d)
            snaps.append({"date": d, "price": prices_on(datetime.fromisoformat(d).date()),
                          "sig": {t: {"v": h[t]["sig"]} for t in TAB_ASSET if h.get(t)}})
    today = iso[:10]
    if not snaps or snaps[-1]["date"] != today:
        snaps.append({"date": today, "price": prices_on(datetime.fromisoformat(today).date()),
                      "sig": {t: {"v": r["sig"], "c": r["horizons"]["c"]["sig"], "l": r["horizons"]["l"]["sig"]}
                              for t, r in reco.items()}})
    snaps = snaps[-MAX_SNAPSHOTS:]
    resume = _summary(snaps, series)
    write_json(path, {"snapshots": snaps, "resume": resume})
    print(f"[suivi] {len(snaps)} jours enregistrés, signaux évalués : {resume['signaux_evalues']}")
