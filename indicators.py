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

def _bourse() -> dict:
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


def _crypto() -> dict:
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

    return out


# ══════════════════════════════════════════════════════════════════
# MATIÈRES PREMIÈRES
# ══════════════════════════════════════════════════════════════════

def _matieres() -> dict:
    out = {}

    symbols = {
        "gold":      "GC=F",
        "silver":    "SI=F",
        "platinum":  "PL=F",
        "palladium": "PA=F",
        "copper":    "HG=F",
        "dxy":       "DX-Y.NYB",
        "uranium":   "URA",
    }

    data: dict[str, pd.Series] = {}
    for name, sym in symbols.items():
        try:
            h = yf.Ticker(sym).history(period="2y")
            if not h.empty:
                data[name] = h["Close"]
        except Exception as e:
            print(f"[matieres] {sym}: {e}")

    # DXY
    if "dxy" in data:
        s      = data["dxy"]
        p      = round(float(s.iloc[-1]), 1)
        ma200  = float(s.rolling(200).mean().iloc[-1])
        strong = p > ma200
        out["dxy"] = _ind(
            _norm(p, 90, 115), p, "",
            "sell" if strong else "buy",
            f"DXY à {p} ({'au-dessus' if strong else 'en dessous'} de sa MM200 {ma200:.0f}) — "
            f"dollar {'fort, pression sur les matières premières.' if strong else 'en repli, favorable aux matières premières.'}",
        )

    # RSI Or
    if "gold" in data:
        s  = data["gold"]
        rv = _rsi(s)
        p  = round(float(s.iloc[-1]))
        if rv is not None:
            out["goldrsi"] = _ind(
                rv, rv, "", _rsi_sig(rv),
                f"RSI Or (GC=F) à {rv}, prix ${p:,} — "
                + ("or suracheté à court terme." if rv > 65
                   else "or survendu, opportunité d'accumulation." if rv < 35
                   else "zone neutre sur l'or."),
            )

    # Ratio Or / Argent
    if "gold" in data and "silver" in data:
        gp    = float(data["gold"].iloc[-1])
        sp    = float(data["silver"].iloc[-1])
        ratio = round(gp / sp, 1)
        r_sig = "buy" if ratio > 75 else "sell" if ratio < 55 else "neutral"
        out["goldsil"] = _ind(
            _norm(ratio, 40, 100), f"{ratio}:1", "",
            r_sig,
            f"Ratio Or/Argent à {ratio}:1 (Or ${gp:,.0f} / Argent ${sp:.1f}) — "
            + ("l'argent est historiquement sous-évalué vs l'or (moy. ~65:1)." if ratio > 75
               else "argent surperformant l'or, ratio bas." if ratio < 55
               else "ratio dans la normale historique."),
        )

    # RSI Argent
    if "silver" in data:
        s  = data["silver"]
        rv = _rsi(s)
        if rv is not None:
            out["sivrsi"] = _ind(
                rv, rv, "", _rsi_sig(rv),
                f"RSI Argent (SI=F) à {rv} — "
                + ("suracheté." if rv > 65 else "survendu, opportunité." if rv < 35 else "zone neutre."),
            )

    # Platine vs Palladium
    if "platinum" in data and "palladium" in data:
        pt   = round(float(data["platinum"].iloc[-1]))
        pd_  = round(float(data["palladium"].iloc[-1]))
        r    = round(pt / pd_, 2)
        pp_s = "buy" if r < 1.0 else "neutral" if r < 1.3 else "sell"
        out["platpall"] = _ind(
            _norm(r, 0.3, 2.0), f"Pt ${pt:,} / Pd ${pd_:,}", "",
            pp_s,
            f"Platine ${pt:,} vs Palladium ${pd_:,} (ratio {r}) — "
            + ("platine à forte décote, potentiel de rattrapage." if r < 1.0
               else "ratio normalisé." if r < 1.3
               else "platine à prime sur le palladium."),
        )

    # Cuivre
    if "copper" in data:
        s  = data["copper"]
        rv = _rsi(s)
        p  = round(float(s.iloc[-1]), 2)
        if rv is not None:
            out["copper"] = _ind(
                rv, f"${p}/lb", "", _rsi_sig(rv),
                f"Cuivre (HG=F) à ${p}/lb, RSI {rv} — "
                + ("suracheté." if rv > 65
                   else "survendu sur fond de demande structurelle forte." if rv < 35
                   else "demande soutenue par la transition énergétique (IA, EVs)."),
            )

    # Uranium ETF (URA)
    if "uranium" in data:
        s  = data["uranium"]
        rv = _rsi(s)
        p  = round(float(s.iloc[-1]), 2)
        if rv is not None:
            out["ursi"]  = _ind(rv, rv, "", _rsi_sig(rv),
                f"RSI URA ETF à {rv} — {'suracheté.' if rv>65 else 'survendu.' if rv<35 else 'zone neutre, tendance long terme haussière.'}")
            out["uspot"] = _ind(
                _norm(p, 15, 65), f"${p}", " (URA ETF)",
                "buy" if p > 25 else "neutral",
                f"URA ETF (proxy uranium) à ${p} — déficit structurel offre/demande jusqu'à 2030+.",
            )

    return out


# ══════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════

def get_all() -> dict:
    return {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "bourse":    _bourse(),
        "crypto":    _crypto(),
        "matieres":  _matieres(),
    }
