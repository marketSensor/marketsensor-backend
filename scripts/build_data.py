#!/usr/bin/env python3
"""
Génère les fichiers statiques publiés sur GitHub Pages (branche `data`) :
  indicators.json  analytics.json  calendar.json  history.json  signals.json   (+ state.json : état interne)

Usage (depuis la racine du dépôt) :
  python scripts/build_data.py --out out --alert-file /tmp/alert.json
"""
import argparse
import json
import math
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # indicators.py / alerts.py à la racine du dépôt
sys.path.insert(0, str(HERE))

import signal_engine as eng  # noqa: E402

MAX_CARRY_S = 24 * 3600   # un indicateur en échec garde sa dernière valeur jusqu'à 24 h
MIN_FRESH = 10            # sous ce nombre d'indicateurs frais, on ne publie rien
MIN_TAB = 5               # sous ce nombre d'indicateurs par onglet : onglet exclu des signaux et des alertes
HISTORY_DAYS = 90
HISTORY_PUBLISHED = 60    # le frontend lisait ?limit=60
CALENDAR_DAYS = 180


def _clean(o):
    """JSON strict : numpy -> python, NaN/inf -> None."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        o = o.item()
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(_clean(obj), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def merge_with_previous(fresh: dict, prev: dict, now: float):
    """fresh/prev : {tab: {id: patch}} / {tab: {id: {"p": patch, "ts": epoch}}} -> (merged, new_prev, carried)"""
    merged, new_prev, carried = {}, {}, []
    for tab in eng.TABS:
        f, p = fresh.get(tab) or {}, prev.get(tab) or {}
        merged[tab], new_prev[tab] = dict(f), {k: {"p": v, "ts": now} for k, v in f.items()}
        for ind_id, rec in p.items():
            if ind_id not in f and now - rec["ts"] < MAX_CARRY_S:
                merged[tab][ind_id] = rec["p"]
                new_prev[tab][ind_id] = rec
                carried.append(f"{tab}.{ind_id}")
    return merged, new_prev, carried


def collect_indicators(iso: str) -> dict:
    """get_all() ; s'il plante (une source en échec), chaque section est appelée séparément."""
    import indicators
    try:
        return indicators.get_all()
    except Exception as e:
        print(f"::warning::get_all() a échoué ({type(e).__name__}: {e}) — appel section par section")
    out = {"timestamp": iso}
    for tab, fn in (("bourse", "_bourse"), ("crypto", "_crypto"), ("matieres", "_matieres")):
        try:
            out[tab] = getattr(indicators, fn)()
        except Exception as e:
            print(f"::warning::section {tab} indisponible ({type(e).__name__}: {e})")
            out[tab] = {}
    return out


def calendar_events(today: date) -> list[dict]:
    events = json.loads((HERE / "macro_calendar.json").read_text(encoding="utf-8"))
    out = []
    for ev in events:
        delta = (date.fromisoformat(ev["date"]) - today).days
        if 0 <= delta <= CALENDAR_DAYS:
            out.append({**ev, "days_from_now": delta})
    return sorted(out, key=lambda e: e["date"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--alert-file", default=None)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    now_dt = datetime.now(timezone.utc)
    now = now_dt.timestamp()
    iso = now_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    state = read_json(out / "state.json", {})

    # 1. Indicateurs du backend (indicators.py, inchangé)
    import indicators
    t0 = time.time()
    fresh = collect_indicators(iso)
    n_fresh = sum(len(fresh.get(t) or {}) for t in eng.TABS)
    print(f"[build] {n_fresh} indicateurs frais en {time.time() - t0:.0f}s")
    if n_fresh < MIN_FRESH:
        print(f"::error::Moins de {MIN_FRESH} indicateurs frais : rien n'est publié, l'ancien état reste en ligne.")
        return 1
    backend, backend_state, carried = merge_with_previous(fresh, state.get("backend", {}), now)

    # 2. Sources crypto que le navigateur interrogeait seul
    browser_raw = eng.browser_signal_patches(eng.fetch_browser_sources())
    browser, browser_state, carried_b = merge_with_previous(browser_raw, state.get("browser", {}), now)
    carried += [f"browser.{c}" for c in carried_b]

    # 3. Signaux
    # Un onglet dont presque aucun indicateur n'est disponible serait calculé sur des valeurs par défaut :
    # on l'exclut (pas de signal, pas d'historique, pas d'alerte) jusqu'à son retour.
    unavailable = [t for t in eng.TABS if len(backend[t]) < MIN_TAB]
    for t in unavailable:
        print(f"::warning::onglet {t} indisponible ({len(backend[t])} indicateurs) : exclu des signaux et alertes")
    reco_all = eng.compute_reco([backend, browser])
    reco = {t: reco_all[t] for t in eng.TABS if t not in unavailable}
    prev_reco = state.get("last") or {}
    changes = [{"tab": t, "from": prev_reco[t]["sig"], "to": reco[t]["sig"]}
               for t in reco if t in prev_reco and prev_reco[t]["sig"] != reco[t]["sig"]]
    last = {**prev_reco, **reco}
    print(f"[build] signaux : { {t: reco[t]['sig'] for t in reco} }  changements : {changes or 'aucun'}")

    # 4. Historique (90 jours conservés dans state.json, 60 derniers publiés)
    cutoff = (now_dt - timedelta(days=HISTORY_DAYS)).isoformat().replace("+00:00", "Z")
    history = [h for h in state.get("history", []) if h["ts"] >= cutoff]
    history.append({"ts": iso, **reco})

    # 5. Analytics (échec = l'ancien analytics.json reste en place)
    try:
        write_json(out / "analytics.json", indicators.get_analytics())
    except Exception as e:
        print(f"::warning::analytics non mis à jour : {e}")

    # 6. Calendrier
    events = calendar_events(now_dt.date())
    if not [e for e in events if e["days_from_now"] <= 60]:
        print("::warning::Aucun événement macro dans les 60 prochains jours : mettre à jour scripts/macro_calendar.json")

    # 7. Écriture
    write_json(out / "indicators.json", {**fresh, **backend, "timestamp": fresh.get("timestamp", iso), "carried": carried})
    write_json(out / "calendar.json", {"events": events, "count": len(events), "generated": iso})
    write_json(out / "history.json", {"history": history[-HISTORY_PUBLISHED:], "total": len(history)})
    write_json(out / "signals.json", {"ts": iso, **reco, "unavailable": unavailable})
    write_json(out / "state.json", {"backend": backend_state, "browser": browser_state, "last": last, "history": history})

    if changes and args.alert_file:
        Path(args.alert_file).write_text(json.dumps({"changes": changes, "ts": iso}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
