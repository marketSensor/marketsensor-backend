"""
Assistant analyste : transforme tous les indicateurs en un « dossier » compact, puis en analyse rédigée.

Deux niveaux, publiés dans la branche `data` :
  dossier.json   toutes les données utiles à l'analyse (aussi utilisé par le chat du site)
  analysis.json  l'analyse par onglet (décision, confiance, arguments, risques, déclencheurs, plan)

  - sans clé : analyse « à règles » déterministe (gratuite, toujours disponible, source = "regles")
  - avec le secret ANTHROPIC_API_KEY : analyse rédigée par Claude (source = "llm"), régénérée seulement
    si un signal a changé ou si la dernière date de plus de ANALYST_MIN_HOURS heures (coût maîtrisé).
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

import signal_engine as eng
from backtest import reliability

DEFAULTS = json.loads(Path(__file__).with_name("indicator_defaults.json").read_text(encoding="utf-8"))
TAB_LABEL = {"bourse": "Bourse (actions)", "crypto": "Crypto", "matieres": "Matières premières"}
MIN_HOURS = float(os.getenv("ANALYST_MIN_HOURS", "6"))
MODELS = [m for m in (os.getenv("ANALYST_MODEL", "").strip() or "claude-opus-5-5", "claude-sonnet-5-5") if m]

FR = {"buy": "achat", "sell": "vente", "neutral": "neutre"}
DECISIONS = {"acheter", "renforcer_progressivement", "attendre", "alleger", "vendre"}
DECISION_LABEL = {"acheter": "Acheter", "renforcer_progressivement": "Renforcer progressivement",
                  "attendre": "Attendre", "alleger": "Alléger", "vendre": "Vendre"}

SYSTEM_PROMPT = """Tu es l'analyste en chef d'une table de gestion : trader et investisseur chevronné, formé à la gestion du risque avant la recherche du rendement. Tu analyses le tableau de bord MarketSense (indicateurs bourse, crypto, matières premières) pour aider UNE personne à décider quand acheter et quand vendre.

Principes (non négociables) :
1. Tu t'appuies uniquement sur le dossier fourni. Tu n'inventes aucune donnée, aucun prix, aucune actualité. Ce qui manque, tu le dis.
2. Tu hiérarchises. Sur ~140 indicateurs, tu identifies les 5 à 8 qui comptent vraiment aujourd'hui (poids élevé, fiabilité testée, valeur extrême) et tu laisses le bruit de côté.
3. Tu pondères par la fiabilité passée : le champ `fiabilite_passee` vient d'un test sur 36 ans (S&P 500). « discriminant » = a eu du sens ; « peu_informatif » = pas d'avantage net ; « inverse » = trompeur. Un indicateur non testé n'est ni validé ni invalidé.
4. Tu sépares court terme (momentum, sentiment) et long terme (tendance de fond, valorisation, macro). Quand ils divergent, tu l'écris clairement et tu en tires une conséquence pratique (ex. étaler les achats).
5. Tu traques les contradictions, les indicateurs corrélés qui se répètent (crédit, stress, courbe des taux), les données anciennes (`date_donnee`, `valeur_conservee`) et les indicateurs non actualisés.
6. Tu parles en probabilités et en scénarios, jamais en certitudes. Tu indiques ce qui te ferait changer d'avis (déclencheurs chiffrés quand le dossier le permet).
7. Gestion du risque : tu proposes des principes de taille de position et d'étalement (ex. achats en 3 à 4 fois, alléger par paliers), jamais de levier, jamais de « tout investir ». Tu rappelles que l'analyse est générale et ne tient pas compte de la situation personnelle.
8. Tu es direct et concret. Pas de jargon inutile, pas de remplissage, pas de phrases d'introduction. Français.

