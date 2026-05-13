"""
MarketSense — indicators.py  v2
Sources :
  - yfinance         : RSI, MACD, Bollinger, ATR, MA, Stochastique, P/E, prix
  - multpl.com       : Shiller CAPE (scrape)
  - FRED API         : CPI YoY, taux réels TIPS 10y (clé gratuite recommandée)
  - Binance API      : Funding Rate BTC (public, sans clé)
  - CBOE             : Put/Call Ratio equity (CSV public)
  - AAII             : Sentiment bullish/bearish (scrape hebdo)
  - Log-régression   : Rainbow Chart BTC (calcul interne sur prix yfinance)
"""

import io
import math
import datetime as dt
import os
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from bs4 import BeautifulSoup

FRED_KEY = os.getenv("FRED_API_KEY", "")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
TIMEOUT = 12


# ══════════════════════════════════════════════════════════════════
# HELPERS — CALCULS TECHNIQUES
# ══════════════════════════════════════════════════════════════════

def _rsi(series: pd.Series, period: int = 14) -> float | None:
    if len(series) < period + 1:
        return None
    delta = series.diff().dropna()
    gain  = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return round(float((100 - 100 / (1 + rs)).iloc[-1]))


def _macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast    = series.ewm(span=fast,   adjust=False).mean()
    ema_slow    = series.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1])


def _stochastic(hist: pd.DataFrame, k=14, d=3) -> tuple[float, float] | None:
    """Stochastique %K et %D."""
    if len(hist) < k + d:
        return None
    low_min  = hist["Low"].rolling(k).min()
    high_max = hist["High"].rolling(k).max()
    pct_k    = 100 * (hist["Close"] - low_min) / (high_max - low_min)
    pct_d    = pct_k.rolling(d).mean()
    return round(float(pct_k.iloc[-1])), round(float(pct_d.iloc[-1]))


def _bollinger_pct(series: pd.Series, window=20) -> float | None:
    if len(series) < window:
        return None
    ma    = series.rolling(window).mean()
    std   = series.rolling(window).std()
    upper = (ma + 2 * std).iloc[-1]
    lower = (ma - 2 * std).iloc[-1]
    pct   = (series.iloc[-1] - lower) / (upper - lower) * 100
    return round(max(0.0, min(100.0, pct)))


