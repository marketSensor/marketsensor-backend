/* ═══════════════════════════════════════════════════════════════════
   MarketSense — app.js  v2
   Sources live :
     Backend propre  → Bourse (RSI/MACD/Bollinger/VIX/CAPE) + Matières premières
     Alternative.me  → Crypto Fear & Greed
     CoinGecko       → BTC Dominance · RSI BTC · MACD BTC · Pi Cycle
     Blockchain.info → Hash Rate BTC
     Alpha Vantage   → cache 24h localStorage (quota 25 req/jour préservé)
   ═══════════════════════════════════════════════════════════════════ */
'use strict';

const APP = { tab: 'bourse', data: null, loading: false, lastUpdate: null, liveCount: 0 };

const Config = {
  get avKey()      { return localStorage.getItem('ms_av_key')      || ''; },
  set avKey(v)     { localStorage.setItem('ms_av_key', v); },
  get theme()      { return localStorage.getItem('ms_theme')      || 'dark'; },
  set theme(v)     { localStorage.setItem('ms_theme', v); },
  get backendUrl() { return localStorage.getItem('ms_backend_url') || ''; },
  set backendUrl(v){ localStorage.setItem('ms_backend_url', v); },
};

const gel  = id => document.getElementById(id);
const html = (el, h) => { el.innerHTML = h; };

/* ══════════════════════════════════════════════════════════════════
   API MODULE
   ══════════════════════════════════════════════════════════════════ */
const Api = {
  async get(url, label = '') {
    try {
      const ctrl  = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 10000);
      const res   = await fetch(url, { signal: ctrl.signal, cache: 'no-store' });
      clearTimeout(timer);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) { console.warn(`[MarketSense] ${label} →`, e.message); return null; }
  },

  async cryptoFearGreed() {
    const d = await this.get('https://api.alternative.me/fng/?limit=1', 'FNG');
    return d?.data?.[0] ? { value: +d.data[0].value, label: d.data[0].value_classification } : null;
  },

  async cgGlobal() {
    const d = await this.get('https://api.coingecko.com/api/v3/global', 'CG Global');
    return d?.data ? { btcDom: d.data.market_cap_percentage.btc } : null;
  },

  async btcPrices(days = 365) {
    const d = await this.get(
      `https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=${days}&interval=daily`,
      'BTC Prices'
    );
    return d?.prices ? d.prices.map(p => p[1]) : null;
  },

  async blockchainStats() {
    const d = await this.get('https://api.blockchain.info/stats', 'Blockchain');
    return d?.hash_rate ? { hashRate: d.hash_rate } : null;
  },

  async backend() {
    const base = Config.backendUrl.replace(/\/$/, '');
    if (!base) return null;
    return await this.get(`${base}/api/indicators`, 'Backend');
  },

  /* Alpha Vantage — cache localStorage 24h pour préserver le quota */
  _avRead(key) {
    try {
      const item = JSON.parse(localStorage.getItem(`ms_av_${key}`) || 'null');
      if (!item || Date.now() - item.ts > 86_400_000) return null;
      return item.data;
    } catch { return null; }
  },
  _avWrite(key, data) {
    try { localStorage.setItem(`ms_av_${key}`, JSON.stringify({ data, ts: Date.now() })); } catch {}
  },

  async avRSI(symbol) {
    const c = this._avRead(`rsi_${symbol}`);
    if (c !== null) { console.info(`[AV] RSI ${symbol} depuis cache 24h`); return c; }
    if (!Config.avKey) return null;
    const url = `https://www.alphavantage.co/query?function=RSI&symbol=${symbol}&interval=daily&time_period=14&series_type=close&apikey=${Config.avKey}`;
    const d = await this.get(url, `AV RSI ${symbol}`);
    const ana = d?.['Technical Analysis: RSI'];
    if (!ana) return null;
    const val = parseFloat(Object.values(ana)[0].RSI);
    this._avWrite(`rsi_${symbol}`, val);
    return val;
  },

  async avMACD(symbol) {
    const c = this._avRead(`macd_${symbol}`);
    if (c !== null) { console.info(`[AV] MACD ${symbol} depuis cache 24h`); return c; }
    if (!Config.avKey) return null;
    const url = `https://www.alphavantage.co/query?function=MACD&symbol=${symbol}&interval=daily&series_type=close&apikey=${Config.avKey}`;
    const d = await this.get(url, `AV MACD ${symbol}`);
    const ana = d?.['Technical Analysis: MACD'];
    if (!ana) return null;
    const lat = Object.values(ana)[0];
    const val = { macd: parseFloat(lat.MACD), signal: parseFloat(lat.MACD_Signal) };
    this._avWrite(`macd_${symbol}`, val);
    return val;
  },
};

/* ══════════════════════════════════════════════════════════════════
   CALCULS (client — pour la crypto uniquement)
   ══════════════════════════════════════════════════════════════════ */
const Calc = {
  rsi(prices, period = 14) {
    if (!prices || prices.length < period + 1) return null;
    let g = 0, l = 0;
    for (let i = 1; i <= period; i++) { const d = prices[i] - prices[i-1]; d > 0 ? g += d : l -= d; }
    let ag = g / period, al = l / period;
    for (let i = period + 1; i < prices.length; i++) {
      const d = prices[i] - prices[i-1];
      ag = (ag * (period-1) + Math.max(0, d))  / period;
      al = (al * (period-1) + Math.max(0,-d)) / period;
    }
    return al === 0 ? 100 : Math.round(100 - 100 / (1 + ag / al));
  },
  ema(prices, period) {
    if (!prices || prices.length < period) return null;
    const k = 2 / (period + 1);
    let e = prices.slice(0, period).reduce((a, b) => a + b, 0) / period;
    for (let i = period; i < prices.length; i++) e = prices[i] * k + e * (1 - k);
    return e;
  },
  sma(prices, period) {
    if (!prices || prices.length < period) return null;
    const s = prices.slice(-period);
    return s.reduce((a, b) => a + b, 0) / s.length;
  },
  macdSign(prices) {
    const e12 = this.ema(prices, 12), e26 = this.ema(prices, 26);
    return (e12 && e26) ? e12 - e26 : null;
  },
  norm(v, lo, hi) { return Math.min(100, Math.max(0, Math.round((v - lo) / (hi - lo) * 100))); },
  rsiSig(v)  { return v < 35 ? 'buy' : v > 65 ? 'sell' : 'neutral'; },
  fngSig(v)  { return v < 30 ? 'buy' : v > 70 ? 'sell' : 'neutral'; },
};

/* ══════════════════════════════════════════════════════════════════
   DONNÉES PAR DÉFAUT
   ══════════════════════════════════════════════════════════════════ */
