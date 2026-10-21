"""
Niveaux de prix indicatifs (supports, résistances, moyennes mobiles, volatilité) pour les actifs suivis.
Calculés depuis l'historique de prix (yfinance) ; publiés dans levels.json.
Ce ne sont pas des prévisions : des repères où le prix a déjà réagi (plus haut/plus bas récents, MM50/MM200).
"""
import pandas as pd
import yfinance as yf

ASSETS = {"btc": ("BTC-USD", "Bitcoin"), "eth": ("ETH-USD", "Ethereum"), "sol": ("SOL-USD", "Solana"),
          "hype": ("HYPE-USD", "Hyperliquid"), "spx": ("^GSPC", "S&P 500"), "gold": ("GC=F", "Or")}


def _atr(h: pd.DataFrame, n: int = 14):
    if len(h) < n + 1:
        return None
    pc = h["Close"].shift(1)
    tr = pd.concat([h["High"] - h["Low"], (h["High"] - pc).abs(), (h["Low"] - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(n).mean().iloc[-1])


def compute(h: pd.DataFrame, label: str, ticker: str) -> dict | None:
    h = h.dropna(subset=["Close", "High", "Low"])
    if len(h) < 30:
        return None
    c = h["Close"]
    price = float(c.iloc[-1])
    lv = {"prix": price, "date": str(h.index[-1])[:10], "ticker": ticker, "label": label}
    cand = []     # (libellé, niveau)
    for n, name in ((30, "30 j"), (90, "90 j")):
        if len(h) >= n:
            cand.append((f"plus bas {name}", float(h["Low"].iloc[-n:].min())))
            cand.append((f"plus haut {name}", float(h["High"].iloc[-n:].max())))
    for n in (50, 200):
        if len(c) >= n:
            m = float(c.rolling(n).mean().iloc[-1])
            lv[f"mm{n}"] = m
            cand.append((f"MM{n}", m))
    if len(h) >= 250:
        cand.append(("plus bas 1 an", float(h["Low"].iloc[-365:].min())))
        cand.append(("plus haut 1 an", float(h["High"].iloc[-365:].max())))
    atr = _atr(h)
    if atr:
        lv["atr"], lv["atr_pct"] = atr, atr / price * 100
        lv["fourchette_2atr"] = [price - 2 * atr, price + 2 * atr]

    def mk(v, lbl):
        return {"label": lbl, "prix": v, "dist_pct": (v / price - 1) * 100}
    # on fusionne les niveaux à moins de 2 % l'un de l'autre
    def pick(levels, reverse):
        out = []
        for lbl, v in sorted(levels, key=lambda x: x[1], reverse=reverse):
            if not out or abs(v / out[-1]["prix"] - 1) > 0.02:
                out.append(mk(v, lbl))
            else:
                out[-1]["label"] += f" + {lbl}"
        return out[:3]
    lv["supports"] = pick([x for x in cand if x[1] < price], True)
    lv["resistances"] = pick([x for x in cand if x[1] > price], False)
    return lv


def update(out, write_json, iso):
    res = {}
    for key, (tk, label) in ASSETS.items():
        try:
            h = yf.Ticker(tk).history(period="2y", auto_adjust=True)
            lv = compute(h, label, tk)
            if lv:
                res[key] = lv
        except Exception as e:
            print(f"[niveaux] {tk}: {e}")
    if res:
        write_json(out / "levels.json", {"generated": iso, "actifs": res})
    print(f"[niveaux] {len(res)} actifs : {sorted(res)}")
    return res