def _atr_pct(hist: pd.DataFrame, period=14) -> float | None:
    if len(hist) < period + 1:
        return None
    high, low, close = hist["High"], hist["Low"], hist["Close"]
    tr  = pd.concat([high - low,
                     (high - close.shift()).abs(),
                     (low  - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return round(atr / close.iloc[-1] * 100, 2)


def _rainbow_zone(btc_price: float) -> dict | None:
    """
    Rainbow Chart BTC — log-régression sur les jours depuis le genesis block.
    Formule de référence : log10(price) ≈ 5.84 × log10(days) − 17.01
    Zones 1 (fire sale) → 9 (maximum bubble).
    """
    try:
        genesis = dt.date(2009, 1, 3)
        days    = (dt.date.today() - genesis).days
        if days <= 0:
            return None

        # Ligne centrale de la régression
        log_mid   = 5.84 * math.log10(days) - 17.01
        price_mid = 10 ** log_mid

        # Écart log du prix actuel par rapport à la ligne centrale
        log_ratio = math.log10(btc_price / price_mid) if price_mid > 0 else 0

        # Mapping vers les 9 zones (chaque zone ≈ 0.27 d'écart log)
        BAND = 0.27
        if   log_ratio >  4 * BAND: zone = 9   # Maximum Bubble Territory
        elif log_ratio >  3 * BAND: zone = 8   # Sell. Seriously, SELL!
        elif log_ratio >  2 * BAND: zone = 7   # FOMO intensifies
        elif log_ratio >  1 * BAND: zone = 6   # Is this a bubble?
        elif log_ratio >  0:        zone = 5   # HOLD!
        elif log_ratio > -1 * BAND: zone = 4   # Still cheap
        elif log_ratio > -2 * BAND: zone = 3   # Accumulate
        elif log_ratio > -3 * BAND: zone = 2   # BUY!
        else:                       zone = 1   # Basically a fire sale

        LABELS = {
            9: ("Maximum Bubble", "sell"),
            8: ("Vendre maintenant", "sell"),
            7: ("FOMO — prudence", "sell"),
            6: ("Possible bulle ?", "neutral"),
            5: ("Conserver (HOLD)", "neutral"),
            4: ("Encore bon marché", "buy"),
            3: ("Accumuler", "buy"),
            2: ("Acheter !", "buy"),
            1: ("Soldes exceptionnelles", "buy"),
        }
        label, sig = LABELS[zone]

        # Position sur la barre 0-100 : zone 1 → 5%, zone 9 → 95%
        val = round(5 + (zone - 1) * 90 / 8)

        return {
            "val":  val,
            "raw":  f"Zone {zone}/9",
            "unit": f" — {label}",
            "sig":  sig,
            "desc": (
                f"Rainbow Chart BTC — Zone {zone}/9 ({label}). "
                f"Prix actuel : ${btc_price:,.0f} vs régression centrale : ${price_mid:,.0f}. "
                + ("Historiquement associé aux sommets de cycle, prudence maximale." if zone >= 7
                   else "Zone de distribution, envisager de prendre des bénéfices." if zone == 6
                   else "Zone neutre selon le modèle logarithmique." if zone == 5
                   else "Zone d'accumulation favorable selon le modèle de cycle BTC.")
            ),
        }
    except Exception as e:
        print(f"[Rainbow] {e}")
        return None


# ══════════════════════════════════════════════════════════════════
# HELPERS — REQUÊTES EXTERNES
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
        r   = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        obs = r.json().get("observations", [])
        return [float(o["value"]) for o in obs if o["value"] != "."]
    except Exception as e:
        print(f"[FRED] {series_id}: {e}")
        return None


def _scrape_cape() -> float | None:
    try:
        r    = requests.get("https://www.multpl.com/shiller-pe",
                            headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        el   = soup.select_one("#current-value")
        if el:
            return float(el.text.strip().replace(",", "."))
    except Exception as e:
        print(f"[scrape] CAPE: {e}")
    return None


def _cboe_put_call() -> float | None:
    """
    Ratio Put/Call equity quotidien publié par le CBOE.
    URL publique sans authentification.
    """
    url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/EQUITY_PC_RATIO_History.csv"
    try:
        r    = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        df   = pd.read_csv(io.StringIO(r.text))
        # Dernière colonne = ratio, dernière ligne = plus récente
        ratio = float(df.iloc[-1, -1])
        return round(ratio, 2)
    except Exception as e:
        print(f"[CBOE] Put/Call: {e}")
    # Fallback : essayer l'ancien chemin
    try:
        url2 = "https://www.cboe.com/publish/scheduledtask/mktdata/datahouse/equitypc.csv"
        r    = requests.get(url2, headers=HEADERS, timeout=TIMEOUT)
        # Le fichier a un en-tête sur 2 lignes
        df   = pd.read_csv(io.StringIO(r.text), skiprows=2, header=None)
        ratio = float(df.iloc[-1, -1])
        return round(ratio, 2)
    except Exception as e2:
        print(f"[CBOE] Put/Call fallback: {e2}")
    return None


def _aaii_sentiment() -> dict | None:
    """
    Sentiment hebdomadaire AAII (American Association of Individual Investors).
    Scrape la page publique.
    """
    try:
        r    = requests.get("https://www.aaii.com/sentimentsurvey",
                            headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")

        # Les pourcentages sont dans des éléments avec classes spécifiques
        # Structure typique : Bullish / Neutral / Bearish en %
        texts = soup.get_text()

        # Chercher les patterns "XX.X% Bullish" etc.
        import re
        bullish  = re.search(r"([\d.]+)%\s*Bullish",  texts, re.I)
        bearish  = re.search(r"([\d.]+)%\s*Bearish",  texts, re.I)
        neutral_ = re.search(r"([\d.]+)%\s*Neutral",  texts, re.I)

        if bullish and bearish:
            b  = float(bullish.group(1))
            br = float(bearish.group(1))
            n  = float(neutral_.group(1)) if neutral_ else round(100 - b - br, 1)
            return {"bullish": b, "bearish": br, "neutral": n}
    except Exception as e:
        print(f"[AAII] scrape: {e}")
    return None


def _binance_funding_rate(symbol: str = "BTCUSDT") -> float | None:
    """
    Funding Rate des contrats perpétuels Binance.
    API publique, sans clé requise.
    """
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1"
    try:
        r    = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        data = r.json()
        if data and isinstance(data, list):
            return float(data[0]["fundingRate"])
        # Parfois c'est un dict direct
        if isinstance(data, dict) and "fundingRate" in data:
            return float(data["fundingRate"])
    except Exception as e:
        print(f"[Binance] funding rate: {e}")
    return None


# ══════════════════════════════════════════════════════════════════
# HELPERS — NORMALISATION
# ══════════════════════════════════════════════════════════════════

def _norm(v: float, lo: float, hi: float) -> int:
    return int(max(0, min(100, (v - lo) / (hi - lo) * 100)))

def _rsi_sig(v: float, buy=35, sell=65) -> str:
    return "buy" if v < buy else "sell" if v > sell else "neutral"

def _ind(val, raw, unit, sig, desc) -> dict:
    return {"val": int(val), "raw": str(raw), "unit": unit, "sig": sig, "desc": desc}


# ══════════════════════════════════════════════════════════════════
# BOURSE
# ══════════════════════════════════════════════════════════════════

def _etf_block(ticker: str, prefix: str, label: str) -> dict:
    """
    Génère RSI, MACD, MM50, MM200, Stochastique et variation 1 an pour un ETF.
    prefix  : identifiant court (ex. 'urth', 'eem', 'aaxj')
    label   : nom affiché (ex. 'MSCI World (URTH)')
    """
    out = {}
    try:
        hist  = yf.Ticker(ticker).history(period="2y")
        if hist.empty:
            print(f"[etf] {ticker} : données vides")
            return out

        close = hist["Close"]
        price = float(close.iloc[-1])
        ma50  = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])

        above50  = price > ma50
        above200 = price > ma200
        pct200   = round((price / ma200 - 1) * 100, 1)

        # RSI 14j
        rsi_v = _rsi(close)
        if rsi_v is not None:
            out[f"rsi_{prefix}"] = _ind(
                rsi_v, rsi_v, "", _rsi_sig(rsi_v),
                f"RSI {label} à {rsi_v} — "
                + ("suracheté, risque de correction à court terme." if rsi_v > 70
                   else "survendu — opportunité d'achat potentielle." if rsi_v < 30
                   else "zone neutre, momentum équilibré."),
            )

        # MACD
        macd_v, signal_v = _macd(close)
        bull = macd_v > signal_v
        out[f"macd_{prefix}"] = _ind(
            72 if bull else 28, "Positif" if bull else "Négatif", "",
            "buy" if bull else "sell",
            f"MACD {label} {'au-dessus' if bull else 'sous'} sa ligne de signal — "
            f"tendance {'haussière' if bull else 'baissière'} confirmée.",
        )

        # Prix vs MM50
        out[f"mm50_{prefix}"] = _ind(
            78 if above50 else 25,
            f"{'Au-dessus' if above50 else 'En dessous'} ({int(ma50)})", "",
            "buy" if above50 else "sell",
            f"{label} {'au-dessus' if above50 else 'en dessous'} de sa MM50 ({int(ma50)}) — "
            f"tendance court terme {'haussière.' if above50 else 'baissière.'}",
        )

        # Prix vs MM200
        out[f"mm200_{prefix}"] = _ind(
            83 if above200 else 20,
            f"{'+ ' if pct200 >= 0 else ''}{pct200} %", "",
            "buy" if above200 else "sell",
            f"{label} {'au-dessus' if above200 else 'en dessous'} de sa MM200 ({int(ma200)}) "
            f"de {abs(pct200)} % — tendance long terme {'haussière.' if above200 else 'baissière.'}",
        )

        # Stochastique
        stoch = _stochastic(hist)
        if stoch:
            k_val, d_val = stoch
            s_sig = "sell" if k_val > 80 else "buy" if k_val < 20 else "neutral"
            out[f"stoch_{prefix}"] = _ind(
                k_val, f"%K {k_val} / %D {d_val}", "",
                s_sig,
                f"Stochastique {label} : %K={k_val}, %D={d_val} — "
                + ("suracheté (> 80)." if k_val > 80
                   else "survendu (< 20), rebond potentiel." if k_val < 20
                   else "zone neutre."),
            )

    except Exception as e:
        print(f"[etf] {ticker}: {e}")
    return out
    out = {}

    # ── S&P 500 — données de base ────────────────────────────────
    hist  = None
    close = None
    price = None
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

        # Stochastique %K/%D
        stoch = _stochastic(hist)
        if stoch:
            k_val, d_val = stoch
            s_sig = "sell" if k_val > 80 else "buy" if k_val < 20 else "neutral"
            out["stoch"] = _ind(
                k_val, f"%K {k_val} / %D {d_val}", "",
                s_sig,
                f"Stochastique S&P 500 : %K={k_val}, %D={d_val} — "
                + ("suracheté (> 80), signal de retournement possible." if k_val > 80
                   else "survendu (< 20), rebond potentiel." if k_val < 20
                   else "zone neutre, pas de signal extrême."),
            )

        # MM50 vs Prix
        above50 = price > ma50
        out["mm50"] = _ind(
            78 if above50 else 25,
            f"{'Au-dessus' if above50 else 'En dessous'} ({int(ma50):,})", "",
            "buy" if above50 else "sell",
            f"S&P 500 à {int(price):,} {'au-dessus' if above50 else 'en dessous'} "
            f"de sa MM50 ({int(ma50):,}) — tendance court terme {'haussière.' if above50 else 'baissière.'}",
        )

        # MM200 vs Prix
        above200 = price > ma200
        out["mm200"] = _ind(
            83 if above200 else 20,
            f"{'Au-dessus' if above200 else 'En dessous'} ({int(ma200):,})", "",
            "buy" if above200 else "sell",
            f"S&P 500 à {int(price):,} {'au-dessus' if above200 else 'en dessous'} "
            f"de sa MM200 ({int(ma200):,}) — tendance long terme {'haussière.' if above200 else 'baissière.'}",
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
                   else "proche de la bande basse, rebond probable." if boll < 20
                   else "zone médiane, compression de volatilité."),
            )

        # ATR
        atr = _atr_pct(hist)
        if atr is not None:
            a_sig = "sell" if atr > 2.5 else "buy" if atr < 0.8 else "neutral"
            out["atr"] = _ind(
                _norm(atr, 0.5, 3.0), f"{atr} % du prix", "",
                a_sig,
                f"ATR à {atr} % du prix — "
                + ("volatilité élevée, marché nerveux." if atr > 2.5
                   else "faible volatilité, conditions calmes." if atr < 0.8
                   else "volatilité dans la moyenne historique."),
            )

    except Exception as e:
        print(f"[bourse] S&P 500: {e}")

    # ── VIX ──────────────────────────────────────────────────────
    try:
        vix_price = round(float(yf.Ticker("^VIX").history(period="5d")["Close"].iloc[-1]), 1)
        v_sig = "sell" if vix_price > 30 else "buy" if vix_price < 15 else "neutral"
        out["vix"] = _ind(
            _norm(vix_price, 10, 50), vix_price, "",
            v_sig,
            f"VIX à {vix_price} — "
            + ("marché très anxieux, opportunité contrariante." if vix_price > 30
               else "complacence élevée, méfiance vis-à-vis des chocs." if vix_price < 15
               else "volatilité modérée, marché relativement serein."),
        )
    except Exception as e:
        print(f"[bourse] VIX: {e}")

    # ── Shiller CAPE ─────────────────────────────────────────────
    try:
        cape = _scrape_cape()
        if cape:
            c_sig = "sell" if cape > 30 else "buy" if cape < 15 else "neutral"
            out["cape"] = _ind(
                _norm(cape, 10, 45), round(cape, 1), "x",
                c_sig,
                f"Shiller CAPE à {round(cape,1)}x (multpl.com) — "
                + ("marchés fortement surévalués (moy. historique ~16x). Prudence long terme." if cape > 30
                   else "valorisation attrayante." if cape < 15
                   else "valorisation dans la moyenne historique."),
            )
    except Exception as e:
        print(f"[bourse] CAPE: {e}")

    # ── P/E Trailing (SPY comme proxy S&P 500) ───────────────────
    try:
        spy_info = yf.Ticker("SPY").fast_info
        # fast_info peut avoir trailing_pe selon les versions de yfinance
        pe = getattr(spy_info, "trailing_pe", None)
        if pe is None:
            # Fallback : calculer approximativement depuis le prix et EPS estimé
            spy_price = getattr(spy_info, "last_price", None)
            # On ne peut pas calculer sans EPS → on passe
            raise ValueError("trailing_pe indisponible")
        pe = round(float(pe), 1)
        p_sig = "sell" if pe > 25 else "buy" if pe < 14 else "neutral"
        out["pe_fwd"] = _ind(
            _norm(pe, 10, 35), pe, "x",
            p_sig,
            f"P/E Trailing S&P 500 (SPY) à {pe}x — "
            + ("valorisation élevée par rapport à la moyenne historique (17-18x)." if pe > 25
               else "valorisation attrayante, potentiel de hausse long terme." if pe < 14
               else "valorisation dans la normale historique."),
        )
    except Exception as e:
        print(f"[bourse] P/E: {e}")

    # ── Put/Call Ratio (CBOE) ────────────────────────────────────
    try:
        pc = _cboe_put_call()
        if pc is not None:
            # Ratio bas (< 0.7) = excès d'optimisme (mauvais signal)
            # Ratio haut (> 1.0) = excès de peur (bon signal contrariant)
            pc_sig = "buy" if pc > 1.0 else "sell" if pc < 0.7 else "neutral"
            pc_norm = _norm(pc, 0.4, 1.4)
            out["putcall"] = _ind(
                pc_norm, pc, "",
                pc_sig,
                f"Ratio Put/Call equity (CBOE) à {pc} — "
                + ("excès de peur, opportunité contrariante d'achat." if pc > 1.0
                   else "excès d'optimisme, méfiance recommandée." if pc < 0.7
                   else "équilibre put/call, sentiment neutre."),
            )
    except Exception as e:
        print(f"[bourse] Put/Call: {e}")

    # ── AAII Sentiment ───────────────────────────────────────────
    try:
        aaii = _aaii_sentiment()
        if aaii:
            b = aaii["bullish"]
            br = aaii["bearish"]
            # > 50% bulls = euphorie, < 25% = pessimisme extrême (opportunité)
            a_sig = "sell" if b > 50 else "buy" if b < 25 else "neutral"
            out["aaii"] = _ind(
                round(b), f"{b:.0f} % bulls / {br:.0f} % bears", "",
                a_sig,
                f"AAII Sentiment : {b:.0f} % haussiers, {br:.0f} % baissiers — "
                + ("euphorie excessive (> 50% bulls), signal de prudence." if b > 50
                   else "pessimisme extrême (< 25% bulls), opportunité contrariante." if b < 25
                   else "sentiment équilibré, dans la normale historique."),
            )
    except Exception as e:
        print(f"[bourse] AAII: {e}")

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
                   else "taux modérés, impact neutre."),
            )
    except Exception as e:
        print(f"[bourse] TIPS: {e}")

    # ── CPI YoY (FRED) ───────────────────────────────────────────
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
                   else "inflation très basse, impact réduit sur les matières premières." if yoy < 1
                   else "inflation modérée."),
            )
    except Exception as e:
        print(f"[bourse] CPI: {e}")

    # ── ETFs mondiaux (Amundi, Euronext Paris) ────────────────────
    for ticker, prefix, label in [
        ("CW8.PA",   "cw8",   "MSCI World (CW8)"),
        ("ESE.PA",   "ese",   "S&P 500 ETF (ESE)"),
        ("PAEEM.PA", "paeem", "Marchés Émergents (PAEEM)"),
        ("PAASI.PA", "paasi", "Asie Pac. ex-Japon (PAASI)"),
    ]:
        out.update(_etf_block(ticker, prefix, label))

    return out