function defaultData() {
  return {
    bourse: [
      { name: 'Momentum', indicators: [
        { id:'rsi_spx',   name:'RSI S&P 500 (14j)',     val:58, sig:'neutral', w:2, raw:'—',       unit:'',        source:'live',      desc:'RSI S&P 500 — chargement en cours via le backend.' },
        { id:'macd_spx',  name:'MACD S&P 500',          val:50, sig:'neutral', w:2, raw:'—',       unit:'',        source:'live',      desc:'MACD S&P 500 — chargement en cours via le backend.' },
        { id:'stoch',     name:'Stochastique (14,3)',    val:65, sig:'neutral', w:1, raw:'—',       unit:'',        source:'live',      desc:'Stochastique S&P 500 — chargement via le backend.' },
      ]},
      { name: 'Tendance', indicators: [
        { id:'mm50',      name:'Prix vs MM50',           val:78, sig:'buy',     w:2, raw:'—',       unit:'',        source:'live',      desc:'Prix S&P 500 vs MM50 — chargement en cours.' },
        { id:'mm200',     name:'Prix vs MM200',          val:83, sig:'buy',     w:3, raw:'—',       unit:'',        source:'live',      desc:'Prix S&P 500 vs MM200 — chargement en cours.' },
        { id:'cross',     name:'Golden / Death Cross',   val:80, sig:'buy',     w:2, raw:'—',       unit:'',        source:'live',      desc:'Croisement MM50/MM200 — chargement en cours.' },
      ]},
      { name: 'Volatilité', indicators: [
        { id:'vix',       name:'VIX (Cboe)',             val:38, sig:'neutral', w:2, raw:'—',       unit:'',        source:'live',      desc:'VIX — chargement en cours via le backend.' },
        { id:'bollinger', name:'Bollinger Bands',        val:55, sig:'neutral', w:1, raw:'—',       unit:'',        source:'live',      desc:'Bandes de Bollinger S&P 500 — chargement en cours.' },
        { id:'atr',       name:'ATR (volatilité hist.)', val:42, sig:'neutral', w:1, raw:'—',       unit:'',        source:'live',      desc:'ATR S&P 500 — chargement en cours.' },
      ]},
      { name: 'Sentiment', indicators: [
        { id:'fg_spx',    name:'Fear & Greed Index',     val:65, sig:'neutral', w:3, raw:'65',      unit:'/100',    source:'simulated', desc:'Indice CNN Fear & Greed. (données simulées — API privée CNN)' },
        { id:'putcall',   name:'Put / Call Ratio',       val:48, sig:'neutral', w:2, raw:'—',       unit:'',        source:'live',      desc:'Ratio Put/Call equity (CBOE) — chargement via le backend.' },
        { id:'aaii',      name:'AAII Sentiment',         val:60, sig:'neutral', w:1, raw:'—',       unit:'',        source:'live',      desc:'Sentiment AAII hebdomadaire — chargement via le backend.' },
      ]},
      { name: 'MSCI World — CW8 (Amundi)', indicators: [
        { id:'rsi_cw8',   name:'RSI CW8 (14j)',         val:50, sig:'neutral', w:2, raw:'—', unit:'', source:'live', desc:'RSI Amundi MSCI World UCITS ETF (CW8) — chargement.' },
        { id:'macd_cw8',  name:'MACD CW8',              val:50, sig:'neutral', w:2, raw:'—', unit:'', source:'live', desc:'MACD CW8 — chargement via le backend.' },
        { id:'mm50_cw8',  name:'CW8 vs MM50',           val:50, sig:'neutral', w:1, raw:'—', unit:'', source:'live', desc:'CW8 vs moyenne mobile 50j — tendance court terme.' },
        { id:'mm200_cw8', name:'CW8 vs MM200',          val:50, sig:'neutral', w:3, raw:'—', unit:'', source:'live', desc:'CW8 vs moyenne mobile 200j — tendance long terme MSCI World.' },
        { id:'stoch_cw8', name:'Stochastique CW8',      val:50, sig:'neutral', w:1, raw:'—', unit:'', source:'live', desc:'Stochastique CW8 — chargement via le backend.' },
      ]},
      { name: 'S&P 500 ETF — ESE (Amundi)', indicators: [
        { id:'rsi_ese',   name:'RSI ESE (14j)',          val:50, sig:'neutral', w:2, raw:'—', unit:'', source:'live', desc:'RSI Amundi S&P 500 UCITS ETF (ESE) — chargement.' },
        { id:'macd_ese',  name:'MACD ESE',               val:50, sig:'neutral', w:2, raw:'—', unit:'', source:'live', desc:'MACD ESE — chargement via le backend.' },
        { id:'mm50_ese',  name:'ESE vs MM50',            val:50, sig:'neutral', w:1, raw:'—', unit:'', source:'live', desc:'ESE vs moyenne mobile 50j — tendance court terme S&P 500.' },
        { id:'mm200_ese', name:'ESE vs MM200',           val:50, sig:'neutral', w:3, raw:'—', unit:'', source:'live', desc:'ESE vs moyenne mobile 200j — tendance long terme S&P 500 ETF.' },
        { id:'stoch_ese', name:'Stochastique ESE',       val:50, sig:'neutral', w:1, raw:'—', unit:'', source:'live', desc:'Stochastique ESE — chargement via le backend.' },
      ]},
      { name: 'Marchés Émergents — PAEEM (Amundi)', indicators: [
        { id:'rsi_paeem',   name:'RSI PAEEM (14j)',      val:50, sig:'neutral', w:2, raw:'—', unit:'', source:'live', desc:'RSI Amundi MSCI Emerging Markets UCITS ETF (PAEEM) — chargement.' },
        { id:'macd_paeem',  name:'MACD PAEEM',           val:50, sig:'neutral', w:2, raw:'—', unit:'', source:'live', desc:'MACD PAEEM — chargement via le backend.' },
        { id:'mm50_paeem',  name:'PAEEM vs MM50',        val:50, sig:'neutral', w:1, raw:'—', unit:'', source:'live', desc:'PAEEM vs moyenne mobile 50j — tendance court terme.' },
        { id:'mm200_paeem', name:'PAEEM vs MM200',       val:50, sig:'neutral', w:3, raw:'—', unit:'', source:'live', desc:'PAEEM vs moyenne mobile 200j — tendance long terme marchés émergents.' },
        { id:'stoch_paeem', name:'Stochastique PAEEM',   val:50, sig:'neutral', w:1, raw:'—', unit:'', source:'live', desc:'Stochastique PAEEM — chargement via le backend.' },
      ]},
      { name: 'Asie Pac. ex-Japon — PAASI (Amundi)', indicators: [
        { id:'rsi_paasi',   name:'RSI PAASI (14j)',      val:50, sig:'neutral', w:2, raw:'—', unit:'', source:'live', desc:'RSI Amundi MSCI AC Asia Pacific ex Japan UCITS ETF (PAASI) — chargement.' },
        { id:'macd_paasi',  name:'MACD PAASI',           val:50, sig:'neutral', w:2, raw:'—', unit:'', source:'live', desc:'MACD PAASI — chargement via le backend.' },
        { id:'mm50_paasi',  name:'PAASI vs MM50',        val:50, sig:'neutral', w:1, raw:'—', unit:'', source:'live', desc:'PAASI vs moyenne mobile 50j — tendance court terme.' },
        { id:'mm200_paasi', name:'PAASI vs MM200',       val:50, sig:'neutral', w:3, raw:'—', unit:'', source:'live', desc:'PAASI vs moyenne mobile 200j — tendance long terme Asie.' },
        { id:'stoch_paasi', name:'Stochastique PAASI',   val:50, sig:'neutral', w:1, raw:'—', unit:'', source:'live', desc:'Stochastique PAASI — chargement via le backend.' },
      ]},
      { name: 'Valorisation', indicators: [
        { id:'cape',      name:'Shiller CAPE',           val:22, sig:'sell',    w:3, raw:'—',       unit:'x',       source:'live',      desc:'Shiller CAPE — chargement en cours (scrape multpl.com).' },
        { id:'pe_fwd',    name:'P/E Trailing S&P 500',  val:38, sig:'neutral', w:2, raw:'—',       unit:'x',       source:'live',      desc:'P/E Trailing S&P 500 (SPY) — chargement via le backend.' },
      ]},
    ],
    crypto: [
      { name: 'Métriques On-Chain', indicators: [
        { id:'mvrv',     name:'MVRV Z-Score',           val:50, sig:'neutral', w:3, raw:'—',        unit:'',     source:'live', desc:'MVRV Z-Score (Bitbo API) — chargement.' },
        { id:'nupl',     name:'NUPL',                   val:50, sig:'neutral', w:3, raw:'—',        unit:'',     source:'live', desc:'Net Unrealized Profit/Loss (Bitbo API) — chargement.' },
        { id:'sopr',     name:'SOPR',                   val:50, sig:'neutral', w:2, raw:'—',        unit:'',     source:'live', desc:'Spent Output Profit Ratio (Bitbo API) — chargement.' },
        { id:'cdd',      name:'Coin Days Destroyed',    val:50, sig:'neutral', w:2, raw:'—',        unit:'',     source:'live', desc:'Coin Days Destroyed (Bitbo API) — chargement.' },
        { id:'nvt',      name:'NVT Signal',             val:50, sig:'neutral', w:2, raw:'—',        unit:'',     source:'live', desc:'Network Value to Transactions (Bitbo API) — chargement.' },
        { id:'hashrate', name:'Hash Rate BTC',          val:88, sig:'buy',     w:2, raw:'—',        unit:'EH/s', source:'live', desc:'Hash Rate BTC en temps réel (Blockchain.info).' },
      ]},
      { name: 'Sentiment Crypto', indicators: [
        { id:'cfg',      name:'Crypto Fear & Greed',    val:50, sig:'neutral', w:3, raw:'—',        unit:'/100', source:'live', desc:'Indice Alternative.me — chargement en cours.' },
        { id:'btcdom',   name:'Bitcoin Dominance',      val:54, sig:'neutral', w:1, raw:'—',        unit:'%',    source:'live', desc:'Dominance BTC (CoinGecko) — chargement en cours.' },
        { id:'funding',  name:'Funding Rate Perps',     val:55, sig:'neutral', w:2, raw:'—',        unit:'/8h',  source:'live', desc:'Funding Rate BTC perps (Binance) — chargement.' },
      ]},
      { name: 'Indicateurs de Cycle', indicators: [
        { id:'picycle',  name:'Pi Cycle Top',           val:28, sig:'buy',     w:3, raw:'—',        unit:'',     source:'live', desc:'Pi Cycle calculé depuis les prix BTC (CoinGecko) — chargement.' },
        { id:'puell',    name:'Puell Multiple',         val:50, sig:'neutral', w:2, raw:'—',        unit:'',     source:'live', desc:'Puell Multiple (Bitbo API) — chargement.' },
        { id:'mayer',    name:'Mayer Multiple',         val:50, sig:'neutral', w:2, raw:'—',        unit:'x',    source:'live', desc:'Prix BTC / MM200 (Bitbo / yfinance) — chargement.' },
        { id:'rainbow',  name:'Rainbow Chart Zone',     val:52, sig:'neutral', w:1, raw:'—',        unit:'',     source:'live', desc:'Rainbow Chart BTC (log-régression) — chargement.' },
      ]},
      { name: 'Indicateurs Sociaux', indicators: [
        { id:'google_trends',  name:'Google Trends "Bitcoin"', val:50, sig:'neutral', w:2, raw:'—', unit:'/100', source:'live', desc:'Intérêt de recherche Google pour "Bitcoin" (90j) — chargement.' },
        { id:'coinbase_rank',  name:'Coinbase — App Store',    val:50, sig:'neutral', w:2, raw:'—', unit:' Finance', source:'live', desc:'Classement Coinbase App Store Finance US — chargement.' },
        { id:'binance_rank',   name:'Binance — App Store',     val:50, sig:'neutral', w:2, raw:'—', unit:' Finance', source:'live', desc:'Classement Binance App Store Finance US — chargement.' },
      ]},
      { name: 'Halving & Cycles BTC', indicators: [
        { id:'days_since_halving', name:'Jours depuis le halving', val:30, sig:'buy', w:3, raw:'—', unit:'', source:'live', desc:'Jours écoulés depuis le halving 4 (20 avril 2024) — chargement.' },
        { id:'days_until_halving', name:'Jours avant le halving',  val:20, sig:'buy', w:2, raw:'—', unit:'', source:'live', desc:'Jours restants avant le halving 5 (~avril 2028) — chargement.' },
        { id:'cycle_progress',     name:'Progression du cycle',    val:25, sig:'buy', w:2, raw:'—', unit:'', source:'live', desc:'% du cycle post-halving H4 écoulé — chargement.' },
      ]},
      { name: 'Analyse Technique BTC', indicators: [
        { id:'btcrsi',       name:'RSI Bitcoin Journalier',  val:50, sig:'neutral', w:2, raw:'—', unit:'',    source:'live', desc:'RSI BTC (14j) depuis les prix CoinGecko — chargement.' },
        { id:'btcrsim',      name:'RSI Bitcoin Mensuel',     val:50, sig:'neutral', w:3, raw:'—', unit:'',    source:'live', desc:'RSI mensuel BTC — signal clé de cycle.' },
        { id:'btcmacd',      name:'MACD Bitcoin',            val:50, sig:'neutral', w:2, raw:'—', unit:'',    source:'live', desc:'MACD BTC depuis les prix CoinGecko — chargement.' },
        { id:'bollinger_btc',name:'Bollinger Bands BTC',     val:50, sig:'neutral', w:1, raw:'—', unit:'',    source:'live', desc:'Position BTC dans ses bandes de Bollinger — chargement.' },
        { id:'atr_btc',      name:'Volatilité ATR BTC',      val:40, sig:'neutral', w:1, raw:'—', unit:'% du prix', source:'live', desc:'ATR Bitcoin — niveau de volatilité actuel.' },
        { id:'btcsupp',      name:'Support / Résistance',    val:72, sig:'buy',     w:1, raw:'Au-dessus', unit:'', source:'simulated', desc:'Bitcoin au-dessus de ses supports clés. (données simulées)' },
      ]},
      { name: 'Ethereum (ETH)', indicators: [
        { id:'rsi_eth',      name:'RSI ETH (14j)',           val:50, sig:'neutral', w:2, raw:'—', unit:'',       source:'live', desc:'RSI Ethereum — chargement via le backend.' },
        { id:'macd_eth',     name:'MACD ETH',                val:50, sig:'neutral', w:2, raw:'—', unit:'',       source:'live', desc:'MACD Ethereum — chargement via le backend.' },
        { id:'mm50_eth',     name:'ETH vs MM50',             val:50, sig:'neutral', w:1, raw:'—', unit:'',       source:'live', desc:'ETH vs moyenne mobile 50j — chargement.' },
        { id:'mm200_eth',    name:'ETH vs MM200',            val:50, sig:'neutral', w:3, raw:'—', unit:'',       source:'live', desc:'ETH vs moyenne mobile 200j — tendance long terme.' },
        { id:'bollinger_eth',name:'Bollinger ETH',           val:50, sig:'neutral', w:1, raw:'—', unit:'',       source:'live', desc:'Position ETH dans ses bandes de Bollinger.' },
        { id:'vs_btc_eth',   name:'ETH vs BTC (90j)',        val:50, sig:'neutral', w:2, raw:'—', unit:' vs BTC',source:'live', desc:'Performance ETH relative à Bitcoin sur 90j.' },
      ]},
      { name: 'Solana (SOL)', indicators: [
        { id:'rsi_sol',      name:'RSI SOL (14j)',           val:50, sig:'neutral', w:2, raw:'—', unit:'',       source:'live', desc:'RSI Solana — chargement via le backend.' },
        { id:'macd_sol',     name:'MACD SOL',                val:50, sig:'neutral', w:2, raw:'—', unit:'',       source:'live', desc:'MACD Solana — chargement via le backend.' },
        { id:'mm50_sol',     name:'SOL vs MM50',             val:50, sig:'neutral', w:1, raw:'—', unit:'',       source:'live', desc:'SOL vs moyenne mobile 50j — chargement.' },
        { id:'mm200_sol',    name:'SOL vs MM200',            val:50, sig:'neutral', w:3, raw:'—', unit:'',       source:'live', desc:'SOL vs moyenne mobile 200j — tendance long terme.' },
        { id:'bollinger_sol',name:'Bollinger SOL',           val:50, sig:'neutral', w:1, raw:'—', unit:'',       source:'live', desc:'Position SOL dans ses bandes de Bollinger.' },
        { id:'vs_btc_sol',   name:'SOL vs BTC (90j)',        val:50, sig:'neutral', w:2, raw:'—', unit:' vs BTC',source:'live', desc:'Performance Solana relative à Bitcoin sur 90j.' },
      ]},
      { name: 'Hyperliquid (HYPE)', indicators: [
        { id:'rsi_hype',      name:'RSI HYPE (14j)',         val:50, sig:'neutral', w:2, raw:'—', unit:'',       source:'live', desc:'RSI Hyperliquid (HYPE) — chargement via le backend.' },
        { id:'macd_hype',     name:'MACD HYPE',              val:50, sig:'neutral', w:2, raw:'—', unit:'',       source:'live', desc:'MACD HYPE — chargement via le backend.' },
        { id:'mm50_hype',     name:'HYPE vs MM50',           val:50, sig:'neutral', w:1, raw:'—', unit:'',       source:'live', desc:'HYPE vs MM50 — tendance court terme.' },
        { id:'bollinger_hype',name:'Bollinger HYPE',         val:50, sig:'neutral', w:1, raw:'—', unit:'',       source:'live', desc:'Position HYPE dans ses bandes de Bollinger.' },
        { id:'vs_btc_hype',   name:'HYPE vs BTC (90j)',      val:50, sig:'neutral', w:2, raw:'—', unit:' vs BTC',source:'live', desc:'Performance HYPE relative à Bitcoin sur 90j.' },
      ]},
    ],
    matieres: [
      { name: 'Macro & Dollar', indicators: [
        { id:'dxy',           name:'Dollar Index (DXY)',       val:30, sig:'buy',     w:3, raw:'—', unit:'',          source:'live',      desc:'DXY — chargement via le backend.' },
        { id:'realrates',     name:'Taux réels (TIPS 10y)',    val:72, sig:'buy',     w:3, raw:'—', unit:'',          source:'live',      desc:'Taux réels TIPS 10y (FRED) — chargement.' },
        { id:'cpi',           name:'Inflation CPI (USA)',      val:62, sig:'buy',     w:2, raw:'—', unit:'',          source:'live',      desc:'Inflation CPI annualisée (FRED) — chargement.' },
      ]},
      { name: 'Or (GC=F)', indicators: [
        { id:'rsi_gold',      name:'RSI Or (14j)',             val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'RSI Or — chargement.' },
        { id:'macd_gold',     name:'MACD Or',                  val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'MACD Or — chargement.' },
        { id:'mm50_gold',     name:'Or vs MM50',               val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'Or vs MM50 — tendance court terme.' },
        { id:'mm200_gold',    name:'Or vs MM200',              val:50, sig:'neutral', w:3, raw:'—', unit:'',          source:'live', desc:'Or vs MM200 — tendance long terme.' },
        { id:'perf1y_gold',   name:'Performance Or 1 an',      val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'Performance Or sur 12 mois.' },
        { id:'goldsil',       name:'Ratio Or / Argent',        val:78, sig:'buy',     w:2, raw:'—', unit:'',          source:'live', desc:'Ratio Or/Argent — moy. historique ~65:1.' },
        { id:'cbgold',        name:'Achats Banques Centrales', val:88, sig:'buy',     w:3, raw:'Records', unit:'',    source:'simulated', desc:'Achats records banques centrales (WGC 2024). (données simulées)' },
      ]},
      { name: 'Argent (SI=F)', indicators: [
        { id:'rsi_silver',    name:'RSI Argent (14j)',         val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'RSI Argent — chargement.' },
        { id:'macd_silver',   name:'MACD Argent',              val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'MACD Argent — chargement.' },
        { id:'mm50_silver',   name:'Argent vs MM50',           val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'Argent vs MM50 — tendance court terme.' },
        { id:'mm200_silver',  name:'Argent vs MM200',          val:50, sig:'neutral', w:3, raw:'—', unit:'',          source:'live', desc:'Argent vs MM200 — tendance long terme.' },
        { id:'perf1y_silver', name:'Performance Argent 1 an',  val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'Performance Argent sur 12 mois.' },
      ]},
      { name: 'Petrole & Energie', indicators: [
        { id:'rsi_wti',       name:'RSI Petrole WTI (14j)',    val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'RSI Petrole WTI (CL=F) — chargement.' },
        { id:'macd_wti',      name:'MACD WTI',                 val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'MACD Petrole WTI — chargement.' },
        { id:'mm50_wti',      name:'WTI vs MM50',              val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'WTI vs MM50 — tendance court terme.' },
        { id:'mm200_wti',     name:'WTI vs MM200',             val:50, sig:'neutral', w:3, raw:'—', unit:'',          source:'live', desc:'WTI vs MM200 — tendance long terme.' },
        { id:'perf1y_wti',    name:'Performance WTI 1 an',     val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'Performance WTI sur 12 mois.' },
        { id:'rsi_brent',     name:'RSI Brent (14j)',          val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'RSI Petrole Brent (BZ=F) — chargement.' },
        { id:'macd_brent',    name:'MACD Brent',               val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'MACD Brent — chargement.' },
        { id:'mm200_brent',   name:'Brent vs MM200',           val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'Brent vs MM200 — tendance long terme.' },
        { id:'rsi_ng',        name:'RSI Gaz Naturel (14j)',    val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'RSI Gaz Naturel (NG=F) — chargement.' },
        { id:'macd_ng',       name:'MACD Gaz Naturel',         val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'MACD Gaz Naturel — chargement.' },
        { id:'mm200_ng',      name:'Gaz Naturel vs MM200',     val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'Gaz Naturel vs MM200 — tendance long terme.' },
        { id:'gold_oil_ratio',name:'Ratio Or / Petrole',       val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'Ratio Or/Petrole — signal macro (> 30 = deflationniste).' },
      ]},
      { name: 'Uranium & Nucleaire', indicators: [
        { id:'rsi_ura',       name:'RSI URA ETF (14j)',        val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'RSI Uranium ETF (URA) — chargement.' },
        { id:'macd_ura',      name:'MACD URA',                 val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'MACD URA ETF — chargement.' },
        { id:'mm200_ura',     name:'URA vs MM200',             val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'URA vs MM200 — tendance long terme.' },
        { id:'perf1y_ura',    name:'Performance URA 1 an',     val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'Performance URA ETF sur 12 mois.' },
        { id:'rsi_urnm',      name:'RSI URNM (14j)',           val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'RSI Sprott Uranium Miners ETF (URNM) — chargement.' },
        { id:'macd_urnm',     name:'MACD URNM',                val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'MACD URNM — chargement.' },
        { id:'mm200_urnm',    name:'URNM vs MM200',            val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'URNM vs MM200 — tendance long terme.' },
        { id:'rsi_ccj',       name:'RSI Cameco CCJ (14j)',     val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'RSI Cameco Corp (CCJ) — plus grand producteur mondial.' },
        { id:'macd_ccj',      name:'MACD Cameco',              val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'MACD Cameco — chargement.' },
        { id:'mm200_ccj',     name:'Cameco vs MM200',          val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'Cameco vs MM200 — tendance long terme.' },
        { id:'nuclear',       name:'Demande Nucleaire',        val:88, sig:'buy',     w:3, raw:'60+', unit:' reacteurs', source:'simulated', desc:'60+ reacteurs en construction mondiale. (donnees simulees)' },
      ]},
      { name: 'Platine & Palladium', indicators: [
        { id:'rsi_platinum',  name:'RSI Platine (14j)',        val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'RSI Platine (PL=F) — chargement.' },
        { id:'macd_platinum', name:'MACD Platine',             val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'MACD Platine — chargement.' },
        { id:'mm200_platinum',name:'Platine vs MM200',         val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'Platine vs MM200 — tendance long terme.' },
        { id:'perf1y_platinum',name:'Perf Platine 1 an',      val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'Performance Platine sur 12 mois.' },
        { id:'rsi_palladium', name:'RSI Palladium (14j)',      val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'RSI Palladium (PA=F) — chargement.' },
        { id:'mm200_palladium',name:'Palladium vs MM200',      val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'Palladium vs MM200 — tendance long terme.' },
        { id:'platpall',      name:'Ratio Platine / Palladium',val:76, sig:'buy',     w:2, raw:'—', unit:'',          source:'live', desc:'Ratio Platine vs Palladium — chargement.' },
      ]},
      { name: 'Metaux Industriels', indicators: [
        { id:'rsi_copper',    name:'RSI Cuivre (14j)',         val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'RSI Cuivre (HG=F) — chargement.' },
        { id:'macd_copper',   name:'MACD Cuivre',              val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'MACD Cuivre — chargement.' },
        { id:'mm50_copper',   name:'Cuivre vs MM50',           val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'Cuivre vs MM50 — tendance court terme.' },
        { id:'mm200_copper',  name:'Cuivre vs MM200',          val:50, sig:'neutral', w:3, raw:'—', unit:'',          source:'live', desc:'Cuivre vs MM200 — thermometre de la croissance mondiale.' },
        { id:'perf1y_copper', name:'Perf Cuivre 1 an',        val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'Performance Cuivre sur 12 mois.' },
        { id:'rsi_alum',      name:'RSI Aluminium (14j)',      val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'RSI Aluminium (ALI=F) — chargement.' },
        { id:'macd_alum',     name:'MACD Aluminium',           val:50, sig:'neutral', w:1, raw:'—', unit:'',          source:'live', desc:'MACD Aluminium — chargement.' },
        { id:'mm200_alum',    name:'Aluminium vs MM200',       val:50, sig:'neutral', w:2, raw:'—', unit:'',          source:'live', desc:'Aluminium vs MM200 — tendance long terme.' },
      ]},
    ],

  };
}