Réponds par UN SEUL objet JSON valide (sans texte autour, sans balises), de forme :
{
 "synthese_globale": "3 à 5 phrases : ce que disent les marchés ensemble, le point qui compte le plus",
 "marches": {
  "bourse|crypto|matieres": {
   "decision": "acheter | renforcer_progressivement | attendre | alleger | vendre",
   "confiance": "faible | moderee | forte",
   "resume": "3 à 5 phrases, la thèse",
   "arguments_pour": ["3 à 5 puces, chacune cite l'indicateur et sa valeur"],
   "arguments_contre": ["2 à 4 puces"],
   "risques": ["2 à 4 puces"],
   "declencheurs": ["2 à 4 conditions précises qui feraient changer la décision"],
   "plan_action": "2 à 4 phrases concrètes (étalement, paliers, ce qu'on surveille)",
   "indicateurs_cles": ["ids des 5 à 8 indicateurs décisifs"]
  }
 },
 "qualite_des_donnees": "2 à 3 phrases : couverture, données anciennes ou manquantes, limites",
 "avertissement": "1 phrase"
}
Les trois clés bourse, crypto, matieres sont obligatoires."""


PERSONA = SYSTEM_PROMPT.split("Réponds par UN SEUL objet JSON")[0].rstrip()
CHAT_PROMPT = (PERSONA + "\n\nTu es en conversation avec cette personne dans l'application. Réponds en texte clair et court "
               "(250 mots maximum sauf demande contraire), sans JSON, avec des puces si utile. Appuie chaque affirmation sur un "
               "indicateur du dossier (nom et valeur). Si la question sort du dossier (actualité, prix d'un titre précis), dis-le "
               "et ramène la réponse à ce que les indicateurs permettent de dire. Tu peux t'appuyer sur `analyse_du_jour` "
               "(ta dernière analyse) tout en la corrigeant si l'utilisateur apporte un élément nouveau.")


# ── Dossier ──────────────────────────────────────────────────────────────────

def _merged(backend: dict, browser: dict, tab: str) -> dict:
    out = {}
    for src in (backend.get(tab) or {}, browser.get(tab) or {}):
        for k, v in src.items():
            out[k] = {**out.get(k, {}), **v}
    return out


def build_dossier(iso, backend, browser, reco, history, events, backtest, analytics, carried, suivi=None) -> dict:
    carried_set = set(carried or [])
    marches = {}
    for tab in eng.TABS:
        patches = _merged(backend, browser, tab)
        inds, stale = [], []
        total = 0
        for g in DEFAULTS[tab]:
            for d in g["indicators"]:
                total += 1
                p = patches.get(d["id"])
                if p is None or d["source"] == "simulated" and "sig" not in p:
                    stale.append(d["name"])
                    continue
                bt = reliability(backtest, tab, d["id"])
                row = {"id": d["id"], "nom": d["name"], "groupe": g["name"],
                       "valeur": f"{p.get('raw', '')}{p.get('unit', '') or ''}".strip(),
                       "signal": {"buy": "achat", "sell": "vente", "neutral": "neutre"}[p.get("sig", d["sig"])],
                       "poids": d["w"], "horizon": "court" if d.get("h") == "c" else "long"}
                if bt:
                    row["fiabilite_passee"] = bt
                if p.get("asof"):
                    row["date_donnee"] = p["asof"]
                if f"{tab}.{d['id']}" in carried_set:
                    row["valeur_conservee"] = True
                inds.append(row)
        r = reco.get(tab)
        if not r:
            marches[tab] = {"indisponible": True}
            continue
        hz = {("court" if k == "c" else "long"): {"verdict": v["sig"], "achat_pct": v["bp"], "vente_pct": v["sp"]}
              for k, v in r["horizons"].items()}
        marches[tab] = {
            "libelle": TAB_LABEL[tab],
            "verdict": r["sig"], "achat_pct": r["bp"], "vente_pct": r["sp"], "horizons": hz,
            "couverture": {"indicateurs_actifs": len(inds), "indicateurs_total": total,
                           "non_actualises": stale},
            "historique_verdicts": [{"ts": h["ts"][:16], "verdict": h[tab]["sig"], "achat_pct": h[tab]["bp"]}
                                    for h in (history or [])[-12:] if h.get(tab)],
            "indicateurs": sorted(inds, key=lambda x: -x["poids"]),
        }
    ev = [{"date": e.get("date"), "evenement": e.get("title") or e.get("name") or e.get("event"),
           "dans_jours": e.get("days_from_now"), "importance": e.get("impact") or e.get("importance")}
          for e in (events or []) if e.get("days_from_now", 99) <= 45][:14]
    divs = [d.get("desc") for d in ((analytics or {}).get("divergences") or [])][:8]
    tests = {}
    for key, a_ in ((backtest or {}).get("assets") or {}).items():
        c = a_.get("composite") or {}
        tests[key] = {"cible": a_.get("label"), "depuis": a_.get("since"),
                      "rendement_moyen_pct": a_.get("baseline"),
                      "vote_composite": {"verdict": c.get("verdict"), "ecart_achat_moins_vente_pct": c.get("edge")}}
    cal = (backtest or {}).get("calibration") or {}
    return {
        "genere_le": iso,
        "regle_de_decision": "Vote pondéré des indicateurs actualisés : achat si ≥ 45 % du poids est acheteur, "
                             "vente si ≥ 35 % est vendeur, sinon attendre. Court terme = momentum/sentiment, long terme = tendance/valorisation/macro.",
        "marches": marches,
        "calendrier_macro": ev,
        "divergences_detectees": divs,
        "test_retroactif": {"actifs_testes": tests, "limites": (backtest or {}).get("caveat"),
                            "calibration_hors_echantillon_meilleure": cal.get("calibration_meilleure_hors_echantillon")},
        "suivi_reel_du_site": suivi,
        "consignes_chat": CHAT_PROMPT,
    }


# ── Analyse à règles (toujours disponible) ───────────────────────────────────

def _decision(r_all, r_c, r_l):
    s, c, l = r_all["verdict"], r_c["verdict"], r_l["verdict"]
    if l == "buy" and c == "sell":
        return "renforcer_progressivement", "Tendance de fond favorable mais court terme sous tension : acheter par étapes."
    if s == "buy" or (l == "buy" and c != "sell"):
        return ("acheter" if s == "buy" and c != "sell" else "renforcer_progressivement"), "Les signaux sont majoritairement favorables."
    if s == "sell" and l == "sell":
        return "vendre", "Les signaux de fond et de court terme sont défavorables."
    if s == "sell" or l == "sell":
        return "alleger", "Prudence : le signal de fond ou global est défavorable."
    return "attendre", "Signaux partagés : pas d'avantage net."


def rules_analysis(dossier: dict) -> dict:
    out = {}
    notes = []
    for tab, m in dossier["marches"].items():
        if m.get("indisponible"):
            continue
        hz = m["horizons"]
        r_all = {"verdict": m["verdict"], "bp": m["achat_pct"], "sp": m["vente_pct"]}
        dec, why = _decision(r_all, hz["court"], hz["long"])
        inds = m["indicateurs"]
        rel = {"discriminant": 3, "peu_informatif": 1, "insuffisant": 0.5, "inverse": -1}
        score = lambda i: i["poids"] * (1 + rel.get(i.get("fiabilite_passee"), 1) * 0.5)
        pros = sorted([i for i in inds if i["signal"] == "achat"], key=score, reverse=True)[:5]
        cons = sorted([i for i in inds if i["signal"] == "vente"], key=score, reverse=True)[:4]
        fmt = lambda i: f"{i['nom']} : {i['valeur']}"
        cov = m["couverture"]
        coverage = cov["indicateurs_actifs"] / max(1, cov["indicateurs_total"])
        spread = max(m["achat_pct"], m["vente_pct"])
        conf = "forte" if spread >= 60 and coverage >= .8 else "moderee" if spread >= 40 and coverage >= .6 else "faible"
        if hz["court"]["verdict"] != hz["long"]["verdict"] and conf == "forte":
            conf = "moderee"
        need_buy = max(0, 45 - m["achat_pct"]); need_sell = max(0, 35 - m["vente_pct"])
        trig = []
        if m["verdict"] != "buy":
            trig.append(f"Passage à « acheter » : il manque {need_buy} points de poids acheteur (45 % requis, {m['achat_pct']} % aujourd'hui).")
        if m["verdict"] != "sell":
            trig.append(f"Passage à « vendre » : il manque {need_sell} points de poids vendeur (35 % requis, {m['vente_pct']} % aujourd'hui).")
        if hz["court"]["verdict"] != hz["long"]["verdict"]:
            trig.append("Convergence du court terme vers le long terme : c'est elle qui confirmerait la direction.")
        risks = []
        if cov["non_actualises"]:
            risks.append(f"{len(cov['non_actualises'])} indicateur(s) non actualisé(s), donc exclus du vote.")
        old = [i for i in inds if i.get("valeur_conservee")]
        if old:
            risks.append(f"{len(old)} valeur(s) conservée(s) depuis plus d'une heure (source indisponible).")
        for i in cons[:2]:
            risks.append(f"Alerte : {fmt(i)} ({i['signal']}).")
        key = [i["id"] for i in (pros[:4] + cons[:3])]
        out[tab] = {
            "decision": dec, "confiance": conf,
            "resume": (f"{why} Bilan {m['achat_pct']} % achat / {m['vente_pct']} % vente ; "
                       f"court terme : {FR[hz['court']['verdict']]}, long terme : {FR[hz['long']['verdict']]}."),
            "arguments_pour": [fmt(i) for i in pros] or ["Aucun indicateur acheteur marquant."],
            "arguments_contre": [fmt(i) for i in cons] or ["Aucun indicateur vendeur marquant."],
            "risques": risks or ["Pas de risque de données majeur détecté."],
            "declencheurs": trig,
            "plan_action": ("Étaler les achats en 3 à 4 fois et laisser le court terme se normaliser." if dec == "renforcer_progressivement"
                            else "Alléger par paliers plutôt qu'en une fois." if dec in ("alleger", "vendre")
                            else "Renforcer si le signal confirme ; sinon conserver sa position actuelle." if dec == "acheter"
                            else "Rester patient : attendre qu'un signal dépasse son seuil."),
            "indicateurs_cles": key,
        }
        notes.append(f"{TAB_LABEL[tab]} : {DECISION_LABEL[dec].lower()}")
    return {"synthese_globale": "Analyse automatique à règles (sans IA) — " + " · ".join(notes) + ".",
            "marches": out,
            "qualite_des_donnees": "Analyse calculée directement à partir du vote pondéré ; elle ne lit pas les nuances entre indicateurs.",
            "avertissement": "Analyse générale, sans prise en compte de votre situation personnelle ; aucune garantie de résultat."}


# ── Analyse rédigée par Claude (optionnelle) ─────────────────────────────────

def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    a, b = text.find("{"), text.rfind("}")
    return json.loads(text[a:b + 1])


def _valid(a: dict) -> bool:
    try:
        for t in eng.TABS:
            m = a["marches"][t]
            assert m["decision"] in DECISIONS and m["confiance"] in ("faible", "moderee", "forte")
            assert m["resume"] and isinstance(m["arguments_pour"], list) and isinstance(m["declencheurs"], list)
        return bool(a["synthese_globale"])
    except Exception:
        return False


def llm_analysis(dossier: dict, key: str):
    body_data = {k: v for k, v in dossier.items() if k != "consignes_chat"}
    user = ("Voici le dossier du jour (JSON). Produis ton analyse au format demandé.\n\n"
            + json.dumps(body_data, ensure_ascii=False, separators=(",", ":")))
    for model in dict.fromkeys(MODELS):
        try:
            r = requests.post("https://api.anthropic.com/v1/messages",
                              headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"},
                              json={"model": model, "max_tokens": 8000, "system": SYSTEM_PROMPT,
                                    "messages": [{"role": "user", "content": user}]},
                              timeout=240)
            if r.status_code != 200:
                print(f"[analyste] {model}: HTTP {r.status_code} {r.text[:200]}")
                continue
            text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
            a = _extract_json(text)
            if _valid(a):
                a["modele"] = model
                return a
            print(f"[analyste] {model}: réponse JSON incomplète")
        except Exception as e:
            print(f"[analyste] {model}: {type(e).__name__}: {e}")
    return None


# ── Orchestration ────────────────────────────────────────────────────────────

def _age_hours(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600
    except Exception:
        return 1e9


def run(out: Path, write_json, iso, backend, browser, reco, history, events, carried):
    def rj(name):
        try:
            return json.loads((out / name).read_text(encoding="utf-8"))
        except Exception:
            return {}

    tr = rj("trackrecord.json")
    dossier = build_dossier(iso, backend, browser, reco, history, events, rj("backtest.json"),
                            rj("analytics.json"), carried, tr.get("resume"))
    write_json(out / "dossier.json", dossier)

    sigs = {t: {"v": m["verdict"], "c": m["horizons"]["court"]["verdict"], "l": m["horizons"]["long"]["verdict"]}
            for t, m in dossier["marches"].items() if not m.get("indisponible")}
    prev = rj("analysis.json")
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    analysis = None

    if key:
        fresh_llm = prev.get("source") == "llm" and _age_hours(prev.get("genere_le")) < MIN_HOURS and prev.get("signaux") == sigs
        if fresh_llm:
            print("[analyste] analyse IA encore valable (aucun signal changé) : conservée")
            return
        analysis = llm_analysis(dossier, key)
        if analysis:
            analysis["source"] = "llm"
            print(f"[analyste] analyse rédigée par {analysis['modele']}")
        elif prev.get("source") == "llm" and _age_hours(prev.get("genere_le")) < 24:
            print("[analyste] échec de l'IA : l'analyse IA de moins de 24 h est conservée")
            return
    if analysis is None:
        analysis = rules_analysis(dossier)
        analysis["source"] = "regles"
        print("[analyste] analyse à règles publiée" + ("" if key else " (pas de clé ANTHROPIC_API_KEY)"))
    analysis["genere_le"] = iso
    analysis["signaux"] = sigs
    write_json(out / "analysis.json", analysis)