# ══════════════════════════════════════════════════════════════════
# CRYPTO — indicateurs backend (complète le frontend CoinGecko/Alternative.me)
# Sources : Bitbo API (public) · Binance API · yfinance
# ══════════════════════════════════════════════════════════════════

BITBO_KEY = os.getenv("BITBO_API_KEY", "")   # optionnel — fonctionne sans clé

def _bitbo(endpoint: str) -> float | None:
    """
    Bitbo Charts public API — 5 req/min, 150k/mois.
    Docs : https://bitbo.io/api/docs/category/endpoints
    """
    url = f"https://charts.bitbo.io/api/v1/{endpoint}/?latest=true"
    hdrs = {**HEADERS}
    if BITBO_KEY:
        hdrs["Authorization"] = f"Bearer {BITBO_KEY}"
    try:
        r = requests.get(url, headers=hdrs, timeout=TIMEOUT)
        if r.status_code == 429:
            print(f"[Bitbo] rate-limit sur {endpoint}")
            return None
        data = r.json()
        rows = data.get("data", [])
        if rows:
            return float(rows[-1][1])
    except Exception as e:
        print(f"[Bitbo] {endpoint}: {e}")
    return None


# ══════════════════════════════════════════════════════════════════
# DONNÉES STATIQUES — CYCLES HALVING
# ══════════════════════════════════════════════════════════════════

HALVINGS = [
    {"n": 1, "date": "2012-11-28", "price_at":    12, "peak":   1_163, "peak_date": "2013-11-30", "gain": "+9 600 %"},
    {"n": 2, "date": "2016-07-09", "price_at":   650, "peak":  19_891, "peak_date": "2017-12-17", "gain": "+2 960 %"},
    {"n": 3, "date": "2020-05-11", "price_at": 8_600, "peak":  68_789, "peak_date": "2021-11-10", "gain":   "+700 %"},
    {"n": 4, "date": "2024-04-20", "price_at": 63_800, "peak": None,   "peak_date": None,         "gain": "en cours"},
]
NEXT_HALVING_DATE = dt.date(2028, 4, 17)   # estimation bloc 1 050 000

# ══════════════════════════════════════════════════════════════════
# INDICATEURS SOCIAUX
# ══════════════════════════════════════════════════════════════════

def _google_trends_bitcoin() -> dict | None:
    """
    Intérêt de recherche Google pour 'Bitcoin' sur 90 jours.
    Retourne la valeur actuelle (0-100) et la moyenne sur la période.
    Nécessite pytrends.
    """
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=0, timeout=(10, 30), retries=1, backoff_factor=0.5)
        pt.build_payload(["Bitcoin"], timeframe="today 3-m", geo="")
        df = pt.interest_over_time()
        if df is None or df.empty:
            return None
        current = int(df["Bitcoin"].iloc[-1])
        avg_90d = int(df["Bitcoin"].mean())
        peak_90d = int(df["Bitcoin"].max())
        return {"current": current, "avg_90d": avg_90d, "peak_90d": peak_90d}
    except Exception as e:
        print(f"[social] Google Trends: {e}")
        return None


def _appstore_ranking(app_id: str, country: str = "us") -> int | None:
    """
    Classement App Store d'une app via l'API iTunes.
    Retourne le classement dans la catégorie Finance (top 100).
    """
    try:
        # Récupère les 100 meilleures apps Finance (top grossing)
        url = f"https://itunes.apple.com/{country}/rss/topgrossingapplications/limit=100/genre=6015/json"
        r   = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        entries = r.json()["feed"]["entry"]
        for i, entry in enumerate(entries, 1):
            if entry["id"]["attributes"]["im:id"] == app_id:
                return i
        return None   # pas dans le top 100
    except Exception as e:
        print(f"[social] App Store {app_id}: {e}")
        return None