/* ══════════════════════════════════════════════════════════════════
   LIVE DATA UPDATER
   ══════════════════════════════════════════════════════════════════ */
function findInd(groups, id) {
  for (const g of groups) { const i = g.indicators.find(x => x.id === id); if (i) return i; }
  return null;
}
function applyPatch(groups, id, patch) {
  const ind = findInd(groups, id);
  if (!ind) return false;
  Object.assign(ind, patch, { source: 'live' });
  return true;
}

async function fetchLiveData(data) {
  let live = 0;

  const [fng, global, prices, blockchain, backend, avRsiSpx, avMacdSpx, avRsiGld] = await Promise.all([
    Api.cryptoFearGreed(),
    Api.cgGlobal(),
    Api.btcPrices(365),
    Api.blockchainStats(),
    Api.backend(),
    Api.avRSI('SPY'),
    Api.avMACD('SPY'),
    Api.avRSI('GLD'),
  ]);

  /* ── Backend → bourse + crypto + matières ──────────────────── */
  if (backend) {
    for (const [section, groups] of [['bourse', data.bourse], ['crypto', data.crypto], ['matieres', data.matieres]]) {
      for (const [id, vals] of Object.entries(backend[section] || {})) {
        if (applyPatch(groups, id, vals)) live++;
      }
    }
  }

  /* ── Crypto Fear & Greed ─────────────────────────────────────── */
  if (fng) {
    const v = fng.value;
    applyPatch(data.crypto, 'cfg', {
      val: v, raw: String(v), sig: Calc.fngSig(v),
      desc: `Indice à ${v} (${fng.label}) — `
        + (v > 75 ? 'euphorie extrême, risque de correction élevé.'
         : v < 25 ? 'peur extrême — opportunité d\'achat historique.'
         : v > 55 ? 'sentiment optimiste, vigilance conseillée.' : 'sentiment neutre.'),
    }); live++;
  }

  /* ── BTC Dominance ───────────────────────────────────────────── */
  if (global) {
    const dom = Math.round(global.btcDom * 10) / 10;
    applyPatch(data.crypto, 'btcdom', {
      val: Math.min(100, Math.round(dom)), raw: dom.toFixed(1),
      sig: dom > 58 ? 'sell' : dom < 42 ? 'buy' : 'neutral',
      desc: `Dominance BTC à ${dom.toFixed(1)} % (CoinGecko) — `
        + (dom > 58 ? 'Bitcoin ultra-dominant, altcoins sous pression.'
         : dom < 45 ? 'Potentielle altseason, rotations vers les altcoins.'
         : 'Marché crypto équilibré.'),
    }); live++;
  }

  /* ── BTC Prices → RSI · MACD · Pi Cycle ─────────────────────── */
  if (prices && prices.length >= 30) {
    const rsi = Calc.rsi(prices, 14);
    if (rsi !== null) {
      applyPatch(data.crypto, 'btcrsi', {
        val: rsi, raw: String(rsi), sig: Calc.rsiSig(rsi),
        desc: `RSI BTC à ${rsi} (${prices.length}j, CoinGecko) — `
          + (rsi > 70 ? 'suracheté.' : rsi < 30 ? 'survendu — opportunité d\'achat.' : 'zone neutre.'),
      }); live++;
    }
    const macd = Calc.macdSign(prices);
    if (macd !== null) {
      applyPatch(data.crypto, 'btcmacd', {
        val: macd > 0 ? 72 : 28, raw: macd > 0 ? 'Positif' : 'Négatif',
        sig: macd > 0 ? 'buy' : 'sell',
        desc: `MACD BTC ${macd > 0 ? 'positif — tendance haussière.' : 'négatif — tendance baissière.'} (CoinGecko)`,
      }); live++;
    }
    const mm111 = prices.length >= 111 ? Calc.sma(prices, 111) : null;
    const mm350 = prices.length >= 350 ? Calc.sma(prices, 350) : null;
    if (mm111 && mm350) {
      const ratio = mm111 / (2 * mm350);
      applyPatch(data.crypto, 'picycle', {
        raw: `${(ratio * 100).toFixed(0)} %`,
        val: ratio >= 0.96 ? 90 : Math.max(10, Math.round(ratio * 60)),
        sig: ratio >= 0.96 ? 'sell' : 'buy',
        desc: ratio >= 0.96
          ? `Pi Cycle proche du déclenchement (${(ratio*100).toFixed(0)} %) — signal de sommet de cycle.`
          : `Pi Cycle non déclenché (ratio ${(ratio*100).toFixed(0)} %) — loin du sommet. Signal haussier.`,
      }); live++;
    }
  }

  /* ── Hash Rate ───────────────────────────────────────────────── */
  if (blockchain?.hashRate) {
    const hr = blockchain.hashRate;
    const hrEH = hr > 1e8 ? hr / 1e9 : hr > 1e5 ? hr / 1e6 : hr;
    const display = `${Math.round(hrEH)} EH/s`;
    applyPatch(data.crypto, 'hashrate', {
      raw: display, val: Math.min(97, Calc.norm(hrEH, 100, 800)),
      sig: hrEH > 400 ? 'buy' : 'neutral',
      desc: `Hash Rate BTC : ${display} (blockchain.info). `
        + (hrEH > 500 ? 'Niveau historiquement très élevé — confiance maximale des mineurs.' : 'Réseau sécurisé.'),
    }); live++;
  }

  /* ── Alpha Vantage (cache 24h, fallback si backend absent) ─── */
  if (findInd(data.bourse, 'rsi_spx')?.source !== 'live' && typeof avRsiSpx === 'number' && !isNaN(avRsiSpx)) {
    const v = Math.round(avRsiSpx);
    applyPatch(data.bourse, 'rsi_spx', { val: v, raw: String(v), sig: Calc.rsiSig(avRsiSpx),
      desc: `RSI S&P 500 (SPY) à ${v} (Alpha Vantage, cache 24h).` }); live++;
  }
  if (findInd(data.bourse, 'macd_spx')?.source !== 'live' && avMacdSpx) {
    const bull = avMacdSpx.macd > avMacdSpx.signal;
    applyPatch(data.bourse, 'macd_spx', { sig: bull ? 'buy' : 'sell', val: bull ? 70 : 30,
      raw: bull ? 'Positif' : 'Négatif',
      desc: `MACD SPY ${bull ? 'haussier.' : 'baissier.'} (Alpha Vantage, cache 24h)` }); live++;
  }
  if (findInd(data.matieres, 'goldrsi')?.source !== 'live' && typeof avRsiGld === 'number' && !isNaN(avRsiGld)) {
    const v = Math.round(avRsiGld);
    applyPatch(data.matieres, 'goldrsi', { val: v, raw: String(v), sig: Calc.rsiSig(avRsiGld),
      desc: `RSI Or (GLD ETF) à ${v} (Alpha Vantage, cache 24h).` }); live++;
  }

  APP.liveCount = live;
  return data;
}

