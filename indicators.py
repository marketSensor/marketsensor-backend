"""
MarketSense — indicators.py
Fetches and calculates all market indicators via :
  - yfinance     : prices, RSI, MACD, Bollinger, ATR, MA crosses
  - multpl.com   : Shiller CAPE (scrape)
  - FRED API     : CPI, taux réels TIPS (clé optionnelle mais recommandée)
"""

import os
import math
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from bs4 import BeautifulSoup

FRED_KEY = os.getenv("FRED_API_KEY", "")
HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; MarketSense/1.0)"}
TIMEOUT  = 10


# ══════════════════════════════════════════════════════════════════
# CALCULATION HELPERS
# ══════════════════════════════════════════════════════════════════

def _rsi(series: pd.Series, period: int = 14) -> float | None:
    if len(series) < period + 1:
        return None
    delta = series.diff().dropna()
    gain  = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - 100 / (1 + rs)
    return round(float(rsi.iloc[-1]))


def _macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast   = series.ewm(span=fast,   adjust=False).mean()
    ema_slow   = series.ewm(span=slow,   adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1])


def _bollinger_pct(series: pd.Series, window=20) -> float | None:
    if len(series) < window:
        return None
    ma  = series.rolling(window).mean()
    std = series.rolling(window).std()
    upper = ma + 2 * std
    lower = ma - 2 * std
    pct = (series.iloc[-1] - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1]) * 100
    return round(max(0, min(100, pct)))