def _halving_indicators() -> dict:
    """
    Calcule tous les indicateurs liés aux halvings Bitcoin :
    - jours depuis le dernier halving
    - jours jusqu'au prochain halving
    - % du cycle écoulé
    - contexte historique des cycles précédents
    """
    out  = {}
    today      = dt.date.today()
    last_h     = HALVINGS[-1]
    last_date  = dt.date.fromisoformat(last_h["date"])
    days_since = (today - last_date).days
    days_until = (NEXT_HALVING_DATE - today).days
    cycle_len  = (NEXT_HALVING_DATE - last_date).days   # ~1 461 j
    pct_cycle  = round(days_since / cycle_len * 100, 1)

    # ── Jours depuis le dernier halving ──────────────────────────
    # Phase du cycle : < 365j = early, < 730j = mid, < 1095j = late
    if days_since < 365:
        phase, phase_sig = "Phase précoce (< 1 an)", "buy"
    elif days_since < 730:
        phase, phase_sig = "Phase intermédiaire (1-2 ans)", "neutral"
    elif days_since < 1095:
        phase, phase_sig = "Phase tardive (2-3 ans)", "sell"
    else:
        phase, phase_sig = "Fin de cycle (> 3 ans)", "sell"

    hist_lines = " | ".join(
        f"H{h['n']} ({h['date'][:4]}) : ${h['price_at']:,} → "
        + (f"pic ${h['peak']:,}" if h['peak'] else "en cours")
        + f" ({h['gain']})"
        for h in HALVINGS
    )

    out["days_since_halving"] = _ind(
        _norm(days_since, 0, cycle_len),
        f"{days_since} jours", f" ({pct_cycle} % du cycle)",
        phase_sig,
        f"Halving 4 le {last_h['date']} — {days_since} jours écoulés "
        f"({pct_cycle} % du cycle de ~{cycle_len} j). {phase}. "
        f"Historique : {hist_lines}",
    )

    # ── Jours jusqu'au prochain halving ──────────────────────────
    days_util_pct = round(days_until / cycle_len * 100)
    dh_sig = "buy" if days_until > 900 else "neutral" if days_until > 400 else "sell"
    out["days_until_halving"] = _ind(
        100 - _norm(days_until, 0, cycle_len),
        f"{max(0, days_until)} jours", f" (~{NEXT_HALVING_DATE})",
        dh_sig,
        f"Prochain halving estimé autour du {NEXT_HALVING_DATE} "
        f"(bloc 1 050 000) — dans ~{max(0, days_until)} jours. "
        f"Historiquement, les 12-18 mois précédant un halving sont haussiers.",
    )

    # ── Progression du cycle ──────────────────────────────────────
    out["cycle_progress"] = _ind(
        int(pct_cycle),
        f"{pct_cycle} %", f" du cycle H4",
        phase_sig,
        f"Cycle post-halving H4 à {pct_cycle} % — {phase}. "
        f"Les cycles précédents ont duré en moyenne 3,5 ans avec un pic à ~12-18 mois. "
        f"Modèle de rendement décroissant : H1 +9600 %, H2 +2960 %, H3 +700 %.",
    )

    return out


def _social_indicators() -> dict:
    """Agrège tous les indicateurs sociaux crypto."""
    out = {}

    # ── Google Trends — 'Bitcoin' ─────────────────────────────────
    trends = _google_trends_bitcoin()
    if trends:
        v   = trends["current"]
        avg = trends["avg_90d"]
        pk  = trends["peak_90d"]
        # Intérêt > 75 = FOMO retail (signal de prudence)
        # Intérêt < 25 = désintérêt total (signal d'accumulation)
        t_sig = "sell" if v > 75 else "buy" if v < 25 else "neutral"
        out["google_trends"] = _ind(
            v, v, "/100",
            t_sig,
            f"Google Trends 'Bitcoin' à {v}/100 (moy. 90j : {avg}, pic 90j : {pk}) — "
            + ("intérêt très élevé, signal de FOMO retail souvent associé aux sommets." if v > 75
               else "intérêt très faible, désintérêt du grand public — zone d'accumulation historique." if v < 25
               else f"intérêt {'supérieur' if v > avg else 'inférieur'} à la moyenne des 90 derniers jours."),
        )

    # ── Classement App Store — Coinbase & Binance ─────────────────
    # Coinbase : 886427730, Binance : 1436799971
    coinbase_rank = _appstore_ranking("886427730", "us")
    binance_rank  = _appstore_ranking("1436799971", "us")

    if coinbase_rank is not None:
        # Classement bas (top 10 Finance) = euphorie retail
        cb_sig = "sell" if coinbase_rank <= 5 else "buy" if coinbase_rank > 50 else "neutral"
        out["coinbase_rank"] = _ind(
            _norm(101 - coinbase_rank, 1, 100),
            f"#{coinbase_rank}", " Finance App Store",
            cb_sig,
            f"Coinbase #{coinbase_rank} App Store Finance (US) — "
            + (f"top 5 ! Afflux massif de retail, souvent associé aux pics de marché." if coinbase_rank <= 5
               else f"classement modéré, pas d'euphorie retail détectée." if coinbase_rank > 20
               else f"intérêt retail soutenu."),
        )

    if binance_rank is not None:
        bn_sig = "sell" if binance_rank <= 5 else "buy" if binance_rank > 50 else "neutral"
        out["binance_rank"] = _ind(
            _norm(101 - binance_rank, 1, 100),
            f"#{binance_rank}", " Finance App Store",
            bn_sig,
            f"Binance #{binance_rank} App Store Finance (US) — "
            + (f"dans le top 5, signal d'euphorie retail possible." if binance_rank <= 5
               else f"classement normal, pas de FOMO détecté."),
        )

    # ── Indicateurs halving ───────────────────────────────────────
    out.update(_halving_indicators())

    return out


