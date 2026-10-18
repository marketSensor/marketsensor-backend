"""
Test temporaire (publié dans diagnostics.json > candidates) : quelles sources gratuites répondent depuis les serveurs GitHub,
avec quelles valeurs. À retirer une fois le choix fait.
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}


def _get(url, timeout=15, **kw):
    return requests.get(url, headers=UA, timeout=timeout, **kw)


def _safe(name, fn):
    try:
        out = fn()
        return {"name": name, **out}
    except Exception as e:
        return {"name": name, "error": f"{type(e).__name__}: {str(e)[:160]}"}


def _http(url, summarize):
    r = _get(url)
    res = {"status": r.status_code}
    try:
        res["sample"] = summarize(r)
    except Exception as e:
        res["parse_error"] = f"{type(e).__name__}: {str(e)[:100]}; début={r.text[:120]!r}"
    return res


# ── FRED ─────────────────────────────────────────────────────────
FRED = ["T10Y2Y", "T10Y3M", "BAMLH0A0HYM2", "BAA10Y", "STLFSI4", "NFCI", "SAHMREALTIME", "VIXCLS", "T10YIE",
        "UNRATE", "ICSA", "UMCSENT", "WALCL", "RRPONTSYD", "DTWEXBGS", "M2SL", "WILL5000PRFC", "GDP", "FEDFUNDS"]


def _fred(series, key, limit=1):
    url = ("https://api.stlouisfed.org/fred/series/observations?series_id=%s&api_key=%s&file_type=json"
           "&sort_order=desc&limit=%d" % (series, key, limit))
    r = requests.get(url, headers=UA, timeout=15)
    j = r.json()
    obs = [(o["date"], o["value"]) for o in j.get("observations", [])]
    return {"status": r.status_code, "sample": obs[:limit] if limit > 1 else (obs[0] if obs else j.get("error_message"))}


def probe_fred():
    key = os.environ.get("FRED_API_KEY", "")
    if not key:
        return [{"name": "FRED", "error": "pas de clé"}]
    first = _safe("FRED T10Y2Y", lambda: _fred("T10Y2Y", key))
    out = [first]
    if "error" in first:      # FRED injoignable : inutile d'attendre 18 délais
        return out
    for s in FRED[1:]:
        out.append(_safe("FRED " + s, lambda s=s: _fred(s, key)))
    out.append(_safe("FRED CPIAUCSL (14 dernières, avec trous)", lambda: _fred("CPIAUCSL", key, limit=14)))
    return out


# ── Coin Metrics (communauté, sans clé) ──────────────────────────
CM_METRICS = ["CapMVRVCur", "CapRealUSD", "CapMrktCurUSD", "NVTAdj", "NVTAdj90", "AdrActCnt", "TxCnt", "TxTfrValAdjUSD",
              "HashRate", "SplyCur", "SplyAct1yr", "FeeTotUSD", "IssTotUSD", "RevUSD", "CapMVRVFF", "ROI30d", "SOPR"]


def probe_coinmetrics():
    out = []
    for m in CM_METRICS:
        url = ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=%s"
               "&frequency=1d&page_size=1&paging_from=end" % m)
        out.append(_safe("CoinMetrics " + m, lambda url=url, m=m: _http(url, lambda r: r.json()["data"][0].get(m) or r.json())))
        time.sleep(0.8)   # 10 requêtes / 6 s
    return out


# ── Autres sources ───────────────────────────────────────────────
def probe_misc():
    items = [
        ("DefiLlama stablecoins", "https://stablecoins.llama.fi/stablecoins?includePrices=false",
         lambda r: {"n": len(r.json()["peggedAssets"])}),
        ("OKX funding rate BTC", "https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP",
         lambda r: r.json()["data"][0]["fundingRate"]),
        ("OKX open interest BTC", "https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP",
         lambda r: r.json()["data"][0]["oiUsd"]),
        ("OKX long/short ratio", "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy=BTC&period=1D",
         lambda r: r.json()["data"][0]),
        ("Blockchain.com revenu mineurs", "https://api.blockchain.info/charts/miners-revenue?timespan=3days&format=json",
         lambda r: r.json()["values"][-1]),
        ("Mempool.space hashrate", "https://mempool.space/api/v1/mining/hashrate/1m",
         lambda r: r.json()["currentHashrate"]),
        ("CNN Fear & Greed", "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
         lambda r: r.json()["fear_and_greed"]),
        ("Stooq S&P 500 (CSV)", "https://stooq.com/q/l/?s=%5Espx&f=sd2t2ohlcv&h&e=csv", lambda r: r.text[:140]),
        ("NY Fed probabilité récession", "https://www.newyorkfed.org/medialibrary/media/research/capital_markets/allmonth.xls",
         lambda r: {"octets": len(r.content), "type": r.headers.get("content-type")}),
        ("Treasury taux (CSV)", "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve&field_tdr_date_value=2026&page&_format=csv",
         lambda r: r.text[:200]),
    ]
    with ThreadPoolExecutor(max_workers=6) as ex:
        return list(ex.map(lambda t: _safe(t[0], lambda: _http(t[1], t[2])), items))


# ── yfinance : tickers supplémentaires ───────────────────────────
YF = ["^VIX3M", "^SKEW", "^VVIX", "^TNX", "^IRX", "^FVX", "^TYX", "HYG", "LQD", "IEF", "TLT", "RSP", "SPY", "XLY", "XLP",
      "^RUT", "^STOXX50E", "^FCHI", "EURUSD=X", "BTC-USD", "GLD", "^MOVE"]


def probe_yfinance():
    import yfinance as yf

    def one(t):
        h = yf.Ticker(t).history(period="1mo")
        return {"rows": int(len(h)), "last": round(float(h["Close"].iloc[-1]), 3) if len(h) else None}

    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(lambda t: _safe("yfinance " + t, lambda t=t: one(t)), YF))


def probe_all() -> list:
    t0 = time.time()
    out = []
    for fn in (probe_fred, probe_coinmetrics, probe_misc, probe_yfinance):
        try:
            out += fn()
        except Exception as e:
            out.append({"name": fn.__name__, "error": f"{type(e).__name__}: {str(e)[:120]}"})
    out.append({"name": "durée du test (s)", "sample": round(time.time() - t0)})
    return out
