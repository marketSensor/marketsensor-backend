"""
Suivi de grands investisseurs, à partir de sources officielles publiques et gratuites.

  Warren Buffett  -> Berkshire Hathaway : déclarations 13F de la SEC (positions en actions américaines à la fin de chaque
                     trimestre, publiées jusqu'à 45 jours après), trésorerie issue des comptes (XBRL SEC), indicateur de Buffett (FRED).
  Nancy Pelosi    -> déclarations de transactions (STOCK Act) publiées par le greffe de la Chambre des représentants (PDF).

Aucune de ces données n'est en temps réel : un 13F décrit la situation à la fin du trimestre, une déclaration de transaction
est publiée jusqu'à 45 jours après l'opération, avec des montants en tranches. Fichiers : buffett.json, pelosi.json.
Chaque fichier précédent est conservé si la source est indisponible.
"""
import io
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import requests

SEC_HEADERS = {"User-Agent": "MarketSense/1.0 (suivi public ; github.com/marketSensor/marketsensor-backend)",
               "Accept-Encoding": "gzip, deflate"}
CLERK_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
BRK_CIK = "1067983"
BUFFETT_REFRESH_H = 12
PELOSI_REFRESH_H = 6
PELOSI_YEARS = 3                # année en cours + 2 précédentes
PELOSI_MAX_NEW_PDF = 30         # PDF analysés par exécution (le reste suit aux exécutions suivantes)