/* ══════════════════════════════════════════════════════════════════
   RECOMMANDATION
   ══════════════════════════════════════════════════════════════════ */
function computeReco(groups) {
  let b = 0, s = 0, n = 0, t = 0, excluded = 0;
  groups.forEach(g => g.indicators.forEach(i => {
    if (i.source !== 'live') { excluded++; return; } // ignoré
    t += i.w;
    if (i.sig === 'buy') b += i.w; else if (i.sig === 'sell') s += i.w; else n += i.w;
  }));
  const bp = t ? Math.round(b/t*100) : 0;
  const sp = t ? Math.round(s/t*100) : 0;
  const np = 100 - bp - sp;
  return { sig: bp >= 45 ? 'buy' : sp >= 35 ? 'sell' : 'neutral', bp, sp, np, excluded };
}

/* ══════════════════════════════════════════════════════════════════
   RENDU
   ══════════════════════════════════════════════════════════════════ */
const TABS = [
  { id:'bourse',   label:'Bourse',             icon:'📈' },
  { id:'crypto',   label:'Crypto',              icon:'₿'  },
  { id:'matieres', label:'Matières premières',  icon:'🥇' },
];
const SIG  = { buy:'Achat', sell:'Vente', neutral:'Neutre' };
const RECO = {
  buy:     { arrow:'↑', label:'Acheter',  sub:'Signaux majoritairement haussiers' },
  sell:    { arrow:'↓', label:'Vendre',   sub:'Signaux de distribution détectés'  },
  neutral: { arrow:'—', label:'Attendre', sub:'Signaux mixtes, direction incertaine' },
};
const RECO_DESC = {
  buy: {
    bourse:   'Les indicateurs techniques et de valorisation sont alignés à la hausse. Moment favorable pour renforcer des positions.',
    crypto:   'Métriques on-chain, cycle et technique pointent vers la hausse. Environnement favorable à l\'accumulation.',
    matieres: 'Macro favorable, dollar faible, demande en hausse. Excellent profil risque/rendement sur les matières premières.',
  },
  sell: {
    bourse:   'Valorisation excessive et surchauffe détectées. Prendre des bénéfices et réduire l\'exposition.',
    crypto:   'Indicateurs de cycle en zone de distribution. Sécuriser des profits.',
    matieres: 'Pression du dollar, ralentissement demande — réduire les positions.',
  },
  neutral: {
    bourse:   'Signaux partagés — patience avant de renforcer.',
    crypto:   'Indicateurs divergents — attendre une confirmation directionnelle.',
    matieres: 'Contexte incertain — attendre de meilleures conditions d\'entrée.',
  },
};