def _atr_pct(hist: pd.DataFrame, period=14) -> float | None:
    if len(hist) < period + 1:
        return None
    high, low, close = hist["High"], hist["Low"], hist["Close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return round(atr / close.iloc[-1] * 100, 2)


def _norm(v: float, lo: float, hi: float) -> int:
    return int(max(0, min(100, (v - lo) / (hi - lo) * 100)))


def _rsi_sig(v: float, buy=35, sell=65) -> str:
    return "buy" if v < buy else "sell" if v > sell else "neutral"


def _ind(val, raw, unit, sig, desc) -> dict:
    return {"val": val, "raw": str(raw), "unit": unit, "sig": sig, "desc": desc}


# ══════════════════════════════════════════════════════════════════
# FRED API
# ══════════════════════════════════════════════════════════════════

def _fred(series_id: str, limit: int = 1) -> list[float] | None:
    if not FRED_KEY:
        return None
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_KEY}"
        f"&file_type=json&sort_order=desc&limit={limit}"
    )
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        obs = r.json().get("observations", [])
        return [float(o["value"]) for o in obs if o["value"] != "."]
    except Exception as e:
        print(f"[FRED] {series_id}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
# SCRAPING
# ══════════════════════════════════════════════════════════════════

def _scrape_cape() -> float | None:
    try:
        r    = requests.get("https://www.multpl.com/shiller-pe", headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        el   = soup.select_one("#current-value")
        if el:
            return float(el.text.strip().replace(",", "."))
    except Exception as e:
        print(f"[scrape] CAPE: {e}")
    return None


# ══════════════════════════════════════════════════════════════════
# BOURSE INDICATORS
# ══════════════════════════════════════════════════════════════════

def _bourse() -> dict:
    out = {}

    # ── S&P 500 ─────────────────────────────────────────────────
    try:
        hist  = yf.Ticker("^GSPC").history(period="2y")
        close = hist["Close"]
        price = float(close.iloc[-1])
        ma50  = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])

        # RSI
        rsi_val = _rsi(close)
        if rsi_val is not None:
            out["rsi_spx"] = _ind(
                rsi_val, rsi_val, "", _rsi_sig(rsi_val),
                f"RSI S&P 500 à {rsi_val} — "
                + ("suracheté, risque de correction à court terme." if rsi_val > 70
                   else "survendu — opportunité d'achat potentielle." if rsi_val < 30
                   else "zone neutre, momentum équilibré."),
            )

        # MACD
        macd_v, signal_v = _macd(close)
        bull = macd_v > signal_v
        out["macd_spx"] = _ind(
            72 if bull else 28, "Positif" if bull else "Négatif", "",
            "buy" if bull else "sell",
            f"MACD S&P 500 {'au-dessus' if bull else 'sous'} sa ligne de signal — "
            f"tendance {'haussière' if bull else 'baissière'} confirmée.",
        )

        # MM50 vs Prix
        above50 = price > ma50
        out["mm50"] = _ind(
            78 if above50 else 25,
            "Au-dessus" if above50 else "En dessous", "",
            "buy" if above50 else "sell",
            f"S&P 500 {'au-dessus' if above50 else 'en dessous'} de sa MM50 "
            f"({int(ma50):,}) — tendance court terme {'haussière.' if above50 else 'baissière.'}",
        )

        # MM200 vs Prix
        above200 = price > ma200
        out["mm200"] = _ind(
            83 if above200 else 20,
            "Au-dessus" if above200 else "En dessous", "",
            "buy" if above200 else "sell",
            f"S&P 500 {'au-dessus' if above200 else 'en dessous'} de sa MM200 "
            f"({int(ma200):,}) — tendance long terme {'haussière.' if above200 else 'baissière.'}",
        )

        # Golden / Death Cross
        golden = ma50 > ma200
        out["cross"] = _ind(
            80 if golden else 20,
            "Golden Cross" if golden else "Death Cross", "",
            "buy" if golden else "sell",
            f"{'Golden Cross actif (MM50 > MM200) — signal historiquement très haussier.' if golden else 'Death Cross actif (MM50 < MM200) — signal baissier à long terme.'}",
        )

        # Bollinger Bands
        boll = _bollinger_pct(close)
        if boll is not None:
            b_sig = "sell" if boll > 80 else "buy" if boll < 20 else "neutral"
            out["bollinger"] = _ind(
                boll, f"{boll} %", "",
                b_sig,
                f"Position dans les bandes de Bollinger : {boll} % — "
                + ("proche de la bande haute, risque de retournement." if boll > 80
                   else "proche de la bande basse, rebond possible." if boll < 20
                   else "zone médiane, compression de volatilité en cours."),
            )

        # ATR
        atr = _atr_pct(hist)
        if atr is not None:
            a_sig = "sell" if atr > 2.5 else "buy" if atr < 0.8 else "neutral"
            out["atr"] = _ind(
                _norm(atr, 0.5, 3.0), f"{atr} %", " du prix",
                a_sig,
                f"ATR à {atr} % du prix — "
                + ("volatilité élevée, marché nerveux." if atr > 2.5
                   else "faible volatilité, conditions calmes." if atr < 0.8
                   else "volatilité dans la moyenne historique."),
            )

    except Exception as e:
        print(f"[bourse] S&P 500: {e}")

    # ── VIX ─────────────────────────────────────────────────────
    try:
        vix_hist  = yf.Ticker("^VIX").history(period="5d")
        vix_price = round(float(vix_hist["Close"].iloc[-1]), 1)
        v_sig = "sell" if vix_price > 30 else "buy" if vix_price < 15 else "neutral"
        out["vix"] = _ind(
            _norm(vix_price, 10, 50), vix_price, "",
            v_sig,
            f"VIX à {vix_price} — "
            + ("marché très anxieux, opportunité contrariante possible." if vix_price > 30
               else "complacence élevée, méfiance vis-à-vis des chocs." if vix_price < 15
               else "volatilité modérée, marché relativement serein."),
        )
    except Exception as e:
        print(f"[bourse] VIX: {e}")

    # ── Shiller CAPE ────────────────────────────────────────────
    try:
        cape = _scrape_cape()
        if cape:
            c_sig = "sell" if cape > 30 else "buy" if cape < 15 else "neutral"
            out["cape"] = _ind(
                _norm(cape, 10, 45), round(cape, 1), "x",
                c_sig,
                f"Shiller CAPE à {round(cape,1)}x (multpl.com) — "
                + ("marchés fortement surévalués historiquement (moy. ~16x). Prudence long terme." if cape > 30
                   else "valorisation attrayante, bon potentiel de rendement futur." if cape < 15
                   else "valorisation dans la moyenne historique."),
            )
    except Exception as e:
        print(f"[bourse] CAPE: {e}")

    # ── Taux réels TIPS 10y (FRED) ───────────────────────────────
    try:
        tips = _fred("DFII10")
        if tips:
            t = tips[0]
            t_sig = "buy" if t < 0.5 else "sell" if t > 2 else "neutral"
            out["realrates"] = _ind(
                _norm(t, -2, 4), f"{t:.1f} %", "",
                t_sig,
                f"Taux réels TIPS 10y à {t:.1f} % (FRED) — "
                + ("très favorable à l'or et aux actifs réels." if t < 0.5
                   else "taux élevés, pression sur l'or et les matières premières." if t > 2
                   else "taux modérés, impact neutre sur les matières premières."),
            )
    except Exception as e:
        print(f"[bourse] TIPS: {e}")

    # ── CPI YoY (FRED) ────────────────────────────────────────────
    try:
        cpi_obs = _fred("CPIAUCSL", limit=13)
        if cpi_obs and len(cpi_obs) >= 13:
            yoy = round((cpi_obs[0] / cpi_obs[12] - 1) * 100, 1)
            c_sig = "buy" if yoy > 2 else "neutral" if yoy > 0.5 else "sell"
            out["cpi"] = _ind(
                _norm(yoy, 0, 8), f"{yoy} %", "",
                c_sig,
                f"Inflation CPI à {yoy} % annualisé (FRED) — "
                + ("soutient les actifs tangibles et matières premières." if yoy > 2
                   else "inflation basse, moins de pression favorable sur les matières premières." if yoy < 1
                   else "inflation modérée."),
            )
    except Exception as e:
        print(f"[bourse] CPI: {e}")

    return out


