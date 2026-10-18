"""
Diagnostic publié avec les données (branche `data`, fichier diagnostics.json) : les journaux GitHub Actions ne sont pas
toujours lisibles, ce fichier dit quelles sources répondent depuis les serveurs GitHub et quelles erreurs ont été vues.
Le dépôt est public : tout est nettoyé des clés et mots de passe avant écriture.
"""
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SECRET_ENV = ("FRED_API_KEY", "SMTP_USER", "SMTP_PASSWORD", "ALERT_EMAILS", "BITBO_API_KEY")
_PATTERN = re.compile(r"(?i)\b(api[_-]?key|apikey|key|token|password|passwd|secret|signature)=[^&\s'\"<>]+")
MAX_LINES, MAX_LEN = 600, 300


def redact(text: str) -> str:
    text = _PATTERN.sub(lambda m: m.group(1) + "=***", text)
    for name in SECRET_ENV:
        for part in re.split(r"[,\s]+", os.environ.get(name, "")):
            if len(part) >= 4:
                text = text.replace(part, "***")
    return text


class _Tee:
    def __init__(self, real, sink):
        self.real, self.sink = real, sink

    def write(self, s):
        self.real.write(s)
        for line in s.splitlines():
            if line.strip():
                self.sink.append(line)
        return len(s)

    def flush(self):
        self.real.flush()

    def __getattr__(self, name):
        return getattr(self.real, name)


class _LogHandler(logging.Handler):
    def __init__(self, sink):
        super().__init__(logging.WARNING)
        self.sink = sink

    def emit(self, record):
        try:
            self.sink.append(f"[log:{record.name}] {record.getMessage()}")
        except Exception:
            pass


_lines: list[str] = []
_orig = (sys.stdout, sys.stderr)
_handler = None


def start() -> None:
    global _handler
    sys.stdout, sys.stderr = _Tee(sys.stdout, _lines), _Tee(sys.stderr, _lines)
    _handler = _LogHandler(_lines)
    logging.getLogger().addHandler(_handler)


def _hosts() -> list[str]:
    """Toutes les adresses appelées par indicators.py, plus Yahoo et les API du frontend."""
    src = (ROOT / "indicators.py").read_text(encoding="utf-8", errors="ignore") if (ROOT / "indicators.py").exists() else ""
    found = set(re.findall(r"https?://([A-Za-z0-9.-]+)", src))
    found |= {"query1.finance.yahoo.com", "fc.yahoo.com", "api.coingecko.com", "api.alternative.me", "api.blockchain.info"}
    return sorted(h for h in found if "." in h and not h.endswith(("github.com", "githubusercontent.com")))


def _probe(host: str) -> dict:
    url = f"https://{host}/"
    if host == "query1.finance.yahoo.com":
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=5d&interval=1d"
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"}, stream=True)
        out = {"host": host, "status": r.status_code}
        r.close()
        return out
    except Exception as e:
        return {"host": host, "error": redact(f"{type(e).__name__}: {e}")[:200]}


def _bitbo_samples() -> list[dict]:
    """Réponse brute de Bitbo (code, en-têtes de limite, début du contenu) : les 6 indicateurs on-chain restent vides sans erreur visible."""
    src = (ROOT / "indicators.py").read_text(encoding="utf-8", errors="ignore") if (ROOT / "indicators.py").exists() else ""
    if "charts.bitbo.io" not in src:
        return []
    mod = sys.modules.get("indicators")
    headers = dict(getattr(mod, "HEADERS", {"User-Agent": "Mozilla/5.0"}))
    time.sleep(15)  # Bitbo limite à 5 requêtes par minute : on laisse retomber la rafale du build
    out = []
    for i, endpoint in enumerate(("mvrv-z-score", "nupl-ratio", "sopr")):
        if i:
            time.sleep(13)
        url = f"https://charts.bitbo.io/api/v1/{endpoint}/?latest=true"
        try:
            r = requests.get(url, headers=headers, timeout=15)
            item = {"endpoint": endpoint, "status": r.status_code, "content_type": r.headers.get("content-type"),
                    "limit_headers": {k: v for k, v in r.headers.items() if re.search(r"(?i)rate|retry|limit", k)},
                    "body": redact(r.text[:400])}
            try:
                j = r.json()
                item["json_type"] = type(j).__name__
                if isinstance(j, dict):
                    item["json_keys"] = list(j)[:10]
                    rows = j.get("data")
                    item["rows"] = len(rows) if isinstance(rows, list) else None
                    item["last_row"] = rows[-1] if isinstance(rows, list) and rows else None
            except Exception as e:
                item["json_error"] = f"{type(e).__name__}"
            out.append(item)
        except Exception as e:
            out.append({"endpoint": endpoint, "error": redact(f"{type(e).__name__}: {e}")[:200]})
    return out


def _candidates():
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import probe_candidates
        res = probe_candidates.probe_all()
        return json.loads(redact(json.dumps(res, ensure_ascii=False, default=str)))
    except Exception as e:
        return [{"error": redact(f"{type(e).__name__}: {e}")[:200]}]


def _version(pkg: str):
    try:
        return metadata.version(pkg)
    except Exception:
        return None


def finish(out_dir) -> None:
    sys.stdout, sys.stderr = _orig
    if _handler:
        logging.getLogger().removeHandler(_handler)
    try:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=12) as ex:
            probes = list(ex.map(_probe, _hosts()))
        seen, kept = set(), []
        for line in _lines:
            line = redact(line)[:MAX_LEN]
            if line not in seen:
                seen.add(line)
                kept.append(line)
        doc = {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "python": sys.version.split()[0],
            "versions": {p: _version(p) for p in ("yfinance", "pandas", "numpy", "requests", "curl_cffi")},
            "secrets_present": {n: bool(os.environ.get(n)) for n in SECRET_ENV},
            "probes": probes,
            "bitbo": _bitbo_samples(),
            "candidates": _candidates(),
            "log_lines_total": len(_lines),
            "log": kept[:MAX_LINES],
        }
        (out_dir / "diagnostics.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[diagnostics] écrit : {len(probes)} sources testées, {len(kept)} lignes de journal", file=sys.stderr)
    except Exception as e:  # le diagnostic ne doit jamais faire échouer le build
        print(f"[diagnostics] impossible : {e}", file=sys.stderr)