function renderTabs() {
  html(gel('tabs'), TABS.map(t =>
    `<button class="tab ${t.id===APP.tab?'active':''}" onclick="switchTab('${t.id}')">
      <span class="tab-icon">${t.icon}</span>${t.label}</button>`).join(''));
}

function renderReco(groups) {
  const r = computeReco(groups), R = RECO[r.sig];
  const live = groups.flatMap(g => g.indicators).filter(i => i.source==='live').length;
  html(gel('reco'), `
    <div class="reco-card reco-${r.sig}">
      <div class="reco-left">
        <div class="reco-label">Recommandation globale</div>
        <div class="reco-signal">${R.arrow} ${R.label}</div>
        <div class="reco-sub">${R.sub}</div>
      </div>
      <div class="reco-mid">
        ${[['Achat',r.bp,'buy'],['Vente',r.sp,'sell'],['Neutre',r.np,'neutral']].map(([l,p,c]) =>
          `<div class="reco-row"><span class="reco-rl">${l}</span>
           <div class="reco-track"><div class="reco-fill ${c}" style="width:${p}%"></div></div>
           <span class="reco-pct">${p} %</span></div>`).join('')}
        <div class="reco-live-count">${live} indicateur${live>1?'s':''} en temps réel${r.excluded ? ` · ${r.excluded} sim. exclus du calcul` : ''}</div>
      </div>
      <div class="reco-right"><p>${RECO_DESC[r.sig][APP.tab]}</p></div>
    </div>`);
}