def _altcoin_block(
    ticker_yf: str,
    cg_id: str,
    prefix: str,
    label: str,
    btc_close: "pd.Series | None" = None,
) -> dict:
    """
    Génère RSI, MACD, MM50, MM200, Bollinger, ATR et perf vs BTC
    pour n'importe quel altcoin.
    Essaie yfinance d'abord, puis CoinGecko en fallback.
    """
    out   = {}
    close = None
    hist  = None

    # ── 1. yfinance ──────────────────────────────────────────────
    try:
        h = yf.Ticker(ticker_yf).history(period="2y")
        if not h.empty:
            hist  = h
            close = h["Close"]
    except Exception as e:
        print(f"[altcoin] yfinance {ticker_yf}: {e}")

    # ── 2. CoinGecko (fallback ou token récent) ───────────────────
    if (close is None or len(close) < 30) and cg_id:
        try:
            url = (
                f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart"
                f"?vs_currency=usd&days=365&interval=daily"
            )
            r    = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            rows = r.json().get("prices", [])
            if rows:
                close = pd.Series([p[1] for p in rows])
                hist  = None   # pas de OHLC, seulement close
        except Exception as e:
            print(f"[altcoin] CoinGecko {cg_id}: {e}")

    if close is None or len(close) < 20:
        print(f"[altcoin] Pas assez de données pour {label}")
        return out

    price = float(close.iloc[-1])

    # ── RSI ───────────────────────────────────────────────────────
    rsi_v = _rsi(close)
    if rsi_v is not None:
        out[f"rsi_{prefix}"] = _ind(
            rsi_v, rsi_v, "", _rsi_sig(rsi_v),
            f"RSI {label} à {rsi_v} — "
            + ("suracheté, risque de correction." if rsi_v > 70
               else "survendu — opportunité potentielle." if rsi_v < 30
               else "zone neutre, momentum équilibré."),
        )

    # ── MACD ──────────────────────────────────────────────────────
    if len(close) >= 26:
        mv, sv = _macd(close)
        bull   = mv > sv
        out[f"macd_{prefix}"] = _ind(
            72 if bull else 28, "Positif" if bull else "Négatif", "",
            "buy" if bull else "sell",
            f"MACD {label} {'positif — tendance haussière confirmée.' if bull else 'négatif — tendance baissière en cours.'}",
        )

    # ── MM50 ──────────────────────────────────────────────────────
    if len(close) >= 50:
        ma50    = float(close.rolling(50).mean().iloc[-1])
        above50 = price > ma50
        pct50   = round((price / ma50 - 1) * 100, 1)
        out[f"mm50_{prefix}"] = _ind(
            78 if above50 else 25,
            f"{'+ ' if pct50 >= 0 else ''}{pct50} %", "",
            "buy" if above50 else "sell",
            f"{label} {'au-dessus' if above50 else 'en dessous'} de sa MM50 "
            f"({'+' if pct50 >= 0 else ''}{pct50} %) — tendance court terme {'haussière.' if above50 else 'baissière.'}",
        )

    # ── MM200 ─────────────────────────────────────────────────────
    if len(close) >= 200:
        ma200    = float(close.rolling(200).mean().iloc[-1])
        above200 = price > ma200
        pct200   = round((price / ma200 - 1) * 100, 1)
        out[f"mm200_{prefix}"] = _ind(
            83 if above200 else 20,
            f"{'+ ' if pct200 >= 0 else ''}{pct200} %", "",
            "buy" if above200 else "sell",
            f"{label} {'au-dessus' if above200 else 'en dessous'} de sa MM200 "
            f"({'+' if pct200 >= 0 else ''}{pct200} %) — tendance long terme {'haussière.' if above200 else 'baissière.'}",
        )

    # ── Bollinger Bands ───────────────────────────────────────────
    boll = _bollinger_pct(close)
    if boll is not None:
        b_sig = "sell" if boll > 80 else "buy" if boll < 20 else "neutral"
        out[f"bollinger_{prefix}"] = _ind(
            boll, f"{boll} %", "",
            b_sig,
            f"Bollinger {label} à {boll} % — "
            + ("proche bande haute, risque de retournement." if boll > 80
               else "proche bande basse, rebond possible." if boll < 20
               else "zone centrale, compression de volatilité."),
        )

    # ── ATR — volatilité ──────────────────────────────────────────
    if hist is not None:
        atr = _atr_pct(hist)
        if atr is not None:
            a_sig = "sell" if atr > 5 else "neutral"
            out[f"atr_{prefix}"] = _ind(
                _norm(atr, 1, 10), f"{atr} %", " du prix",
                a_sig,
                f"ATR {label} à {atr} % du prix — "
                + ("volatilité très élevée, risque de grands mouvements." if atr > 5
                   else "volatilité modérée pour un actif crypto."),
            )

    # ── Performance vs BTC (90j) ──────────────────────────────────
    if btc_close is not None and len(btc_close) >= 10 and len(close) >= 10:
        try:
            n       = min(90, len(close), len(btc_close))
            alt_ret = float(close.iloc[-1]) / float(close.iloc[-n]) - 1
            btc_ret = float(btc_close.iloc[-1]) / float(btc_close.iloc[-n]) - 1
            alpha   = round((alt_ret - btc_ret) * 100, 1)
            r_sig   = "buy" if alpha > 10 else "sell" if alpha < -15 else "neutral"
            out[f"vs_btc_{prefix}"] = _ind(
                _norm(alpha, -40, 40),
                f"{'+ ' if alpha >= 0 else ''}{alpha} %", " vs BTC (90j)",
                r_sig,
                f"{label} {'surperforme' if alpha >= 0 else 'sous-performe'} Bitcoin "
                f"de {abs(alpha)} % sur 90 jours.",
            )
        except Exception as e:
            print(f"[altcoin] vs_btc {prefix}: {e}")

    return out



    out = {}

    # ── Fetch BTC price + historique (yfinance) ──────────────────
    btc_hist  = None
    btc_price = None
    btc_close = None
    try:
        btc_hist  = yf.Ticker("BTC-USD").history(period="max")
        btc_close = btc_hist["Close"]
        btc_price = float(btc_close.iloc[-1])
    except Exception as e:
        print(f"[crypto] BTC yfinance: {e}")

    # ── Appels Bitbo en parallèle (thread pool pour respecter 5 req/min) ─
    import concurrent.futures
    bitbo_endpoints = {
        "nupl":   "nupl-ratio",
        "mvrv":   "mvrv-zscore",
        "puell":  "puell-multiple",
        "sopr":   "sopr",
        "cdd":    "cdd",
        "nvt":    "nvt",
        "mayer":  "mayermultiple",
        "rsim":   "monthly-rsi",
    }
    bitbo_vals: dict[str, float | None] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_bitbo, ep): key for key, ep in bitbo_endpoints.items()}
        for fut in concurrent.futures.as_completed(futures):
            bitbo_vals[futures[fut]] = fut.result()

    # ── NUPL — Net Unrealized Profit/Loss ────────────────────────
    nupl = bitbo_vals.get("nupl")
    if nupl is not None:
        nupl_r  = round(nupl, 3)
        # < 0 = capitulation (achat), 0-0.25 = espoir, 0.25-0.5 = optimisme
        # 0.5-0.75 = croyance/excitation, > 0.75 = euphorie (vente)
        n_sig   = "sell" if nupl > 0.65 else "buy" if nupl < 0.1 else "neutral"
        n_label = ("Euphorie" if nupl > 0.75 else "Excitation" if nupl > 0.5
                   else "Optimisme" if nupl > 0.25 else "Espoir" if nupl > 0.1
                   else "Capitulation")
        out["nupl"] = _ind(
            _norm(nupl, -0.2, 1.0), f"{nupl_r} ({n_label})", "",
            n_sig,
            f"NUPL à {nupl_r} — zone {n_label}. "
            + ("Euphorie : historiquement associée aux sommets de cycle, vente recommandée." if nupl > 0.65
               else "Capitulation : panique généralisée, opportunité d'achat majeure." if nupl < 0.1
               else "Zone intermédiaire, pas de signal extrême."),
        )

    # ── MVRV Z-Score ─────────────────────────────────────────────
    mvrv = bitbo_vals.get("mvrv")
    if mvrv is not None:
        mvrv_r = round(mvrv, 2)
        # > 7 = bulle, < 0 = opportunité d'achat historique
        m_sig  = "sell" if mvrv > 5 else "buy" if mvrv < 0 else "neutral"
        out["mvrv"] = _ind(
            _norm(mvrv, -1, 8), mvrv_r, "",
            m_sig,
            f"MVRV Z-Score à {mvrv_r} (Bitbo) — "
            + ("zone rouge (> 5), distribution probable, sommet de cycle proche." if mvrv > 5
               else "zone verte (< 0), opportunité d'achat historique rare." if mvrv < 0
               else "zone neutre, marché ni surchauffé ni survendu."),
        )

    # ── Puell Multiple ───────────────────────────────────────────
    puell = bitbo_vals.get("puell")
    if puell is not None:
        puell_r = round(puell, 2)
        p_sig   = "sell" if puell > 4 else "buy" if puell < 0.5 else "neutral"
        out["puell"] = _ind(
            _norm(puell, 0.2, 5), puell_r, "",
            p_sig,
            f"Puell Multiple à {puell_r} (Bitbo) — "
            + ("zone de distribution (> 4), mineurs profitables, pression vendeuse." if puell > 4
               else "zone d'accumulation (< 0.5), mineurs sous pression, opportunité." if puell < 0.5
               else "zone neutre, mineurs en bonne santé financière."),
        )

    # ── SOPR — Spent Output Profit Ratio ────────────────────────
    sopr = bitbo_vals.get("sopr")
    if sopr is not None:
        sopr_r = round(sopr, 3)
        # > 1 = vendeurs en profit (distribution), < 1 = vendeurs en perte (capitulation)
        s_sig  = "sell" if sopr > 1.14 else "buy" if sopr < 0.98 else "neutral"
        out["sopr"] = _ind(
            _norm(sopr, 0.95, 1.2), sopr_r, "",
            s_sig,
            f"SOPR à {sopr_r} (Bitbo) — "
            + ("bien au-dessus de 1, vendeurs très profitables, pression distributive." if sopr > 1.14
               else "sous 1, vendeurs en perte — capitulation, opportunité contrariante." if sopr < 0.98
               else "proche de 1, équilibre sain entre vendeurs profitables et en perte."),
        )

    # ── Coin Days Destroyed ──────────────────────────────────────
    cdd = bitbo_vals.get("cdd")
    if cdd is not None:
        # CDD élevé = anciens holders bougent leurs coins (signal de distribution)
        cdd_m   = cdd / 1_000_000
        c_sig   = "sell" if cdd > 21_000_000 else "buy" if cdd < 5_000_000 else "neutral"
        out["cdd"] = _ind(
            min(95, _norm(cdd, 1_000_000, 30_000_000)), f"{cdd_m:.1f}M", "",
            c_sig,
            f"Coin Days Destroyed à {cdd_m:.1f}M (Bitbo) — "
            + ("très élevé : les anciens holders bougent leurs BTC, signal de distribution." if cdd > 21_000_000
               else "faible : les anciens holders conservent, comportement bullish." if cdd < 5_000_000
               else "niveau normal, pas de comportement extrême détecté."),
        )

    # ── NVT Signal ───────────────────────────────────────────────
    nvt = bitbo_vals.get("nvt")
    if nvt is not None:
        nvt_r = round(nvt, 1)
        n_sig  = "sell" if nvt > 150 else "buy" if nvt < 50 else "neutral"
        out["nvt"] = _ind(
            _norm(nvt, 20, 200), nvt_r, "",
            n_sig,
            f"NVT Signal à {nvt_r} (Bitbo) — "
            + ("élevé (> 150) : réseau sous-utilisé par rapport à sa valorisation." if nvt > 150
               else "bas (< 50) : réseau fortement utilisé, valeur fondamentale solide." if nvt < 50
               else "dans la normale, valorisation cohérente avec l'activité réseau."),
        )

    # ── Mayer Multiple (Bitbo ou calcul yfinance) ────────────────
    mayer = bitbo_vals.get("mayer")
    if mayer is None and btc_close is not None and len(btc_close) >= 200:
        # Calcul maison : Prix / MM200
        ma200 = float(btc_close.rolling(200).mean().iloc[-1])
        mayer = round(btc_price / ma200, 2) if ma200 > 0 else None
    if mayer is not None:
        mayer_r = round(mayer, 2)
        # > 2.4 = zone de vente historique (Trace Mayer), < 1 = sous la MM200
        m_sig   = "sell" if mayer > 2.4 else "buy" if mayer < 0.8 else "neutral"
        out["mayer"] = _ind(
            _norm(mayer, 0.4, 3.0), mayer_r, "x",
            m_sig,
            f"Mayer Multiple à {mayer_r}x (Prix BTC / MM200) — "
            + ("zone de vente historique (> 2.4x) selon Trace Mayer." if mayer > 2.4
               else "prix sous la MM200 (< 1x), zone d'accumulation historique." if mayer < 0.8
               else "zone neutre, prix dans la normale par rapport à la MM200."),
        )

    # ── RSI Mensuel (Bitbo ou calcul yfinance monthly) ───────────
    rsim = bitbo_vals.get("rsim")
    if rsim is None and btc_close is not None:
        # Calcul maison sur les clôtures mensuelles
        monthly = btc_close.resample("ME").last()
        rsim    = _rsi(monthly, 14)
    if rsim is not None:
        rsim_r = round(rsim)
        rm_sig  = "sell" if rsim > 90 else "buy" if rsim < 40 else "neutral"
        out["btcrsim"] = _ind(
            rsim_r, rsim_r, "", rm_sig,
            f"RSI Mensuel BTC à {rsim_r} — "
            + ("zone de surachat extrême (> 90), historiquement associé aux sommets de cycle." if rsim > 90
               else "zone de survente (< 40), opportunité d'accumulation long terme." if rsim < 40
               else "zone neutre à modérément haussière."),
        )

    # ── Rainbow Chart (log-régression) ───────────────────────────
    if btc_price:
        rainbow = _rainbow_zone(btc_price)
        if rainbow:
            out["rainbow"] = rainbow

    # ── Funding Rate (Binance) ────────────────────────────────────
    try:
        fr = _binance_funding_rate("BTCUSDT")
        if fr is not None:
            fr_pct = round(fr * 100, 4)
            fr_sig = "sell" if fr_pct > 0.05 else "buy" if fr_pct < -0.01 else "neutral"
            out["funding"] = _ind(
                _norm(fr_pct, -0.05, 0.1), f"{fr_pct:.4f} %", "/8h",
                fr_sig,
                f"Funding Rate BTC perps à {fr_pct:.4f} %/8h (Binance) — "
                + ("excès spéculatif long, risque de liquidations." if fr_pct > 0.05
                   else "marché short-biaié, pression vendeuse extrême." if fr_pct < -0.01
                   else "funding neutre, pas d'excès directionnel."),
            )
    except Exception as e:
        print(f"[crypto] Funding: {e}")

    # ── BTC — Bollinger & ATR (complète l'analyse technique) ───────
    if btc_close is not None and len(btc_close) >= 20:
        boll_btc = _bollinger_pct(btc_close)
        if boll_btc is not None:
            b_sig = "sell" if boll_btc > 80 else "buy" if boll_btc < 20 else "neutral"
            out["bollinger_btc"] = _ind(
                boll_btc, f"{boll_btc} %", "",
                b_sig,
                f"Bollinger BTC à {boll_btc} % — "
                + ("proche bande haute, risque de retournement." if boll_btc > 80
                   else "proche bande basse, rebond possible." if boll_btc < 20
                   else "zone centrale."),
            )
        if btc_hist is not None:
            atr_btc = _atr_pct(btc_hist)
            if atr_btc is not None:
                out["atr_btc"] = _ind(
                    _norm(atr_btc, 1, 6), f"{atr_btc} %", " du prix",
                    "sell" if atr_btc > 4 else "neutral",
                    f"ATR Bitcoin à {atr_btc} % du prix — "
                    + ("volatilité élevée, grands mouvements probables." if atr_btc > 4
                       else "volatilité modérée."),
                )

    # ── ETH, SOL, HYPE ───────────────────────────────────────────
    altcoins = [
        ("ETH-USD",  "ethereum",    "eth",  "Ethereum (ETH)"),
        ("SOL-USD",  "solana",      "sol",  "Solana (SOL)"),
        ("HYPE-USD", "hyperliquid", "hype", "Hyperliquid (HYPE)"),
    ]
    for ticker, cg_id, prefix, label in altcoins:
        out.update(_altcoin_block(ticker, cg_id, prefix, label, btc_close))

    # ── Indicateurs sociaux + halving ────────────────────────────
    out.update(_social_indicators())

    return out