# Correspondance nom d'émetteur (13F) -> symbole, pour les positions connues (sinon on affiche seulement le nom)
TICKERS = {
    "APPLE INC": "AAPL", "AMERICAN EXPRESS CO": "AXP", "BANK AMER CORP": "BAC", "COCA COLA CO": "KO",
    "CHEVRON CORP NEW": "CVX", "OCCIDENTAL PETE CORP": "OXY", "KRAFT HEINZ CO": "KHC", "MOODYS CORP": "MCO",
    "CHUBB LIMITED": "CB", "DAVITA INC": "DVA", "KROGER CO": "KR", "VISA INC": "V", "MASTERCARD INC": "MA",
    "AMAZON COM INC": "AMZN", "ALPHABET INC": "GOOGL", "VERISIGN INC": "VRSN", "CONSTELLATION BRANDS INC": "STZ",
    "DOMINOS PIZZA INC": "DPZ", "POOL CORP": "POOL", "LENNAR CORP": "LEN", "NVR INC": "NVR", "HP INC": "HPQ",
    "CITIGROUP INC": "C", "LOUISIANA PAC CORP": "LPX", "ALLY FINL INC": "ALLY", "T-MOBILE US INC": "TMUS",
    "UNITEDHEALTH GROUP INC": "UNH", "SNOWFLAKE INC": "SNOW", "NU HOLDINGS LTD": "NU", "JEFFERIES FINL GROUP INC": "JEF",
    "SIRIUS XM HOLDINGS INC": "SIRI", "CELANESE CORP DEL": "CE", "ULTA BEAUTY INC": "ULTA", "SPDR S&P 500 ETF TR": "SPY",
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _age_hours(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600
    except Exception:
        return 1e9


def _get(url, headers, timeout=40, tries=3, **kw):
    last = None
    for n in range(tries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout, **kw)
            if r.status_code in (429, 503) and n + 1 < tries:
                time.sleep(2 + 3 * n)
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise last


# ══════════════════════════════════════════════════════════════════════════════
# WARREN BUFFETT — Berkshire Hathaway (SEC EDGAR)
# ══════════════════════════════════════════════════════════════════════════════

def _strip_ns(root):
    for el in root.iter():
        el.tag = el.tag.split("}", 1)[-1]
    return root


def _txt(el, path):
    x = el.find(path)
    return (x.text or "").strip() if x is not None and x.text else ""


def parse_infotable(xml_bytes: bytes) -> list[dict]:
    """Positions d'un 13F, regroupées par CUSIP (un même titre peut apparaître plusieurs fois)."""
    root = _strip_ns(ET.fromstring(xml_bytes))
    agg = {}
    for it in root.iter("infoTable"):
        if _txt(it, "putCall"):                      # options : hors périmètre du tableau principal
            continue
        cusip = _txt(it, "cusip")
        try:
            value = float(_txt(it, "value") or 0)
            shares = float(_txt(it, "shrsOrPrnAmt/sshPrnamt") or 0)
        except ValueError:
            continue
        a = agg.setdefault(cusip, {"name": _txt(it, "nameOfIssuer"), "class": _txt(it, "titleOfClass"),
                                   "cusip": cusip, "value": 0.0, "shares": 0.0})
        a["value"] += value
        a["shares"] += shares
    rows = list(agg.values())
    if rows and sum(r["value"] for r in rows) < 1e10:       # anciennes déclarations : valeurs en milliers de dollars
        for r in rows:
            r["value"] *= 1000
    return rows


def _norm_name(n: str) -> str:
    return re.sub(r"[^A-Z0-9&\- ]", "", n.upper().replace(".", "")).strip()


def _ticker(name: str):
    n = _norm_name(name)
    return TICKERS.get(n) or next((t for k, t in TICKERS.items() if n.startswith(k)), None)


def _list_13f(sub: dict) -> list[dict]:
    r = sub["filings"]["recent"]
    rows = [{"acc": a, "form": f, "filed": d, "period": p}
            for a, f, d, p in zip(r["accessionNumber"], r["form"], r["filingDate"], r["reportDate"]) if f == "13F-HR"]
    return sorted(rows, key=lambda x: x["period"], reverse=True)


def _fetch_holdings(acc: str) -> list[dict]:
    base = f"https://www.sec.gov/Archives/edgar/data/{BRK_CIK}/{acc.replace('-', '')}/"
    idx = _get(base + "index.json", SEC_HEADERS).json()
    names = [x["name"] for x in idx["directory"]["item"]]
    xmls = [n for n in names if n.lower().endswith(".xml") and "primary_doc" not in n.lower()]
    pick = next((n for n in xmls if "info" in n.lower()), xmls[0] if xmls else None)
    if not pick:
        raise RuntimeError(f"table d'information introuvable dans {acc} : {names}")
    return parse_infotable(_get(base + pick, SEC_HEADERS).content)


def build_holdings(cur: list[dict], prev: list[dict]) -> dict:
    total = sum(r["value"] for r in cur)
    pmap = {r["cusip"]: r for r in prev}
    out = []
    for r in sorted(cur, key=lambda x: -x["value"]):
        p = pmap.get(r["cusip"])
        if p is None:
            ch = {"type": "nouveau"}
        else:
            d = r["shares"] - p["shares"]
            pct = d / p["shares"] * 100 if p["shares"] else 0
            ch = {"type": "hausse" if pct > 1 else "baisse" if pct < -1 else "stable", "pct": round(pct, 1)}
        out.append({"name": r["name"].title(), "ticker": _ticker(r["name"]), "class": r["class"], "value": r["value"],
                    "shares": r["shares"], "pct": round(r["value"] / total * 100, 2) if total else None, "change": ch})
    cur_set = {r["cusip"] for r in cur}
    sold = [{"name": p["name"].title(), "ticker": _ticker(p["name"]), "value": p["value"]}
            for p in sorted(prev, key=lambda x: -x["value"]) if p["cusip"] not in cur_set]
    return {"total_value": total, "n_positions": len(out), "holdings": out, "sold_out": sold,
            "top5_pct": round(sum(h["pct"] or 0 for h in out[:5]), 1), "top10_pct": round(sum(h["pct"] or 0 for h in out[:10]), 1)}


def _cash():
    """Trésorerie de Berkshire (comptes trimestriels, XBRL SEC). Meilleur effort : composantes affichées séparément."""
    j = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{BRK_CIK.zfill(10)}.json", SEC_HEADERS, timeout=90).json()
    found = {}
    for ns, tags in j.get("facts", {}).items():
        for tag, body in tags.items():
            usd = (body.get("units") or {}).get("USD")
            if not usd:
                continue
            label = f"{tag} {body.get('label', '')}"
            kind = None
            if tag == "CashAndCashEquivalentsAtCarryingValue":
                kind = "cash"
            elif re.search(r"treasury", label, re.I) and re.search(r"bill|short", label, re.I) and not re.search(r"stock|shares|repurchase|interest|income|gain|unrealized", label, re.I):
                kind = "tbills"
            if not kind:
                continue
            pts = [x for x in usd if x.get("form") in ("10-Q", "10-K") and x.get("end") and "start" not in x]
            if not pts:
                continue
            last = max(pts, key=lambda x: (x["end"], x.get("filed", "")))
            cand = {"tag": f"{ns}:{tag}", "end": last["end"], "val": last["val"]}
            if kind not in found or cand["end"] > found[kind]["end"] or (cand["end"] == found[kind]["end"] and cand["val"] > found[kind]["val"]):
                found[kind] = cand
    print(f"[buffett] trésorerie : { {k: (v['tag'], v['end'], round(v['val'] / 1e9, 1)) for k, v in found.items()} }")
    return found or None


def _buffett_indicator():
    """Valeur de marché des actions américaines (Fed, flux Z.1) rapportée au PIB : repère de valorisation globale."""
    import indicators
    eq = indicators._fred("NCBEILQ027S")          # millions de $
    gdp = indicators._fred("GDP")                 # milliards de $
    if not eq or not gdp:
        return None
    return round(eq[0] / 1000 / gdp[0] * 100, 0)


def update_buffett(out, write_json, read_json):
    path = out / "buffett.json"
    prev_file = read_json(path, {})
    if prev_file.get("holdings") and _age_hours(prev_file.get("generated")) < BUFFETT_REFRESH_H:
        print("[buffett] données récentes : conservées")
        return
    try:
        sub = _get(f"https://data.sec.gov/submissions/CIK{BRK_CIK.zfill(10)}.json", SEC_HEADERS).json()
        filings = _list_13f(sub)
        if len(filings) < 2:
            raise RuntimeError(f"moins de 2 déclarations 13F trouvées ({len(filings)})")
        new, old = filings[0], filings[1]
        if prev_file.get("filing", {}).get("accession") == new["acc"] and prev_file.get("holdings"):
            data = prev_file                              # même trimestre : on ne retélécharge pas les positions
        else:
            cur, prv = _fetch_holdings(new["acc"]), _fetch_holdings(old["acc"])
            data = {**build_holdings(cur, prv),
                    "filing": {"accession": new["acc"], "filed": new["filed"], "period": new["period"],
                               "url": f"https://www.sec.gov/Archives/edgar/data/{BRK_CIK}/{new['acc'].replace('-', '')}/"},
                    "previous": {"period": old["period"], "filed": old["filed"]}}
        data["generated"] = _now_iso()
        try:
            data["cash"] = _cash() or prev_file.get("cash")
        except Exception as e:
            print(f"[buffett] trésorerie indisponible : {type(e).__name__}: {e}")
            data["cash"] = prev_file.get("cash")
        try:
            data["buffett_indicator"] = _buffett_indicator()
        except Exception as e:
            print(f"[buffett] indicateur de Buffett : {e}")
        write_json(path, data)
        print(f"[buffett] {data['n_positions']} positions, {data['total_value'] / 1e9:.0f} Md$, trimestre {data['filing']['period']}")
    except Exception as e:
        print(f"::warning::Buffett : {type(e).__name__}: {e} (fichier précédent conservé)")


# ══════════════════════════════════════════════════════════════════════════════
# NANCY PELOSI — déclarations de transactions (greffe de la Chambre des représentants)
# ══════════════════════════════════════════════════════════════════════════════

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
AMT_RE = re.compile(r"\$[\d,]+(?:\s*-\s*\$[\d,]+)?|Over\s+\$[\d,]+", re.I)
TYPE_LABEL = {"P": "achat", "S": "vente", "S (PARTIAL)": "vente partielle", "E": "échange"}
OWNER_LABEL = {"SP": "conjoint", "DC": "enfant à charge", "JT": "compte joint", "": "elle-même"}
TAIL_RE = re.compile(r"\[(?P<at>[A-Z]{2})\]\s*(?P<tt>S \(partial\)|P|S|E)\s+(?P<d1>\d{2}/\d{2}/\d{4})\s+(?P<d2>\d{2}/\d{2}/\d{4})\s+"
                     r"(?P<amt>Over\s+\$[\d,]+|\$[\d,]+(?:\s*-\s*\$[\d,]+)?)", re.I)


def _iso(mdy: str):
    try:
        return datetime.strptime(mdy, "%m/%d/%Y").date().isoformat()
    except Exception:
        return None


def _amount(label: str):
    nums = [int(x.replace(",", "")) for x in re.findall(r"\$([\d,]+)", label)]
    if not nums:
        return None, None
    return nums[0], nums[-1] if len(nums) > 1 else None


def _clean_asset(raw: str):
    """-> (nom, ticker, type d'actif, description) à partir du texte brut de la cellule « actif »."""
    text = re.sub(r"[ \t]+", " ", raw).strip()
    desc = None
    m = re.search(r"\b(?:D|C):\s*(.+)$", text, re.S)
    if m:
        desc = re.sub(r"\s+", " ", m.group(1)).strip()
    head = re.split(r"\n\s*(?:F\s*S:|D:|C:|S\s*O:|L:)", text)[0]
    head = re.sub(r"\s+", " ", head).strip()
    at = None
    m = re.search(r"\[([A-Z]{2})\]", head)
    if m:
        at = m.group(1)
        head = head.replace(m.group(0), "").strip()
    tk = None
    m = re.search(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s*$", head)
    if m:
        tk = m.group(1)
        head = head[:m.start()].strip()
    return head, tk, at, desc


def _trade(doc, filed, owner, asset_raw, ttype, d1, d2, amt):
    name, tk, at, desc = _clean_asset(asset_raw)
    lo, hi = _amount(amt)
    tdate, ndate = _iso(d1), _iso(d2)
    t = TYPE_LABEL.get(re.sub(r"\s+", " ", ttype.strip()).upper(), ttype)
    delay = (date.fromisoformat(filed) - date.fromisoformat(tdate)).days if filed and tdate else None
    opt = bool(re.search(r"option|call|put", f"{name} {desc or ''}", re.I)) or at == "OP"
    return {"doc": doc, "owner": OWNER_LABEL.get(owner, owner or "elle-même"), "asset": name, "ticker": tk, "asset_type": at,
            "type": t, "date": tdate, "notified": ndate, "filed": filed, "delay_days": delay,
            "amount": re.sub(r"\s+", " ", amt).strip(), "amount_low": lo, "amount_high": hi,
            "description": desc[:300] if desc else None, "option": opt}


def parse_ptr_tables(tables, doc, filed):
    out = []
    amt_full = re.compile(r"^(?:Over\s+\$[\d,]+|\$[\d,]+(?:\s*-\s*\$[\d,]+)?)$", re.I)
    for tb in tables or []:
        for row in tb:
            cells = [re.sub(r"\s+", " ", (c or "")).strip() for c in row]
            dates = [c for c in cells if DATE_RE.match(c)]
            if len(dates) < 2:
                continue
            ttype = next((c for c in cells if re.fullmatch(r"(?i)P|S|E|S \(partial\)", c)), None)
            amt = next((c for c in cells if amt_full.match(c)), None)
            owner = next((c for c in cells if c in ("SP", "DC", "JT")), "")
            rest = [(c, raw) for c, raw in zip(cells, [(x or "") for x in row])
                    if c and c not in (ttype, amt, owner) and not DATE_RE.match(c)]
            tagged = [r for r in rest if re.search(r"\[[A-Z]{2}\]", r[0])]
            asset = (tagged or sorted(rest, key=lambda r: -len(r[0])))[0][1] if rest else None   # texte brut : conserve les retours à la ligne
            if ttype and amt and asset:
                out.append(_trade(doc, filed, owner, asset, ttype, dates[0], dates[1], amt))
    return out


def parse_ptr_text(text, doc, filed):
    """Repli : lecture du texte brut quand les tableaux ne sont pas détectés."""
    body = text.split("$200?", 1)[-1]
    matches = list(TAIL_RE.finditer(body))
    out, pos = [], 0
    for m in matches:
        seg = body[pos:m.start()]
        pos = m.end()
        # on écarte la description de la ligne précédente : l'actif commence après le dernier bloc « F S: / D: / C: »
        parts = re.split(r"\n(?=(?:SP|DC|JT)\s|[A-Z])", seg.strip("\n"))
        asset = parts[-1] if parts else seg
        owner = ""
        mo = re.match(r"^(SP|DC|JT)\s+", asset)
        if mo:
            owner, asset = mo.group(1), asset[mo.end():]
        # l'étiquette [XX] fait partie du motif de fin : on la remet pour le nettoyage
        out.append(_trade(doc, filed, owner, f"{asset} [{m.group('at')}]", m.group("tt"), m.group("d1"), m.group("d2"), m.group("amt")))
    return out


def parse_ptr_pdf(pdf_bytes: bytes, doc: str, filed: str, debug=False):
    import pdfplumber
    trades, text_all, tables_all = [], [], []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text_all.append(page.extract_text() or "")
            try:
                tables_all += page.extract_tables() or []
            except Exception:
                pass
    text = "\n".join(text_all)
    trades = parse_ptr_tables(tables_all, doc, filed)
    method = "tableaux"
    if not trades:
        trades, method = parse_ptr_text(text, doc, filed), "texte"
    if debug:
        print(f"[pelosi] debug {doc}: méthode={method}, {len(trades)} transactions, {len(tables_all)} tableaux")
        for line in text[:900].splitlines()[:14]:
            print(f"[pelosi] debug texte | {line}")
        for t in trades[:2]:
            print(f"[pelosi] debug exemple | {json.dumps(t, ensure_ascii=False)[:280]}")
    scanned = not text.strip()
    return trades, scanned


def _clerk_index(year: int):
    r = _get(f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.xml", CLERK_HEADERS, timeout=60)
    root = ET.fromstring(r.content)
    docs = []
    for m in root.iter("Member"):
        if (m.findtext("Last") or "").strip().lower() == "pelosi" and "nancy" in (m.findtext("First") or "").lower() \
                and (m.findtext("FilingType") or "").strip() == "P":
            fd = (m.findtext("FilingDate") or "").strip()
            docs.append({"doc": (m.findtext("DocID") or "").strip(), "year": year, "filed": _iso(fd) or fd})
    return docs


def _performance(trades):
    """Évolution du cours depuis la transaction (achats d'actions seulement), calculée avec yfinance."""
    import yfinance as yf
    tickers = sorted({t["ticker"] for t in trades if t.get("ticker") and t["asset_type"] == "ST" and t["date"]
                      and t["date"] >= (date.today() - timedelta(days=730)).isoformat()})[:45]
    hist = {}
    for tk in tickers:
        try:
            h = yf.Ticker(tk.replace(".", "-")).history(period="3y", auto_adjust=True)["Close"].dropna()
            h.index = h.index.tz_localize(None).normalize()
            if len(h):
                hist[tk] = h
        except Exception as e:
            print(f"[pelosi] cours {tk}: {e}")
    import pandas as pd
    for t in trades:
        h = hist.get(t.get("ticker"))
        t.pop("since_pct", None)
        if h is None or t["asset_type"] != "ST" or not t["date"] or t["type"] not in ("achat", "vente", "vente partielle"):
            continue
        x = h[h.index >= pd.Timestamp(t["date"])]
        if len(x) and (x.index[0] - pd.Timestamp(t["date"])).days <= 5 and float(x.iloc[0]) > 0:
            t["since_pct"] = round((float(h.iloc[-1]) / float(x.iloc[0]) - 1) * 100, 1)
    return len(hist)


def update_pelosi(out, write_json, read_json):
    path, cache_path = out / "pelosi.json", out / "pelosi_cache.json"
    prev = read_json(path, {})
    docs_prev = read_json(cache_path, {}).get("docs", {})
    complete = prev.get("complete") and not prev.get("pending")
    if prev.get("trades") and complete and _age_hours(prev.get("generated")) < PELOSI_REFRESH_H:
        print("[pelosi] données récentes : conservées")
        return
    try:
        year = date.today().year
        index = []
        for y in range(year, year - PELOSI_YEARS, -1):
            try:
                index += _clerk_index(y)
            except Exception as e:
                print(f"[pelosi] index {y}: {type(e).__name__}: {e}")
        if not index:
            raise RuntimeError("aucune déclaration trouvée dans l'index du greffe")
        docs = dict(docs_prev)
        todo = [d for d in index if d["doc"] not in docs or docs[d["doc"]].get("status") == "erreur"]
        todo = sorted(todo, key=lambda d: d["filed"] or "", reverse=True)
        n_debug = 0
        for d in todo[:PELOSI_MAX_NEW_PDF]:
            url = f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{d['year']}/{d['doc']}.pdf"
            try:
                pdf = _get(url, CLERK_HEADERS, timeout=60).content
                trades, scanned = parse_ptr_pdf(pdf, d["doc"], d["filed"], debug=(n_debug < 2))
                n_debug += 1
                status = "ok" if trades else ("image" if scanned else "vide")
                docs[d["doc"]] = {"status": status, "filed": d["filed"], "year": d["year"], "url": url, "trades": trades}
            except Exception as e:
                print(f"[pelosi] {d['doc']}: {type(e).__name__}: {e}")
                docs[d["doc"]] = {"status": "erreur", "filed": d["filed"], "year": d["year"], "url": url, "trades": []}
        pending = len([d for d in index if d["doc"] not in docs or docs[d["doc"]].get("status") == "erreur"])
        trades = sorted([t for v in docs.values() for t in v.get("trades", [])],
                        key=lambda t: (t["date"] or "", t["filed"] or ""), reverse=True)
        try:
            n_px = _performance(trades)
            print(f"[pelosi] cours récupérés pour {n_px} titres")
        except Exception as e:
            print(f"[pelosi] performance : {type(e).__name__}: {e}")
        stats = {s: sum(1 for v in docs.values() if v["status"] == s) for s in ("ok", "image", "vide", "erreur")}
        write_json(cache_path, {"docs": docs})
        res = {"generated": _now_iso(), "pending": pending, "complete": pending == 0,
               "documents": stats, "filings": {k: {"status": v["status"], "filed": v["filed"], "url": v["url"]} for k, v in docs.items()}, "trades": trades, "n_trades": len(trades),
               "first": trades[-1]["date"] if trades else None, "last": trades[0]["date"] if trades else None,
               "source": "https://disclosures-clerk.house.gov/FinancialDisclosure"}
        write_json(path, res)
        print(f"[pelosi] {len(trades)} transactions, documents {stats}, {pending} en attente")
    except Exception as e:
        print(f"::warning::Pelosi : {type(e).__name__}: {e} (fichier précédent conservé)")


def update(out, write_json, read_json):
    for name, fn in (("buffett", update_buffett), ("pelosi", update_pelosi)):
        try:
            fn(out, write_json, read_json)
        except Exception as e:
            print(f"::warning::{name} : {type(e).__name__}: {e}")