function renderIndicator(ind) {
  const dots = [1,2,3].map(i => `<span class="wd ${i<=ind.w?'on':'off'}"></span>`).join('');
  const isSim = ind.source !== 'live';

  if (isSim) {
    return `<div class="ind ind-sim">
      <div class="ind-top">
        <div class="ind-name-wrap">
          <span class="ind-name">${ind.name}</span>
          <span class="tag-sim">Non actualisé</span>
        </div>
        <span class="badge badge-${ind.sig}" style="opacity:.4">${SIG[ind.sig]}</span>
      </div>
      <div class="sim-warning">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        Donnée figée — exclue de la recommandation. Valeur du ${new Date().toLocaleDateString('fr-FR', {month:'long', year:'numeric'})}.
      </div>
      <div class="meter ind-sim-meter"><div class="meter-fill ${ind.sig}" style="width:${ind.val}%;opacity:.3"></div></div>
      <div class="ind-foot" style="opacity:.4">
        <div class="weight">${dots}<span class="weight-label">Importance</span></div>
        <span class="ind-val">${ind.raw}${ind.unit}</span>
      </div>
    </div>`;
  }

  const tag = '<span class="tag-live">Live</span>';
  return `<div class="ind">
    <div class="ind-top">
      <div class="ind-name-wrap"><span class="ind-name">${ind.name}</span>${tag}</div>
      <span class="badge badge-${ind.sig}">${SIG[ind.sig]}</span>
    </div>
    <div class="ind-desc">${ind.desc}</div>
    <div class="meter"><div class="meter-fill ${ind.sig}" style="width:${ind.val}%"></div></div>
    <div class="ind-foot">
      <div class="weight">${dots}<span class="weight-label">Importance</span></div>
      <span class="ind-val">${ind.raw}${ind.unit}</span>
    </div>
  </div>`;
}

function renderContent() {
  renderTabs(); renderReco(APP.data[APP.tab]);
  html(gel('content'), APP.data[APP.tab].map(g =>
    `<div class="section"><div class="section-title">${g.name}</div>
     <div class="indicators">${g.indicators.map(renderIndicator).join('')}</div></div>`).join(''));
}

function setStatus(type, text) {
  const dot = gel('status-dot'), txt = gel('status-text');
  if (dot) dot.className = `status-dot ${type}`;
  if (txt) txt.textContent = text;
}

/* ══════════════════════════════════════════════════════════════════
   CONTRÔLEUR
   ══════════════════════════════════════════════════════════════════ */
function switchTab(id) { APP.tab = id; renderContent(); }

async function refresh() {
  if (APP.loading) return;
  APP.loading = true;
  setStatus('loading', 'Actualisation…');
  const btn = gel('refresh-btn');
  if (btn) btn.style.opacity = '0.4';
  try {
    APP.data = await fetchLiveData(defaultData());
    APP.lastUpdate = new Date();
    const ts  = APP.lastUpdate.toLocaleTimeString('fr-FR', { hour:'2-digit', minute:'2-digit' });
    const bk  = Config.backendUrl ? '· Backend ✓' : '· ⚠ Backend non configuré';
    setStatus('live', `${ts} · ${APP.liveCount} live ${bk}`);
    renderContent();
  } catch (e) {
    console.error('[MarketSense]', e);
    setStatus('error', 'Erreur de chargement');
  }
  APP.loading = false;
  if (btn) btn.style.opacity = '1';
}

function openSettings() {
  gel('av-key').value      = Config.avKey;
  gel('backend-url').value = Config.backendUrl;
  gel('settings-overlay').style.display = 'block';
  gel('settings-modal').style.display   = 'block';
}
function closeSettings() {
  gel('settings-overlay').style.display = 'none';
  gel('settings-modal').style.display   = 'none';
}
function saveSettings() {
  Config.avKey      = gel('av-key').value.trim();
  Config.backendUrl = gel('backend-url').value.trim().replace(/\/$/, '');
  closeSettings(); refresh();
}
function toggleTheme() {
  const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next); Config.theme = next;
  const moon = document.querySelector('.icon-moon'), sun = document.querySelector('.icon-sun');
  if (moon && sun) { moon.style.display = next==='dark'?'':'none'; sun.style.display = next==='dark'?'none':''; }
}

/* ── Boot ───────────────────────────────────────────────────────── */
(async function init() {
  document.documentElement.setAttribute('data-theme', Config.theme);
  const moon = document.querySelector('.icon-moon'), sun = document.querySelector('.icon-sun');
  if (Config.theme === 'light' && moon && sun) { moon.style.display='none'; sun.style.display=''; }
  APP.data = defaultData();
  setStatus('loading', 'Connexion aux sources de données…');
  renderContent();
  await refresh();
  setInterval(refresh, 5 * 60 * 1000);
})();

/* ══════════════════════════════════════════════════════════════════
   NOUVELLES FONCTIONNALITÉS v3
   ══════════════════════════════════════════════════════════════════ */

/* ── Config étendue ─────────────────────────────────────────────── */
Object.assign(Config, {
  get alertEmail()    { return localStorage.getItem('ms_alert_email')  || ''; },
  set alertEmail(v)   { localStorage.setItem('ms_alert_email', v); },
  get disabledGroups(){ try { return JSON.parse(localStorage.getItem('ms_disabled_groups') || '[]'); } catch { return []; } },
  set disabledGroups(v){ localStorage.setItem('ms_disabled_groups', JSON.stringify(v)); },
  get compareTab()    { return localStorage.getItem('ms_compare_tab')  || ''; },
  set compareTab(v)   { localStorage.setItem('ms_compare_tab', v); },
});

/* ── État étendu ─────────────────────────────────────────────────── */
APP.history       = {};  // { bourse: [...], crypto: [...], matieres: [...] }
APP.calendar      = [];
APP.compareMode   = false;
APP.calendarOpen  = false;

/* ══════════════════════════════════════════════════════════════════
   REPORTING SIGNAUX → BACKEND (alertes + historique)
   ══════════════════════════════════════════════════════════════════ */