# ══════════════════════════════════════════════════════════════════
# MATIÈRES PREMIÈRES
# ══════════════════════════════════════════════════════════════════

def _commodity_block(
    ticker: str,
    prefix: str,
    label: str,
    price_unit: str = "$",
    round_price: int = 2,
) -> dict:
    """RSI, MACD, MM50, MM200 et perf 1 an pour un future ou ETF commodity."""
    out = {}
    try:
        hist  = yf.Ticker(ticker).history(period="2y")
        if hist.empty:
            return out
        close = hist["Close"]
        price = float(close.iloc[-1])
        p_str = f"{price_unit}{round(price, round_price):,}"

        rsi_v = _rsi(close)
        if rsi_v is not None:
            out[f"rsi_{prefix}"] = _ind(
                rsi_v, f"{rsi_v}  ({p_str})", "", _rsi_sig(rsi_v),
                f"RSI {label} à {rsi_v} (cours : {p_str}) — "
                + ("suracheté, risque de correction." if rsi_v > 70
                   else "survendu — opportunité d'achat potentielle." if rsi_v < 30
                   else "zone neutre, momentum équilibré."),
            )

        if len(close) >= 26:
            mv, sv = _macd(close)
            bull   = mv > sv
            out[f"macd_{prefix}"] = _ind(
                72 if bull else 28, "Positif" if bull else "Négatif", "",
                "buy" if bull else "sell",
                f"MACD {label} {'positif — tendance haussière confirmée.' if bull else 'négatif — tendance baissière en cours.'}",
            )

        if len(close) >= 50:
            ma50    = float(close.rolling(50).mean().iloc[-1])
            above50 = price > ma50
            pct50   = round((price / ma50 - 1) * 100, 1)
            out[f"mm50_{prefix}"] = _ind(
                78 if above50 else 25,
                f"{'+' if pct50 >= 0 else ''}{pct50} %", "",
                "buy" if above50 else "sell",
                f"{label} {'au-dessus' if above50 else 'en dessous'} de sa MM50 ({pct50:+.1f} %) — tendance court terme {'haussière.' if above50 else 'baissière.'}",
            )

        if len(close) >= 200:
            ma200    = float(close.rolling(200).mean().iloc[-1])
            above200 = price > ma200
            pct200   = round((price / ma200 - 1) * 100, 1)
            out[f"mm200_{prefix}"] = _ind(
                83 if above200 else 20,
                f"{'+' if pct200 >= 0 else ''}{pct200} %", "",
                "buy" if above200 else "sell",
                f"{label} {'au-dessus' if above200 else 'en dessous'} de sa MM200 ({pct200:+.1f} %) — tendance long terme {'haussière.' if above200 else 'baissière.'}",
            )

        if len(close) >= 250:
            perf = round((float(close.iloc[-1]) / float(close.iloc[-250]) - 1) * 100, 1)
            out[f"perf1y_{prefix}"] = _ind(
                _norm(perf, -60, 120),
                f"{'+' if perf >= 0 else ''}{perf} %", " (1 an)",
                "buy" if perf > 10 else "sell" if perf < -10 else "neutral",
                f"Performance {label} sur 12 mois : {perf:+.1f} %.",
            )

    except Exception as e:
        print(f"[matieres] {ticker}: {e}")
    return out