# ══════════════════════════════════════════════════════════════════
# MATIÈRES PREMIÈRES INDICATORS
# ══════════════════════════════════════════════════════════════════

def _matieres() -> dict:
    out = {}

    symbols = {
        "gold":     "GC=F",
        "silver":   "SI=F",
        "platinum": "PL=F",
        "palladium":"PA=F",
        "copper":   "HG=F",
        "dxy":      "DX-Y.NYB",
        "uranium":  "URA",   # ETF proxy
    }

    data: dict[str, pd.Series] = {}
    for name, sym in symbols.items():
        try:
            h = yf.Ticker(sym).history(period="2y")
            if not h.empty:
                data[name] = h["Close"]
        except Exception as e:
            print(f"[matieres] {sym}: {e}")

    # ── DXY ─────────────────────────────────────────────────────
    if "dxy" in data:
        s    = data["dxy"]
        p    = round(float(s.iloc[-1]), 1)
        ma200 = float(s.rolling(200).mean().iloc[-1])
        rsi_v = _rsi(s)
        strong = p > ma200
        d_sig  = "sell" if strong else "buy"  # fort dollar = mauvais pour MP
        out["dxy"] = _ind(
            _norm(p, 90, 115), p, "",
            d_sig,
            f"DXY à {p} ({'au-dessus' if strong else 'en dessous'} de sa MM200 {ma200:.0f}) — "
            f"dollar {'fort, pression sur les matières premières.' if strong else 'en repli, favorable aux matières premières.'}",
        )

    # ── RSI Or ────────────────────────────────────────────────
    if "gold" in data:
        s   = data["gold"]
        rv  = _rsi(s)
        p   = round(float(s.iloc[-1]))
        if rv is not None:
            out["goldrsi"] = _ind(
                rv, rv, "", _rsi_sig(rv),
                f"RSI Or (GC=F) à {rv}, prix ${p:,} — "
                + ("or suracheté à court terme, prudence." if rv > 65
                   else "or survendu, excellente opportunité d'accumulation." if rv < 35
                   else "zone neutre sur l'or."),
            )

    # ── Ratio Or / Argent ──────────────────────────────────────
    if "gold" in data and "silver" in data:
        gp    = float(data["gold"].iloc[-1])
        sp    = float(data["silver"].iloc[-1])
        ratio = round(gp / sp, 1)
        r_sig = "buy" if ratio > 75 else "sell" if ratio < 55 else "neutral"
        out["goldsil"] = _ind(
            _norm(ratio, 40, 100), f"{ratio}:1", "",
            r_sig,
            f"Ratio Or/Argent à {ratio}:1 (Or ${gp:,.0f} / Argent ${sp:.1f}) — "
            + ("l'argent est historiquement sous-évalué vs l'or (moy. ~65:1). Signal d'achat fort sur l'argent." if ratio > 75
               else "argent surperformant l'or, ratio bas." if ratio < 55
               else "ratio dans la normale historique."),
        )

    # ── RSI Argent ─────────────────────────────────────────────
    if "silver" in data:
        s  = data["silver"]
        rv = _rsi(s)
        if rv is not None:
            out["sivrsi"] = _ind(
                rv, rv, "", _rsi_sig(rv),
                f"RSI Argent (SI=F) à {rv} — "
                + ("argent suracheté." if rv > 65
                   else "argent survendu, opportunité d'accumulation." if rv < 35
                   else "zone neutre sur l'argent."),
            )

    # ── Platine vs Palladium ────────────────────────────────────
    if "platinum" in data and "palladium" in data:
        pt  = round(float(data["platinum"].iloc[-1]))
        pd_ = round(float(data["palladium"].iloc[-1]))
        ratio = round(pt / pd_, 2)
        pp_sig = "buy" if ratio < 1.0 else "neutral" if ratio < 1.3 else "sell"
        out["platpall"] = _ind(
            _norm(ratio, 0.3, 2.0),
            f"Pt ${pt:,} / Pd ${pd_:,}", "",
            pp_sig,
            f"Platine à ${pt:,} vs Palladium à ${pd_:,} (ratio {ratio}) — "
            + ("platine à forte décote, potentiel de rattrapage historique important." if ratio < 1.0
               else "ratio normalisé, spread réduit." if ratio < 1.3
               else "platine à prime sur le palladium."),
        )

    # ── Cuivre ─────────────────────────────────────────────────
    if "copper" in data:
        s  = data["copper"]
        rv = _rsi(s)
        p  = round(float(s.iloc[-1]), 2)
        if rv is not None:
            c_sig = _rsi_sig(rv)
            out["copper"] = _ind(
                rv, f"${p}/lb", "",
                c_sig,
                f"Cuivre (HG=F) à ${p}/lb, RSI {rv} — "
                + ("demande structurelle forte (IA, transition énergétique, EVs). " if rv < 65 else "")
                + ("surachat à court terme." if rv > 65
                   else "survente, opportunité sur fond de demande structurelle croissante." if rv < 35
                   else "demande à long terme soutenue par la transition énergétique."),
            )

    # ── Uranium ETF (URA) comme proxy ──────────────────────────
    if "uranium" in data:
        s  = data["uranium"]
        rv = _rsi(s)
        p  = round(float(s.iloc[-1]), 2)
        if rv is not None:
            u_sig = _rsi_sig(rv)
            out["ursi"] = _ind(
                rv, rv, "", u_sig,
                f"RSI URA ETF (proxy uranium) à {rv}, prix ${p} — "
                + ("suracheté à court terme." if rv > 65
                   else "survendu, opportunité sur fond de déficit structurel." if rv < 35
                   else "zone neutre, tendance haussière long terme intacte."),
            )
            out["uspot"] = _ind(
                _norm(p, 15, 60), f"${p}", " (URA ETF)",
                "buy" if p > 25 else "neutral",
                f"URA ETF à ${p} (proxy du marché uranium) — "
                + ("déficit structurel offre/demande persistant jusqu'à 2030+." if p > 25
                   else "marché en consolidation."),
            )

    return out


# ══════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════

def get_all() -> dict:
    return {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "bourse":   _bourse(),
        "matieres": _matieres(),
    }