async function reportSignals() {
  const base = Config.backendUrl;
  if (!base) return;
  try {
    const tabs = ['bourse', 'crypto', 'matieres'];
    const body = {};
    tabs.forEach(tab => {
      const r = computeReco(APP.data[tab]);
      body[tab]          = r.sig;
      body[`${tab}_bp`]  = r.bp;
      body[`${tab}_sp`]  = r.sp;
    });
    await fetch(`${base}/api/signals`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (e) { console.warn('[signals]', e.message); }
}

/* ══════════════════════════════════════════════════════════════════
   HISTORIQUE DES SIGNAUX
   ══════════════════════════════════════════════════════════════════ */
async function loadHistory() {
  const base = Config.backendUrl;
  if (!base) return;
  try {
    const r = await fetch(`${base}/api/history?limit=60`);
    if (!r.ok) return;
    const data = await r.json();
    // Reformat: { bourse: [{ts, sig, bp, sp}], ... }
    const tabs = ['bourse', 'crypto', 'matieres'];
    tabs.forEach(tab => {
      APP.history[tab] = data.history
        .filter(h => h[tab])
        .map(h => ({ ts: h.ts, sig: h[tab].sig, bp: h[tab].bp, sp: h[tab].sp }));
    });
  } catch (e) { console.warn('[history]', e.message); }
}

function renderHistorySparkline(tab) {
  const pts = (APP.history[tab] || []).slice(-30);
  if (pts.length < 3) return '';
  const W = 160, H = 28, pad = 2;
  const vals = pts.map(p => p.bp);
  const mn = Math.min(...vals), mx = Math.max(...vals) || 100;
  const x = (i) => pad + (i / (pts.length - 1)) * (W - 2 * pad);
  const y = (v) => H - pad - ((v - mn) / (mx - mn || 1)) * (H - 2 * pad);
  const d = vals.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const last = pts[pts.length - 1];
  const col = last.sig === 'buy' ? 'var(--green)' : last.sig === 'sell' ? 'var(--red)' : 'var(--amber)';
  return `<div class="sparkline-wrap" title="Évolution du signal Achat% sur 30 jours">
    <span class="sparkline-label">30j</span>
    <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="overflow:visible">
      <path d="${d}" fill="none" stroke="${col}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.8"/>
      <circle cx="${x(vals.length-1).toFixed(1)}" cy="${y(vals[vals.length-1]).toFixed(1)}" r="3" fill="${col}"/>
    </svg>
  </div>`;
}

/* ══════════════════════════════════════════════════════════════════
   CALENDRIER MACRO
   ══════════════════════════════════════════════════════════════════ */
async function loadCalendar() {
  const base = Config.backendUrl;
  if (!base) return;
  try {
    const r = await fetch(`${base}/api/calendar?days=90`);
    if (!r.ok) return;
    const data = await r.json();
    APP.calendar = data.events || [];
    renderCalendarBadge();
  } catch (e) { console.warn('[calendar]', e.message); }
}

function renderCalendarBadge() {
  const badge = gel('cal-badge');
  if (badge && APP.calendar.length > 0) {
    const soon = APP.calendar.filter(e => e.days_from_now <= 7).length;
    badge.textContent = soon > 0 ? soon : '';
    badge.style.display = soon > 0 ? 'flex' : 'none';
  }
}

function toggleCalendar() {
  APP.calendarOpen = !APP.calendarOpen;
  const panel = gel('calendar-panel');
  if (!panel) return;
  if (APP.calendarOpen) {
    renderCalendarPanel();
    panel.classList.add('open');
  } else {
    panel.classList.remove('open');
  }
}

function renderCalendarPanel() {
  const panel = gel('calendar-panel');
  if (!panel) return;
  const CAT_ICON = { bourse: '📈', crypto: '₿', matieres: '🥇' };
  const IMP_COLOR = { high: 'var(--red)', medium: 'var(--amber)', low: 'var(--text-3)' };
  const today = new Date().toISOString().split('T')[0];

  const items = APP.calendar.map(ev => {
    const dDay = ev.days_from_now === 0 ? "Aujourd'hui" :
                 ev.days_from_now === 1 ? "Demain" :
                 `Dans ${ev.days_from_now}j`;
    const urgency = ev.days_from_now <= 3 ? 'cal-urgent' : '';
    return `<div class="cal-item ${urgency}">
      <div class="cal-date">
        <div class="cal-day">${new Date(ev.date + 'T12:00:00').toLocaleDateString('fr-FR', {day:'numeric', month:'short'})}</div>
        <div class="cal-dday" style="color:${ev.days_from_now <= 7 ? 'var(--amber)' : 'var(--text-3)'}">${dDay}</div>
      </div>
      <div class="cal-info">
        <div class="cal-event">${CAT_ICON[ev.category] || '📅'} ${ev.event}</div>
        <div class="cal-impact" style="color:${IMP_COLOR[ev.impact] || 'var(--text-3)'}">
          ${'●'.repeat(ev.impact === 'high' ? 3 : ev.impact === 'medium' ? 2 : 1)} ${ev.impact === 'high' ? 'Impact fort' : ev.impact === 'medium' ? 'Impact modéré' : 'Impact faible'}
        </div>
      </div>
    </div>`;
  }).join('');

  panel.innerHTML = `
    <div class="cal-header">
      <span class="cal-title">📅 Calendrier macro</span>
      <button class="icon-btn" onclick="toggleCalendar()">✕</button>
    </div>
    <div class="cal-body">${items || '<p style="color:var(--text-3);padding:16px;text-align:center">Aucun événement à venir</p>'}</div>`;
}

/* ══════════════════════════════════════════════════════════════════
   TOOLTIPS — clic sur indicateur pour voir plus de détails
   ══════════════════════════════════════════════════════════════════ */
function openTooltip(indId) {
  // Chercher l'indicateur dans toutes les données
  let ind = null;
  for (const tab of ['bourse', 'crypto', 'matieres']) {
    for (const g of APP.data[tab]) {
      const found = g.indicators.find(i => i.id === indId);
      if (found) { ind = { ...found, group: g.name, tab }; break; }
    }
    if (ind) break;
  }
  if (!ind) return;

  const SIG_COLOR = { buy: 'var(--green)', sell: 'var(--red)', neutral: 'var(--amber)' };
  const SIG_LABEL = { buy: '↑ Signal d\'Achat', sell: '↓ Signal de Vente', neutral: '— Signal Neutre' };
  const dots = [1,2,3].map(i => `<span class="wd ${i<=ind.w?'on':'off'}"></span>`).join('');

  const overlay = gel('tooltip-overlay');
  const modal   = gel('tooltip-modal');
  if (!overlay || !modal) return;

  modal.innerHTML = `
    <div class="modal-header">
      <div>
        <div style="font-size:11px;color:var(--text-3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">${ind.group}</div>
        <h2 class="modal-title">${ind.name}</h2>
      </div>
      <button class="icon-btn" onclick="closeTooltip()">✕</button>
    </div>
    <div style="padding:1.25rem 1.5rem;border-bottom:1px solid var(--border)">
      <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
        <div class="badge badge-${ind.sig}" style="font-size:14px;padding:6px 16px">${SIG_LABEL[ind.sig]}</div>
        <div style="font-size:22px;font-weight:600;color:var(--text-1)">${ind.raw}${ind.unit}</div>
      </div>
      <div class="meter" style="height:8px;margin-bottom:8px">
        <div class="meter-fill ${ind.sig}" style="width:${ind.val}%"></div>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-3)">
        <span>Survente / Bas</span><span>Zone neutre</span><span>Surachat / Haut</span>
      </div>
    </div>
    <div style="padding:1.25rem 1.5rem;border-bottom:1px solid var(--border)">
      <div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--text-3);margin-bottom:8px">Analyse</div>
      <p style="font-size:14px;color:var(--text-2);line-height:1.7;margin:0">${ind.desc}</p>
    </div>
    <div style="padding:1.25rem 1.5rem;display:flex;justify-content:space-between;align-items:center">
      <div>
        <div style="font-size:11px;color:var(--text-3);margin-bottom:4px">Importance dans la recommandation</div>
        <div class="weight">${dots}<span class="weight-label" style="margin-left:6px">${['','Faible','Modérée','Forte'][ind.w]}</span></div>
      </div>
      <div style="font-size:11px;color:var(--text-3);text-align:right">
        Source<br><span style="color:${ind.source==='live'?'var(--green)':'var(--amber)'}">● ${ind.source==='live'?'Temps réel':'Donnée simulée'}</span>
      </div>
    </div>`;

  overlay.style.display = 'block';
  modal.style.display   = 'block';
}

function closeTooltip() {
  gel('tooltip-overlay').style.display = 'none';
  gel('tooltip-modal').style.display   = 'none';
}

/* ══════════════════════════════════════════════════════════════════
   GROUPES — activer / désactiver dans la recommandation
   ══════════════════════════════════════════════════════════════════ */
function isGroupDisabled(tab, groupName) {
  return Config.disabledGroups.includes(`${tab}::${groupName}`);
}

function toggleGroupDisabled(tab, groupName) {
  const key = `${tab}::${groupName}`;
  const dis = Config.disabledGroups;
  const idx = dis.indexOf(key);
  if (idx >= 0) dis.splice(idx, 1); else dis.push(key);
  Config.disabledGroups = dis;
  renderContent();
}

/* ══════════════════════════════════════════════════════════════════
   MODE COMPARAISON
   ══════════════════════════════════════════════════════════════════ */
function enterCompare(compareTabId) {
  APP.compareMode = true;
  Config.compareTab = compareTabId;
  renderContent();
}
function exitCompare() {
  APP.compareMode = false;
  Config.compareTab = '';
  renderContent();
}

function renderCompareSelector() {
  const others = TABS.filter(t => t.id !== APP.tab);
  return `<div class="compare-bar">
    <span style="font-size:12px;color:var(--text-2)">Comparer avec :</span>
    ${others.map(t => `<button class="compare-btn ${Config.compareTab===t.id&&APP.compareMode?'active':''}"
      onclick="enterCompare('${t.id}')">${t.icon} ${t.label}</button>`).join('')}
    ${APP.compareMode ? '<button class="compare-btn" onclick="exitCompare()">✕ Quitter</button>' : ''}
  </div>`;
}

/* ══════════════════════════════════════════════════════════════════
   EXPORT PDF
   ══════════════════════════════════════════════════════════════════ */
function exportPDF() {
  document.title = `MarketSense — ${TABS.find(t=>t.id===APP.tab)?.label} — ${new Date().toLocaleDateString('fr-FR')}`;
  window.print();
  setTimeout(() => { document.title = 'MarketSense — Aide à l\'investissement'; }, 2000);
}

/* ══════════════════════════════════════════════════════════════════
   RÉÉCRITURE DES FONCTIONS DE RENDU
   ══════════════════════════════════════════════════════════════════ */

// Remplace renderReco pour inclure la sparkline
function renderReco(groups) {
  const r     = computeReco(groups, APP.tab);
  const R     = RECO[r.sig];
  const live  = groups.flatMap(g => g.indicators).filter(i => i.source==='live').length;
  const spark = renderHistorySparkline(APP.tab);

  html(gel('reco'), `
    <div class="reco-card reco-${r.sig}">
      <div class="reco-left">
        <div class="reco-label">Recommandation globale</div>
        <div class="reco-signal">${R.arrow} ${R.label}</div>
        <div class="reco-sub">${R.sub}</div>
        ${spark}
      </div>
      <div class="reco-mid">
        ${[['Achat',r.bp,'buy'],['Vente',r.sp,'sell'],['Neutre',r.np,'neutral']].map(([l,p,c]) =>
          `<div class="reco-row"><span class="reco-rl">${l}</span>
           <div class="reco-track"><div class="reco-fill ${c}" style="width:${p}%"></div></div>
           <span class="reco-pct">${p} %</span></div>`).join('')}
        <div class="reco-live-count">${live} live${r.excluded ? ` · ${r.excluded} sim. exclus` : ''}</div>
      </div>
      <div class="reco-right"><p>${RECO_DESC[r.sig][APP.tab]}</p></div>
    </div>`);
}

// Remplace computeReco pour respecter les groupes désactivés
const _computeRecoOrig = computeReco;
function computeReco(groups, tab) {
  const activeTab = tab || APP.tab;
  let b = 0, s = 0, n = 0, t = 0, excluded = 0;
  groups.forEach(g => {
    const disabled = isGroupDisabled(activeTab, g.name);
    g.indicators.forEach(i => {
      if (i.source !== 'live' || disabled) { excluded++; return; }
      t += i.w;
      if (i.sig === 'buy') b += i.w; else if (i.sig === 'sell') s += i.w; else n += i.w;
    });
  });
  const bp = t ? Math.round(b/t*100) : 0;
  const sp = t ? Math.round(s/t*100) : 0;
  const np = 100 - bp - sp;
  return { sig: bp >= 45 ? 'buy' : sp >= 35 ? 'sell' : 'neutral', bp, sp, np, excluded };
}

// Remplace renderIndicator pour ajouter le tooltip au clic
function renderIndicator(ind) {
  const dots  = [1,2,3].map(i => `<span class="wd ${i<=ind.w?'on':'off'}"></span>`).join('');
  const isSim = ind.source !== 'live';
  const click = `onclick="openTooltip('${ind.id}')" style="cursor:pointer" title="Cliquer pour les détails"`;

  if (isSim) {
    return `<div class="ind ind-sim" ${click}>
      <div class="ind-top">
        <div class="ind-name-wrap"><span class="ind-name">${ind.name}</span><span class="tag-sim">Non actualisé</span></div>
        <span class="badge badge-${ind.sig}" style="opacity:.4">${SIG[ind.sig]}</span>
      </div>
      <div class="sim-warning">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        Donnée figée — exclue de la recommandation.
      </div>
      <div class="meter"><div class="meter-fill ${ind.sig}" style="width:${ind.val}%;opacity:.3"></div></div>
      <div class="ind-foot" style="opacity:.4">
        <div class="weight">${dots}<span class="weight-label">Importance</span></div>
        <span class="ind-val">${ind.raw}${ind.unit}</span>
      </div>
    </div>`;
  }

  return `<div class="ind" ${click}>
    <div class="ind-top">
      <div class="ind-name-wrap"><span class="ind-name">${ind.name}</span><span class="tag-live">Live</span></div>
      <span class="badge badge-${ind.sig}">${SIG[ind.sig]}</span>
    </div>
    <div class="ind-desc">${ind.desc}</div>
    <div class="meter"><div class="meter-fill ${ind.sig}" style="width:${ind.val}%"></div></div>
    <div class="ind-foot">
      <div class="weight">${dots}<span class="weight-label">Importance</span></div>
      <span class="ind-val">${ind.raw}${ind.unit}</span>
    </div>
  </div>`;
}

// Remplace renderContent pour gérer la comparaison et les groupes désactivés
function renderContent() {
  renderTabs();
  const groups = APP.data[APP.tab];
  renderReco(groups);

  const renderGroups = (tabId, data) => data.map(g => {
    const disabled = isGroupDisabled(tabId, g.name);
    return `<div class="section ${disabled ? 'section-disabled' : ''}">
      <div class="section-title">
        ${g.name}
        <button class="group-toggle" onclick="toggleGroupDisabled('${tabId}','${g.name.replace(/'/g,"\\'")}')" title="${disabled ? 'Réactiver ce groupe' : 'Désactiver du calcul'}">
          ${disabled ? '⊕' : '⊖'}
        </button>
      </div>
      <div class="indicators">${g.indicators.map(renderIndicator).join('')}</div>
    </div>`;
  }).join('');

  if (APP.compareMode && Config.compareTab && Config.compareTab !== APP.tab) {
    const compareGroups = APP.data[Config.compareTab];
    const compareTab    = TABS.find(t => t.id === Config.compareTab);
    const mainTab       = TABS.find(t => t.id === APP.tab);
    html(gel('content'), `
      ${renderCompareSelector()}
      <div class="compare-grid">
        <div class="compare-col">
          <div class="compare-col-title">${mainTab?.icon} ${mainTab?.label}</div>
          ${renderGroups(APP.tab, groups)}
        </div>
        <div class="compare-col">
          <div class="compare-col-title">${compareTab?.icon} ${compareTab?.label}</div>
          ${renderGroups(Config.compareTab, compareGroups)}
        </div>
      </div>`);
  } else {
    html(gel('content'), renderCompareSelector() + renderGroups(APP.tab, groups));
  }
}

/* ══════════════════════════════════════════════════════════════════
   PARAMÈTRES ÉTENDUS
   ══════════════════════════════════════════════════════════════════ */
function openSettings() {
  gel('av-key').value        = Config.avKey;
  gel('backend-url').value   = Config.backendUrl;
  const emailEl = gel('alert-email');
  if (emailEl) emailEl.value = Config.alertEmail;
  gel('settings-overlay').style.display = 'block';
  gel('settings-modal').style.display   = 'block';
}
function saveSettings() {
  Config.avKey       = (gel('av-key')?.value      || '').trim();
  Config.backendUrl  = (gel('backend-url')?.value || '').trim().replace(/\/$/, '');
  const emailEl      = gel('alert-email');
  if (emailEl) Config.alertEmail = emailEl.value.trim();
  closeSettings();
  refresh();
}

/* ══════════════════════════════════════════════════════════════════
   REFRESH ÉTENDU
   ══════════════════════════════════════════════════════════════════ */
async function refresh() {
  if (APP.loading) return;
  APP.loading = true;
  setStatus('loading', 'Actualisation…');
  const btn = gel('refresh-btn');
  if (btn) btn.style.opacity = '0.4';
  try {
    APP.data = await fetchLiveData(defaultData());
    APP.lastUpdate = new Date();
    const ts = APP.lastUpdate.toLocaleTimeString('fr-FR', { hour:'2-digit', minute:'2-digit' });
    const bk = Config.backendUrl ? '· Backend ✓' : '· ⚠ Backend non configuré';
    setStatus('live', `${ts} · ${APP.liveCount} live ${bk}`);

    // Nouvelles fonctions asynchrones en parallèle
    await Promise.all([
      reportSignals(),
      loadHistory(),
      loadCalendar(),
    ]);

    renderContent();
  } catch (e) {
    console.error('[MarketSense]', e);
    setStatus('error', 'Erreur de chargement');
  }
  APP.loading = false;
  if (btn) btn.style.opacity = '1';
}

/* ══════════════════════════════════════════════════════════════════
   BOOT ÉTENDU
   ══════════════════════════════════════════════════════════════════ */
(async function initV3() {
  // Fonctions de fermeture globales
  window.closeSettings = function() {
    gel('settings-overlay').style.display = 'none';
    gel('settings-modal').style.display   = 'none';
  };
})();