def _commodity_block(ticker, prefix, label, price_unit="$", round_price=2):
    """RSI, MACD, MM50, MM200 et perf 1 an pour un future ou ETF commodity."""
    out = {}
    try:
        hist  = yf.Ticker(ticker).history(period="2y")
        if hist.empty:
            return out
        close = hist["Close"]
        price = float(close.iloc[-1])
        p_str = price_unit + str(f"{round(price, round_price):,}")

        # RSI
        rsi_v = _rsi(close)
        if rsi_v is not None:
            if rsi_v > 70:   rsi_desc = "suracheté, risque de correction."
            elif rsi_v < 30: rsi_desc = "survendu — opportunité d'achat potentielle."
            else:            rsi_desc = "zone neutre, momentum équilibré."
            out[f"rsi_{prefix}"] = _ind(
                rsi_v, f"{rsi_v}  ({p_str})", "", _rsi_sig(rsi_v),
                f"RSI {label} à {rsi_v} (cours : {p_str}) — {rsi_desc}",
            )

        # MACD
        if len(close) >= 26:
            mv, sv = _macd(close)
            bull   = mv > sv
            macd_desc = f"MACD {label} " + ("positif — tendance haussière confirmée." if bull else "négatif — tendance baissière en cours.")
            out[f"macd_{prefix}"] = _ind(72 if bull else 28, "Positif" if bull else "Négatif", "",
                "buy" if bull else "sell", macd_desc)

        # MM50
        if len(close) >= 50:
            ma50    = float(close.rolling(50).mean().iloc[-1])
            above50 = price > ma50
            pct50   = round((price / ma50 - 1) * 100, 1)
            dir50   = "au-dessus" if above50 else "en dessous"
            trend50 = "haussière." if above50 else "baissière."
            out[f"mm50_{prefix}"] = _ind(
                78 if above50 else 25, f"{pct50:+.1f} %", "",
                "buy" if above50 else "sell",
                f"{label} {dir50} de sa MM50 ({pct50:+.1f} %) — tendance court terme {trend50}",
            )

        # MM200
        if len(close) >= 200:
            ma200    = float(close.rolling(200).mean().iloc[-1])
            above200 = price > ma200
            pct200   = round((price / ma200 - 1) * 100, 1)
            dir200   = "au-dessus" if above200 else "en dessous"
            trend200 = "haussière." if above200 else "baissière."
            out[f"mm200_{prefix}"] = _ind(
                83 if above200 else 20, f"{pct200:+.1f} %", "",
                "buy" if above200 else "sell",
                f"{label} {dir200} de sa MM200 ({pct200:+.1f} %) — tendance long terme {trend200}",
            )

        # Performance 1 an
        if len(close) >= 250:
            perf = round((float(close.iloc[-1]) / float(close.iloc[-250]) - 1) * 100, 1)
            if perf > 30:    perf_desc = "Tendance long terme très haussière."
            elif perf > 10:  perf_desc = "Bonne performance annuelle."
            elif perf < -10: perf_desc = "Sous-performance sur l'année."
            else:            perf_desc = "Performance neutre sur l'année."
            out[f"perf1y_{prefix}"] = _ind(
                _norm(perf, -60, 120), f"{perf:+.1f} %", " (1 an)",
                "buy" if perf > 10 else "sell" if perf < -10 else "neutral",
                f"Performance {label} sur 12 mois : {perf:+.1f} %. {perf_desc}",
            )

    except Exception as e:
        print(f"[matieres] {ticker}: {e}")
    return out


# ══════════════════════════════════════════════════════════════════
# MATIÈRES PREMIÈRES
# ══════════════════════════════════════════════════════════════════

def _matieres():
    out = {}

    # ── Macro & Dollar ────────────────────────────────────────────
    try:
        s      = yf.Ticker("DX-Y.NYB").history(period="2y")["Close"]
        p      = round(float(s.iloc[-1]), 1)
        ma200  = float(s.rolling(200).mean().iloc[-1])
        strong = p > ma200
        desc   = "fort, pression sur les matières premières." if strong else "en repli, favorable aux matières premières."
        out["dxy"] = _ind(
            _norm(p, 90, 115), p, "",
            "sell" if strong else "buy",
            f"DXY à {p} (MM200 : {ma200:.0f}) — dollar {desc}",
        )
    except Exception as e:
        print(f"[matieres] DXY: {e}")

    try:
        tips = _fred("DFII10")
        if tips:
            t = tips[0]
            if t < 0.5:  t_desc = "très favorable à l'or et aux actifs réels."
            elif t > 2:  t_desc = "taux élevés, pression sur l'or et les matières premières."
            else:        t_desc = "taux modérés, impact neutre sur les matières premières."
            out["realrates"] = _ind(
                _norm(t, -2, 4), f"{t:.1f} %", "",
                "buy" if t < 0.5 else "sell" if t > 2 else "neutral",
                f"Taux réels TIPS 10y à {t:.1f} % (FRED) — {t_desc}",
            )
    except Exception as e:
        print(f"[matieres] TIPS: {e}")

    try:
        cpi_obs = _fred("CPIAUCSL", limit=13)
        if cpi_obs and len(cpi_obs) >= 13:
            yoy = round((cpi_obs[0] / cpi_obs[12] - 1) * 100, 1)
            if yoy > 2:  c_desc = "soutient les actifs tangibles et matières premières."
            elif yoy < 1: c_desc = "inflation très basse, moins de soutien aux matières premières."
            else:         c_desc = "inflation modérée."
            out["cpi"] = _ind(
                _norm(yoy, 0, 8), f"{yoy} %", "",
                "buy" if yoy > 2 else "neutral" if yoy > 0.5 else "sell",
                f"Inflation CPI à {yoy} % annualisé (FRED) — {c_desc}",
            )
    except Exception as e:
        print(f"[matieres] CPI: {e}")

    # ── Or ────────────────────────────────────────────────────────
    out.update(_commodity_block("GC=F", "gold", "Or (GC=F)", "$", 0))

    try:
        gp    = float(yf.Ticker("GC=F").history(period="5d")["Close"].iloc[-1])
        sp    = float(yf.Ticker("SI=F").history(period="5d")["Close"].iloc[-1])
        ratio = round(gp / sp, 1)
        if ratio > 75:  r_desc = "argent historiquement sous-évalué vs or (moy. ~65:1). Signal fort sur l'argent."
        elif ratio < 55: r_desc = "argent surperformant l'or, ratio bas."
        else:            r_desc = "ratio dans la normale historique."
        out["goldsil"] = _ind(
            _norm(ratio, 40, 100), f"{ratio}:1", "",
            "buy" if ratio > 75 else "sell" if ratio < 55 else "neutral",
            f"Ratio Or/Argent à {ratio}:1 (Or ${gp:,.0f} / Argent ${sp:.1f}) — {r_desc}",
        )
    except Exception as e:
        print(f"[matieres] Gold/Silver ratio: {e}")

    out["cbgold"] = _ind(88, "Records", "", "buy",
        "Achats records des banques centrales mondiales en 2023-2024 — demande institutionnelle forte (World Gold Council).")

    # ── Argent ────────────────────────────────────────────────────
    out.update(_commodity_block("SI=F", "silver", "Argent (SI=F)", "$", 2))

    # ── Pétrole & Énergie ─────────────────────────────────────────
    out.update(_commodity_block("CL=F", "wti",   "Pétrole WTI (CL=F)",   "$", 2))
    out.update(_commodity_block("BZ=F", "brent", "Pétrole Brent (BZ=F)", "$", 2))
    out.update(_commodity_block("NG=F", "ng",    "Gaz Naturel (NG=F)",   "$", 3))

    try:
        gp  = float(yf.Ticker("GC=F").history(period="5d")["Close"].iloc[-1])
        op  = float(yf.Ticker("CL=F").history(period="5d")["Close"].iloc[-1])
        gor = round(gp / op, 1)
        if gor > 30:  gor_desc = "or très cher vs pétrole — signal déflationniste ou récession."
        elif gor < 15: gor_desc = "pétrole cher vs or — tensions d'offre ou expansion économique."
        else:          gor_desc = "rapport équilibré, conditions macro normales."
        out["gold_oil_ratio"] = _ind(
            _norm(gor, 10, 40), f"{gor}:1", "",
            "sell" if gor > 30 else "buy" if gor < 15 else "neutral",
            f"Ratio Or/Pétrole à {gor}:1 — {gor_desc}",
        )
    except Exception as e:
        print(f"[matieres] Gold/Oil ratio: {e}")

    # ── Uranium & Nucléaire ───────────────────────────────────────
    out.update(_commodity_block("URA",  "ura",  "Uranium ETF (URA)",         "$", 2))
    out.update(_commodity_block("URNM", "urnm", "Sprott Uranium (URNM)",     "$", 2))
    out.update(_commodity_block("CCJ",  "ccj",  "Cameco Corp (CCJ)",         "$", 2))
    out["nuclear"] = _ind(88, "60+", " réacteurs", "buy",
        "60+ réacteurs en construction mondiale — relance nucléaire massive. Déficit uranium offre/demande jusqu'à 2030+.")

    # ── Platine & Palladium ───────────────────────────────────────
    out.update(_commodity_block("PL=F", "platinum", "Platine (PL=F)", "$", 0))
    out.update(_commodity_block("PA=F", "palladium", "Palladium (PA=F)", "$", 0))

    try:
        pt  = float(yf.Ticker("PL=F").history(period="5d")["Close"].iloc[-1])
        pd_ = float(yf.Ticker("PA=F").history(period="5d")["Close"].iloc[-1])
        r   = round(pt / pd_, 2)
        if r < 1.0:  pp_desc = "platine à forte décote vs palladium — potentiel de rattrapage historique."
        elif r < 1.3: pp_desc = "ratio normalisé, spread réduit."
        else:         pp_desc = "platine à prime sur le palladium."
        out["platpall"] = _ind(
            _norm(r, 0.3, 2.0), f"Pt ${int(pt):,} / Pd ${int(pd_):,}", "",
            "buy" if r < 1.0 else "neutral" if r < 1.3 else "sell",
            f"Platine ${int(pt):,} vs Palladium ${int(pd_):,} (ratio {r}) — {pp_desc}",
        )
    except Exception as e:
        print(f"[matieres] Plat/Pall: {e}")

    # ── Métaux Industriels ────────────────────────────────────────
    out.update(_commodity_block("HG=F",  "copper", "Cuivre (HG=F)",     "$/lb", 2))
    out.update(_commodity_block("ALI=F", "alum",   "Aluminium (ALI=F)", "$/lb", 4))

    return out


# ══════════════════════════════════════════════════════════════════
# ANALYTICS — CORRÉLATIONS, DIVERGENCES, BACKTESTING
# ══════════════════════════════════════════════════════════════════

def _rsi_series(series: pd.Series, period: int = 14) -> pd.Series:
    """Retourne la série RSI complète (pas seulement la dernière valeur)."""
    delta = series.diff().dropna()
    gain  = delta.clip(lower=0).ewm(com=period-1, min_periods=period).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period-1, min_periods=period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _correlations() -> dict:
    """
    Matrice de corrélation des rendements journaliers sur 1 an.
    Retourne les noms des actifs et la matrice (liste de listes).
    """
    assets = {
        "S&P 500":    "^GSPC",
        "Bitcoin":    "BTC-USD",
        "Ethereum":   "ETH-USD",
        "Solana":     "SOL-USD",
        "Or":         "GC=F",
        "Argent":     "SI=F",
        "Pétrole WTI":"CL=F",
        "DXY":        "DX-Y.NYB",
    }
    prices: dict[str, pd.Series] = {}
    for name, ticker in assets.items():
        try:
            h = yf.Ticker(ticker).history(period="1y")["Close"]
            if len(h) > 50:
                prices[name] = h
        except Exception as e:
            print(f"[corr] {ticker}: {e}")

    if len(prices) < 2:
        return {}

    df      = pd.DataFrame(prices).pct_change().dropna()
    corr    = df.corr()
    cols    = list(corr.columns)
    matrix  = [[round(float(corr.loc[r, c]), 3) for c in cols] for r in cols]
    return {"assets": cols, "matrix": matrix}


def _divergences() -> list[dict]:
    """
    Détecte les divergences haussières et baissières prix/RSI
    sur les 20 derniers jours (pente de régression linéaire).
    """
    assets = {
        "^GSPC":    "S&P 500",
        "BTC-USD":  "Bitcoin",
        "ETH-USD":  "Ethereum",
        "SOL-USD":  "Solana",
        "GC=F":     "Or",
        "CL=F":     "Pétrole WTI",
        "HG=F":     "Cuivre",
    }
    result = []
    WINDOW = 20

    for ticker, name in assets.items():
        try:
            close = yf.Ticker(ticker).history(period="1y")["Close"]
            if len(close) < WINDOW + 14:
                continue

            rsi_s   = _rsi_series(close)
            p_rec   = close.tail(WINDOW).values
            r_rec   = rsi_s.tail(WINDOW).dropna().values
            if len(r_rec) < WINDOW:
                continue

            x            = np.arange(WINDOW, dtype=float)
            p_slope      = np.polyfit(x, p_rec, 1)[0]
            r_slope      = np.polyfit(x, r_rec, 1)[0]
            p_slope_pct  = p_slope / abs(p_rec[0]) * 100  # en % par jour

            # Seuils : divergence significative uniquement
            PRICE_THR = 0.05   # >0.05% par jour
            RSI_THR   = 0.15   # >0.15 pt RSI par jour

            if p_slope_pct < -PRICE_THR and r_slope > RSI_THR:
                result.append({
                    "asset": name, "type": "bullish",
                    "sig": "buy",
                    "desc": (f"{name} : divergence haussière — prix en baisse "
                             f"({p_slope_pct:+.2f}%/j) mais RSI en hausse "
                             f"({r_slope:+.2f}pt/j). Signal fort d'inversion potentielle."),
                })
            elif p_slope_pct > PRICE_THR and r_slope < -RSI_THR:
                result.append({
                    "asset": name, "type": "bearish",
                    "sig": "sell",
                    "desc": (f"{name} : divergence baissière — prix en hausse "
                             f"({p_slope_pct:+.2f}%/j) mais RSI en baisse "
                             f"({r_slope:+.2f}pt/j). Risque de retournement à court terme."),
                })
        except Exception as e:
            print(f"[div] {ticker}: {e}")

    return result


def _backtest() -> list[dict]:
    """
    Backtesting historique : performance 90j après chaque signal RSI.
    Utilise l'historique complet disponible sur yfinance.
    """
    assets = [
        ("BTC-USD", "Bitcoin",  35, 70),
        ("ETH-USD", "Ethereum", 35, 70),
        ("^GSPC",   "S&P 500",  35, 70),
        ("GC=F",    "Or",       35, 70),
        ("SOL-USD", "Solana",   35, 70),
    ]
    FWD   = 90    # jours de rendement forward
    results = []

    for ticker, name, buy_thr, sell_thr in assets:
        try:
            close = yf.Ticker(ticker).history(period="5y")["Close"]
            if len(close) < 200:
                continue

            rsi_s = _rsi_series(close)

            for label, lo, hi, direction in [
                (f"RSI < {buy_thr} (survente)",   None, buy_thr,  "buy"),
                (f"RSI > {sell_thr} (surachat)",  sell_thr, None,  "sell"),
            ]:
                # Détecter les croisements de seuil
                crossings = []
                for i in range(1, len(rsi_s) - FWD):
                    v_prev = rsi_s.iloc[i - 1]
                    v_curr = rsi_s.iloc[i]
                    if np.isnan(v_prev) or np.isnan(v_curr):
                        continue
                    if direction == "buy"  and v_prev >= buy_thr  and v_curr < buy_thr:
                        crossings.append(i)
                    if direction == "sell" and v_prev <= sell_thr and v_curr > sell_thr:
                        crossings.append(i)

                if not crossings:
                    continue

                rets = []
                for idx in crossings:
                    if idx + FWD < len(close):
                        r = (float(close.iloc[idx + FWD]) / float(close.iloc[idx]) - 1) * 100
                        rets.append(round(r, 1))

                if not rets:
                    continue

                avg      = round(sum(rets) / len(rets), 1)
                win_rate = round(sum(1 for r in rets if (r > 0) == (direction == "buy")) / len(rets) * 100)
                results.append({
                    "asset":        name,
                    "signal":       label,
                    "direction":    direction,
                    "occurrences":  len(rets),
                    "avg_90d":      avg,
                    "win_rate":     win_rate,
                    "last_5":       rets[-5:],
                })
        except Exception as e:
            print(f"[backtest] {ticker}: {e}")

    return results


# ══════════════════════════════════════════════════════════════════

def get_all() -> dict:
    """Indicateurs principaux — rapides, cache 1h."""
    return {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "bourse":    _bourse(),
        "crypto":    _crypto(),
        "matieres":  _matieres(),
    }


def get_analytics() -> dict:
    """Analytics avancées — plus lentes, cache 4h."""
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        f_corr = ex.submit(_correlations)
        f_div  = ex.submit(_divergences)
        f_back = ex.submit(_backtest)
        correlations = f_corr.result()
        divergences  = f_div.result()
        backtest     = f_back.result()
    return {
        "timestamp":     datetime.utcnow().isoformat() + "Z",
        "correlations":  correlations,
        "divergences":   divergences,
        "backtest":      backtest,
    }
