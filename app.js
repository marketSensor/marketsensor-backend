/* ═══════════════════════════════════════════════════════════════════
   MarketSense — app.js  (version unifiée, une seule définition par fn)
   ═══════════════════════════════════════════════════════════════════ */
'use strict';

/* ── État global ─────────────────────────────────────────────────── */
const APP = {
  tab: 'bourse', data: null, loading: false,
  lastUpdate: null, liveCount: 0,
  history: {}, calendar: [],
  compareMode: false, calendarOpen: false,
};

/* ── Config (localStorage) ───────────────────────────────────────── */
const AV_KEY     = 'E85V2XISD5ZDIWRS';
const BACKEND    = 'https://web-production-981fc.up.railway.app';

const Config = {
  get theme()         { return localStorage.getItem('ms_theme')           || 'dark'; },
  set theme(v)        { localStorage.setItem('ms_theme', v); },
  get alertEmail()    { return localStorage.getItem('ms_alert_email')     || ''; },
  set alertEmail(v)   { localStorage.setItem('ms_alert_email', v); },
  get compareTab()    { return localStorage.getItem('ms_compare_tab')     || ''; },
  set compareTab(v)   { localStorage.setItem('ms_compare_tab', v); },
  get disabledGroups(){
    try { return JSON.parse(localStorage.getItem('ms_disabled_groups') || '[]'); } catch { return []; }
  },
  set disabledGroups(v){ localStorage.setItem('ms_disabled_groups', JSON.stringify(v)); },
};

/* ── DOM helpers ─────────────────────────────────────────────────── */
const gel  = id  => document.getElementById(id);
const html = (el, h) => { if (el) el.innerHTML = h; };

/* ══════════════════════════════════════════════════════════════════
   API MODULE
   ══════════════════════════════════════════════════════════════════ */
const Api = {
  async get(url, label = '') {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 10000);
      const r = await fetch(url, { signal: ctrl.signal, cache: 'no-store' });
      clearTimeout(t);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } catch (e) { console.warn(`[API] ${label}:`, e.message); return null; }
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
      'BTC Prices');
    return d?.prices ? d.prices.map(p => p[1]) : null;
  },
  async blockchainStats() {
    const d = await this.get('https://api.blockchain.info/stats', 'Blockchain');
    return d?.hash_rate ? { hashRate: d.hash_rate } : null;
  },
  async backend(path = '/api/indicators') {
    const base = BACKEND.replace(/\/$/, '');
    if (!base) return null;
    return await this.get(`${base}${path}`, 'Backend');
  },
  _avRead(k)    { try { const i = JSON.parse(localStorage.getItem(`ms_av_${k}`) || 'null'); return (!i || Date.now()-i.ts > 86400000) ? null : i.data; } catch { return null; } },
  _avWrite(k,d) { try { localStorage.setItem(`ms_av_${k}`, JSON.stringify({data:d,ts:Date.now()})); } catch {} },
  async avRSI(symbol) {
    const c = this._avRead(`rsi_${symbol}`);
    if (c !== null) return c;
    if (!AV_KEY) return null;
    const d = await this.get(`https://www.alphavantage.co/query?function=RSI&symbol=${symbol}&interval=daily&time_period=14&series_type=close&apikey=${AV_KEY}`, `AV RSI ${symbol}`);
    const a = d?.['Technical Analysis: RSI'];
    if (!a) return null;
    const v = parseFloat(Object.values(a)[0].RSI);
    this._avWrite(`rsi_${symbol}`, v); return v;
  },
  async avMACD(symbol) {
    const c = this._avRead(`macd_${symbol}`);
    if (c !== null) return c;
    if (!AV_KEY) return null;
    const d = await this.get(`https://www.alphavantage.co/query?function=MACD&symbol=${symbol}&interval=daily&series_type=close&apikey=${AV_KEY}`, `AV MACD ${symbol}`);
    const a = d?.['Technical Analysis: MACD'];
    if (!a) return null;
    const l = Object.values(a)[0];
    const v = { macd: parseFloat(l.MACD), signal: parseFloat(l.MACD_Signal) };
    this._avWrite(`macd_${symbol}`, v); return v;
  },
};

/* ── Calculs crypto (client) ─────────────────────────────────────── */
const Calc = {
  rsi(prices, p=14) {
    if (!prices||prices.length<p+1) return null;
    let g=0,l=0;
    for(let i=1;i<=p;i++){const d=prices[i]-prices[i-1];d>0?g+=d:l-=d;}
    let ag=g/p,al=l/p;
    for(let i=p+1;i<prices.length;i++){const d=prices[i]-prices[i-1];ag=(ag*(p-1)+Math.max(0,d))/p;al=(al*(p-1)+Math.max(0,-d))/p;}
    return al===0?100:Math.round(100-100/(1+ag/al));
  },
  ema(prices,p){if(!prices||prices.length<p)return null;const k=2/(p+1);let e=prices.slice(0,p).reduce((a,b)=>a+b,0)/p;for(let i=p;i<prices.length;i++)e=prices[i]*k+e*(1-k);return e;},
  sma(prices,p){if(!prices||prices.length<p)return null;const s=prices.slice(-p);return s.reduce((a,b)=>a+b,0)/s.length;},
  macdSign(prices){const e12=this.ema(prices,12),e26=this.ema(prices,26);return(e12&&e26)?e12-e26:null;},
  norm(v,lo,hi){return Math.min(100,Math.max(0,Math.round((v-lo)/(hi-lo)*100)));},
  rsiSig(v){return v<35?'buy':v>65?'sell':'neutral';},
  fngSig(v){return v<30?'buy':v>70?'sell':'neutral';},
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

  // Lance toutes les requêtes en parallèle — chacune retourne null si elle échoue
  const results = await Promise.allSettled([
    Api.cryptoFearGreed(),
    Api.cgGlobal(),
    Api.btcPrices(365),
    Api.blockchainStats(),
    Api.backend(),
    Api.avRSI('SPY'),
    Api.avMACD('SPY'),
    Api.avRSI('GLD'),
  ]);

  const [fng, cgGlobal, prices, blockchain, backend, avRsiSpx, avMacdSpx, avRsiGld]
    = results.map(r => r.status === 'fulfilled' ? r.value : null);

  // ── Backend → bourse + crypto + matières ─────────────────────
  try {
    if (backend) {
      for (const [section, groups] of [
        ['bourse', data.bourse], ['crypto', data.crypto], ['matieres', data.matieres]
      ]) {
        for (const [id, vals] of Object.entries(backend[section] || {})) {
          try { if (applyPatch(groups, id, vals)) live++; } catch(_) {}
        }
      }
      // Stocker les analytics du backend
      if (backend.analytics) APP.analytics = backend.analytics;
    }
  } catch(e) { console.warn('[fetchLive] backend patch:', e.message); }

  // ── Crypto Fear & Greed ───────────────────────────────────────
  try {
    if (fng) {
      const v = fng.value;
      applyPatch(data.crypto, 'cfg', {
        val: v, raw: String(v), sig: Calc.fngSig(v),
        desc: `Indice à ${v} (${fng.label}) — `
          + (v > 75 ? 'euphorie extrême, risque de correction élevé.'
           : v < 25 ? "peur extrême — opportunité d'achat historique."
           : v > 55 ? 'sentiment optimiste, vigilance conseillée.' : 'sentiment neutre.'),
      }); live++;
    }
  } catch(e) { console.warn('[fetchLive] FNG:', e.message); }

  // ── BTC Dominance ─────────────────────────────────────────────
  try {
    if (cgGlobal) {
      const dom = Math.round(cgGlobal.btcDom * 10) / 10;
      applyPatch(data.crypto, 'btcdom', {
        val: Math.min(100, Math.round(dom)), raw: dom.toFixed(1),
        sig: dom > 58 ? 'sell' : dom < 42 ? 'buy' : 'neutral',
        desc: `Dominance BTC à ${dom.toFixed(1)} % (CoinGecko) — `
          + (dom > 58 ? 'Bitcoin ultra-dominant, altcoins sous pression.'
           : dom < 45 ? 'Potentielle altseason, rotations vers les altcoins.'
           : 'Marché crypto équilibré.'),
      }); live++;
    }
  } catch(e) { console.warn('[fetchLive] BTC dom:', e.message); }

  // ── BTC Prices → RSI, MACD, Pi Cycle ─────────────────────────
  try {
    if (prices && prices.length >= 30) {
      const rsi = Calc.rsi(prices, 14);
      if (rsi !== null) {
        applyPatch(data.crypto, 'btcrsi', {
          val: rsi, raw: String(rsi), sig: Calc.rsiSig(rsi),
          desc: `RSI BTC à ${rsi} (${prices.length}j, CoinGecko) — `
            + (rsi > 70 ? 'suracheté.' : rsi < 30 ? "survendu — opportunité d'achat." : 'zone neutre.'),
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
            ? `Pi Cycle proche du déclenchement (${(ratio*100).toFixed(0)} %).`
            : `Pi Cycle non déclenché (ratio ${(ratio*100).toFixed(0)} %) — loin du sommet.`,
        }); live++;
      }
    }
  } catch(e) { console.warn('[fetchLive] BTC prices:', e.message); }

  // ── Hash Rate ─────────────────────────────────────────────────
  try {
    if (blockchain && blockchain.hashRate) {
      const hr = blockchain.hashRate;
      const hrEH = hr > 1e8 ? hr / 1e9 : hr > 1e5 ? hr / 1e6 : hr;
      const display = `${Math.round(hrEH)} EH/s`;
      applyPatch(data.crypto, 'hashrate', {
        raw: display, val: Math.min(97, Calc.norm(hrEH, 100, 800)),
        sig: hrEH > 400 ? 'buy' : 'neutral',
        desc: `Hash Rate BTC : ${display} (blockchain.info).`,
      }); live++;
    }
  } catch(e) { console.warn('[fetchLive] hashrate:', e.message); }

  // ── Alpha Vantage (cache 24h) ─────────────────────────────────
  try {
    if (findInd(data.bourse, 'rsi_spx')?.source !== 'live' && typeof avRsiSpx === 'number' && !isNaN(avRsiSpx)) {
      const v = Math.round(avRsiSpx);
      applyPatch(data.bourse, 'rsi_spx', { val: v, raw: String(v), sig: Calc.rsiSig(avRsiSpx),
        desc: `RSI S&P 500 (SPY) à ${v} — Alpha Vantage, cache 24h.` }); live++;
    }
    if (findInd(data.bourse, 'macd_spx')?.source !== 'live' && avMacdSpx) {
      const bull = avMacdSpx.macd > avMacdSpx.signal;
      applyPatch(data.bourse, 'macd_spx', { sig: bull ? 'buy' : 'sell', val: bull ? 70 : 30,
        raw: bull ? 'Positif' : 'Négatif',
        desc: `MACD SPY ${bull ? 'haussier' : 'baissier'} — Alpha Vantage, cache 24h.` }); live++;
    }
    if (findInd(data.matieres, 'goldrsi')?.source !== 'live' && typeof avRsiGld === 'number' && !isNaN(avRsiGld)) {
      const v = Math.round(avRsiGld);
      applyPatch(data.matieres, 'goldrsi', { val: v, raw: String(v), sig: Calc.rsiSig(avRsiGld),
        desc: `RSI Or (GLD ETF) à ${v} — Alpha Vantage, cache 24h.` }); live++;
    }
  } catch(e) { console.warn('[fetchLive] AV:', e.message); }

  APP.liveCount = live;
  return data;
}



/* ══════════════════════════════════════════════════════════════════
   RECOMMANDATION + CONSTANTES UI
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

function computeReco(groups, tab) {
  const activeTab = tab || APP.tab;
  let b=0, s=0, n=0, t=0, excluded=0, totalLive=0, aligned=0;
  groups.forEach(g => {
    const disabled = isGroupDisabled(activeTab, g.name);
    g.indicators.forEach(i => {
      if (i.source !== 'live' || disabled) { excluded++; return; }
      totalLive++;
      t += i.w;
      if (i.sig==='buy')       { b += i.w; }
      else if (i.sig==='sell') { s += i.w; }
      else                     { n += i.w; }
    });
  });
  const bp  = t ? Math.round(b/t*100) : 0;
  const sp  = t ? Math.round(s/t*100) : 0;
  const sig = bp>=45 ? 'buy' : sp>=35 ? 'sell' : 'neutral';

  // Nombre d'indicateurs alignés avec le signal dominant
  groups.forEach(g => {
    if (isGroupDisabled(activeTab, g.name)) return;
    g.indicators.forEach(i => {
      if (i.source !== 'live') return;
      if (i.sig === sig) aligned++;
    });
  });

  // Score de confiance 0-100
  const confidence = totalLive > 0 ? Math.round(aligned / totalLive * 100) : 0;
  const confLabel  = confidence >= 75 ? 'Fort' : confidence >= 50 ? 'Modéré' : 'Faible';
  const confColor  = confidence >= 75 ? 'var(--green)' : confidence >= 50 ? 'var(--amber)' : 'var(--red)';

  return { sig, bp, sp, np:100-bp-sp, excluded, totalLive, aligned, confidence, confLabel, confColor };
}

const TABS = [
  { id:'bourse',   label:'Bourse',            icon:'📈' },
  { id:'crypto',   label:'Crypto',             icon:'₿'  },
  { id:'matieres', label:'Matières premières', icon:'🥇' },
];
const SIG  = { buy:'Achat', sell:'Vente', neutral:'Neutre' };
const RECO = {
  buy:     { arrow:'↑', label:'Acheter',  sub:'Signaux majoritairement haussiers' },
  sell:    { arrow:'↓', label:'Vendre',   sub:'Signaux de distribution détectés'  },
  neutral: { arrow:'—', label:'Attendre', sub:'Signaux mixtes, direction incertaine' },
};
const RECO_DESC = {
  buy:  { bourse:'Les indicateurs techniques et de valorisation sont alignés à la hausse. Moment favorable pour renforcer des positions.', crypto:'Métriques on-chain, cycle et technique pointent vers la hausse. Environnement favorable à l\'accumulation.', matieres:'Macro favorable, dollar faible, demande en hausse. Excellent profil risque/rendement sur les matières premières.' },
  sell: { bourse:'Valorisation excessive et surchauffe détectées. Prendre des bénéfices et réduire l\'exposition.', crypto:'Indicateurs de cycle en zone de distribution. Sécuriser des profits.', matieres:'Pression du dollar, ralentissement demande — réduire les positions.' },
  neutral: { bourse:'Signaux partagés — patience avant de renforcer.', crypto:'Indicateurs divergents — attendre une confirmation directionnelle.', matieres:'Contexte incertain — attendre de meilleures conditions d\'entrée.' },
};

/* ══════════════════════════════════════════════════════════════════
   RENDU — SPARKLINE HISTORIQUE
   ══════════════════════════════════════════════════════════════════ */
function renderHistorySparkline(tab) {
  const pts = (APP.history[tab] || []).slice(-30);
  if (pts.length === 0) return '<div class="sparkline-empty">Historique en cours de constitution…</div>';
  if (pts.length === 1) {
    const col = pts[0].sig==='buy'?'var(--green)':pts[0].sig==='sell'?'var(--red)':'var(--amber)';
    return `<div class="sparkline-wrap"><span class="sparkline-label">1pt</span><svg width="160" height="28"><circle cx="80" cy="14" r="4" fill="${col}"/></svg></div>`;
  }
  const W=160,H=28,pad=3;
  const vals = pts.map(p=>p.bp);
  const mn=Math.min(...vals), mx=Math.max(...vals,mn+1);
  const x=i=>pad+(i/(pts.length-1))*(W-2*pad);
  const y=v=>H-pad-((v-mn)/(mx-mn))*(H-2*pad);
  const d=vals.map((v,i)=>`${i===0?'M':'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const last=pts[pts.length-1];
  const col=last.sig==='buy'?'var(--green)':last.sig==='sell'?'var(--red)':'var(--amber)';
  return `<div class="sparkline-wrap" title="Évolution Achat% (${pts.length} points)">
    <span class="sparkline-label">${pts.length}pts</span>
    <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="overflow:visible">
      <path d="${d}" fill="none" stroke="${col}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.8"/>
      <circle cx="${x(vals.length-1).toFixed(1)}" cy="${y(vals[vals.length-1]).toFixed(1)}" r="3" fill="${col}"/>
    </svg>
  </div>`;
}

/* ── Reco card ───────────────────────────────────────────────────── */
function renderReco(groups) {
  const r=computeReco(groups), R=RECO[r.sig];
  const live =groups.flatMap(g=>g.indicators).filter(i=>i.source==='live').length;
  const spark=renderHistorySparkline(APP.tab);

  // Divergences détectées pour cet onglet
  const divs = (APP.analytics?.divergences||[]).filter(d => {
    if (APP.tab==='crypto') return ['Bitcoin','Ethereum','Solana'].includes(d.asset);
    if (APP.tab==='bourse') return ['S&P 500'].includes(d.asset);
    if (APP.tab==='matieres') return ['Or','Pétrole WTI','Cuivre'].includes(d.asset);
    return false;
  });
  const divBanner = divs.length > 0 ? `
    <div class="div-banner">
      <span class="div-banner-icon">⚡</span>
      <div>
        <strong>${divs.length} divergence${divs.length>1?'s':''} détectée${divs.length>1?'s':''}</strong>
        ${divs.map(d=>`<div class="div-item div-${d.type}">${d.desc}</div>`).join('')}
      </div>
    </div>` : '';

  html(gel('reco'), `
    ${divBanner}
    <div class="reco-card reco-${r.sig}">
      <div class="reco-left">
        <div class="reco-label">Recommandation globale</div>
        <div class="reco-signal">${R.arrow} ${R.label}</div>
        <div class="reco-sub">${R.sub}</div>
        <div class="conf-badge" style="border-color:${r.confColor}">
          <span style="color:${r.confColor}">●</span>
          Signal <strong style="color:${r.confColor}">${r.confLabel}</strong>
          — ${r.aligned}/${r.totalLive} indicateurs alignés
        </div>
        ${spark}
      </div>
      <div class="reco-mid">
        ${[['Achat',r.bp,'buy'],['Vente',r.sp,'sell'],['Neutre',r.np,'neutral']].map(([l,p,c])=>
          `<div class="reco-row"><span class="reco-rl">${l}</span>
           <div class="reco-track"><div class="reco-fill ${c}" style="width:${p}%"></div></div>
           <span class="reco-pct">${p} %</span></div>`).join('')}
        <div class="reco-live-count">${live} live${r.excluded?` · ${r.excluded} sim. exclus`:''}</div>
      </div>
      <div class="reco-right"><p>${RECO_DESC[r.sig][APP.tab]}</p></div>
    </div>`);
}

/* ── Indicateur card ─────────────────────────────────────────────── */
function renderIndicator(ind) {
  const dots=[1,2,3].map(i=>`<span class="wd ${i<=ind.w?'on':'off'}"></span>`).join('');
  const clickAttr=`onclick="openTooltip('${ind.id}')" style="cursor:pointer"`;
  if (ind.source !== 'live') {
    return `<div class="ind ind-sim" ${clickAttr}>
      <div class="ind-top">
        <div class="ind-name-wrap"><span class="ind-name">${ind.name}</span><span class="tag-sim">Non actualisé</span></div>
        <span class="badge badge-${ind.sig}" style="opacity:.4">${SIG[ind.sig]}</span>
      </div>
      <div class="sim-warning">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        Donnée figée — exclue de la recommandation.
      </div>
      <div class="meter"><div class="meter-fill ${ind.sig}" style="width:${ind.val}%;opacity:.3"></div></div>
      <div class="ind-foot" style="opacity:.4"><div class="weight">${dots}<span class="weight-label">Importance</span></div><span class="ind-val">${ind.raw}${ind.unit}</span></div>
    </div>`;
  }
  return `<div class="ind ind-clickable" ${clickAttr}>
    <div class="ind-top">
      <div class="ind-name-wrap"><span class="ind-name">${ind.name}</span><span class="tag-live">Live</span></div>
      <div style="display:flex;align-items:center;gap:6px">
        <span class="badge badge-${ind.sig}">${SIG[ind.sig]}</span>
        <span class="ind-detail-hint">⋯</span>
      </div>
    </div>
    <div class="ind-desc">${ind.desc}</div>
    <div class="meter"><div class="meter-fill ${ind.sig}" style="width:${ind.val}%"></div></div>
    <div class="ind-foot"><div class="weight">${dots}<span class="weight-label">Importance</span></div><span class="ind-val">${ind.raw}${ind.unit}</span></div>
  </div>`;
}

/* ── Barre de comparaison ────────────────────────────────────────── */
function renderCompareSelector() {
  const others = TABS.filter(t => t.id !== APP.tab);
  return `<div class="compare-bar">
    <span style="font-size:12px;color:var(--text-2)">Comparer :</span>
    ${others.map(t=>`<button class="compare-btn ${Config.compareTab===t.id&&APP.compareMode?'active':''}" onclick="enterCompare('${t.id}')">${t.icon} ${t.label}</button>`).join('')}
    ${APP.compareMode?'<button class="compare-btn" onclick="exitCompare()">✕ Quitter</button>':''}
  </div>`;
}

/* ── Groupes ─────────────────────────────────────────────────────── */
function renderGroups(tabId, data) {
  return data.map(g => {
    const disabled = isGroupDisabled(tabId, g.name);
    const safeName = g.name.replace(/\\/g,'\\\\').replace(/'/g,"\\'");
    return `<div class="section ${disabled?'section-disabled':''}">
      <div class="section-title">
        ${g.name}
        <button class="group-toggle" onclick="toggleGroupDisabled('${tabId}','${safeName}')" title="${disabled?'Réactiver':'Désactiver du calcul'}">${disabled?'⊕':'⊖'}</button>
      </div>
      <div class="indicators">${g.indicators.map(renderIndicator).join('')}</div>
    </div>`;
  }).join('');
}

/* ── renderContent principal ─────────────────────────────────────── */
function renderContent() {
  try {
    renderTabs();
    const groups = APP.data ? APP.data[APP.tab] : null;
    if (!groups) return;
    renderReco(groups);
    const bar = renderCompareSelector();
    if (APP.compareMode && Config.compareTab && Config.compareTab !== APP.tab) {
      const cGroups = APP.data[Config.compareTab];
      const cTab    = TABS.find(t=>t.id===Config.compareTab);
      const mTab    = TABS.find(t=>t.id===APP.tab);
      html(gel('content'), bar + `<div class="compare-grid">
        <div class="compare-col"><div class="compare-col-title">${mTab?.icon} ${mTab?.label}</div>${renderGroups(APP.tab, groups)}</div>
        <div class="compare-col"><div class="compare-col-title">${cTab?.icon} ${cTab?.label}</div>${renderGroups(Config.compareTab, cGroups)}</div>
      </div>`);
    } else {
      html(gel('content'), bar + renderGroups(APP.tab, groups));
    }
  } catch(e) {
    console.error('[renderContent]', e);
    // Fallback minimal sans risque d'erreur récursive
    try {
      const groups = APP.data?.[APP.tab] || [];
      html(gel('content'), groups.map(g=>
        `<div class="section"><div class="section-title">${g.name}</div>
         <div class="indicators">${g.indicators.map(i=>`<div class="ind">
           <div class="ind-top"><span class="ind-name">${i.name}</span><span class="badge badge-${i.sig}">${SIG[i.sig]}</span></div>
           <div class="ind-desc">${i.desc}</div></div>`).join('')}</div></div>`).join(''));
    } catch(_) {}
  }
}

function renderTabs() {
  html(gel('tabs'), TABS.map(t=>
    `<button class="tab ${t.id===APP.tab?'active':''}" onclick="switchTab('${t.id}')">
      <span class="tab-icon">${t.icon}</span>${t.label}</button>`).join(''));
}

function setStatus(type, text) {
  const dot=gel('status-dot'), txt=gel('status-text');
  if(dot) dot.className=`status-dot ${type}`;
  if(txt) txt.textContent=text;
}

/* ══════════════════════════════════════════════════════════════════
   CALENDRIER MACRO
   ══════════════════════════════════════════════════════════════════ */
APP.analytics  = null;
APP.analyticsPanelOpen = false;

async function loadAnalytics(data) {
  if (!data?.analytics) return;
  APP.analytics = data.analytics;
}

/* ── Panel Analytics ─────────────────────────────────────────────── */
function toggleAnalytics() {
  APP.analyticsPanelOpen = !APP.analyticsPanelOpen;
  const panel = gel('analytics-panel');
  if (!panel) return;
  if (APP.analyticsPanelOpen) {
    renderAnalyticsPanel();
    panel.classList.add('open');
    // Si pas de données : lancer un refresh puis re-rendre
    if (!APP.analytics) {
      refresh().then(() => { if (APP.analyticsPanelOpen) renderAnalyticsPanel(); });
    }
  } else {
    panel.classList.remove('open');
  }
}

function renderAnalyticsPanel() {
  const panel = gel('analytics-panel');
  if (!panel) return;

  if (!APP.analytics) {
    panel.innerHTML = `
      <div class="cal-header">
        <span class="cal-title">📊 Analyse avancée</span>
        <button class="icon-btn" onclick="toggleAnalytics()">✕</button>
      </div>
      <div style="padding:40px 24px;text-align:center">
        <div style="font-size:32px;margin-bottom:16px">⏳</div>
        <div style="font-size:14px;font-weight:500;color:var(--text-1);margin-bottom:8px">Calcul en cours…</div>
        <div style="font-size:12px;color:var(--text-3);margin-bottom:24px;line-height:1.6">
          Le backend calcule les corrélations, divergences et backtesting.<br>
          Cette opération prend 20-60 secondes au premier chargement.
        </div>
        <button class="btn-primary" onclick="refresh().then(()=>renderAnalyticsPanel())">
          ⟳ Rafraîchir les données
        </button>
      </div>`;
    return;
  }

  const { correlations, divergences, backtest } = APP.analytics;

  // ── Score de confiance global ───────────────────────────────────
  const confScores = ['bourse','crypto','matieres'].map(tab => {
    const r = computeReco(APP.data[tab], tab);
    return `<div class="conf-card conf-${r.sig}">
      <div class="conf-tab">${TABS.find(t=>t.id===tab)?.icon} ${TABS.find(t=>t.id===tab)?.label}</div>
      <div class="conf-signal" style="color:${r.confColor}">${RECO[r.sig].arrow} ${RECO[r.sig].label}</div>
      <div class="conf-score">
        <div class="conf-bar-track"><div class="conf-bar-fill" style="width:${r.confidence}%;background:${r.confColor}"></div></div>
        <span style="color:${r.confColor};font-weight:600">${r.confLabel}</span>
      </div>
      <div style="font-size:11px;color:var(--text-3);margin-top:4px">${r.aligned}/${r.totalLive} indicateurs alignés</div>
    </div>`;
  }).join('');

  // ── Divergences ─────────────────────────────────────────────────
  const divHtml = divergences.length > 0
    ? divergences.map(d => `
        <div class="div-row div-${d.type}">
          <span class="div-icon">${d.type==='bullish'?'↑':'↓'}</span>
          <div>
            <strong>${d.asset}</strong>
            <div style="font-size:12px;color:var(--text-2);margin-top:2px">${d.desc}</div>
          </div>
          <span class="badge badge-${d.sig}" style="flex-shrink:0">${d.type==='bullish'?'Achat potentiel':'Vente potentielle'}</span>
        </div>`).join('')
    : '<div style="padding:16px;color:var(--text-3);text-align:center;font-size:13px">Aucune divergence détectée actuellement.</div>';

  // ── Matrice de corrélation ──────────────────────────────────────
  let corrHtml = '<div style="padding:16px;color:var(--text-3);text-align:center">Non disponible</div>';
  if (correlations?.assets?.length > 0) {
    const { assets, matrix } = correlations;
    const cellSize = Math.min(52, Math.floor(320 / assets.length));
    const corrColor = v => {
      const abs = Math.abs(v);
      if (v > 0.7)  return '#1fd97e';
      if (v > 0.3)  return '#4ade80';
      if (v > 0)    return '#a7f3d0';
      if (v > -0.3) return '#fca5a5';
      if (v > -0.7) return '#f87171';
      return '#ef4444';
    };
    const shortName = n => n.length > 6 ? n.slice(0,5)+'…' : n;
    corrHtml = `<div style="overflow-x:auto;padding:4px">
      <table class="corr-table">
        <thead><tr><th></th>${assets.map(a=>`<th title="${a}">${shortName(a)}</th>`).join('')}</tr></thead>
        <tbody>${matrix.map((row, i) =>
          `<tr><td class="corr-label" title="${assets[i]}">${shortName(assets[i])}</td>
          ${row.map((v, j) => i===j
            ? `<td class="corr-cell" style="background:var(--bg-panel);color:var(--text-3)">—</td>`
            : `<td class="corr-cell" style="background:${corrColor(v)}20;color:${corrColor(v)}" title="${assets[i]} / ${assets[j]}: ${v}">${v.toFixed(2)}</td>`
          ).join('')}</tr>`
        ).join('')}</tbody>
      </table>
      <div class="corr-legend">
        <span style="color:#1fd97e">■ Forte corrélation positive</span>
        <span style="color:var(--text-3)">■ Neutre</span>
        <span style="color:#ef4444">■ Corrélation négative</span>
      </div>
    </div>`;
  }

  // ── Backtesting ─────────────────────────────────────────────────
  const backHtml = backtest.length > 0
    ? backtest.map(b => {
        const col = b.direction==='buy'
          ? (b.avg_90d > 0 ? 'var(--green)' : 'var(--red)')
          : (b.avg_90d < 0 ? 'var(--green)' : 'var(--red)');
        const mini = b.last_5.map(r =>
          `<span style="color:${r>0?'var(--green)':'var(--red)'};">${r>0?'+':''}${r}%</span>`
        ).join(' · ');
        return `<div class="back-card">
          <div class="back-header">
            <span class="back-asset">${b.asset}</span>
            <span class="back-signal">${b.signal}</span>
            <span class="back-occ">${b.occurrences} fois</span>
          </div>
          <div class="back-stats">
            <div class="back-stat"><div class="back-stat-val" style="color:${col}">${b.avg_90d>0?'+':''}${b.avg_90d}%</div><div class="back-stat-lbl">Rendement moyen 90j</div></div>
            <div class="back-stat"><div class="back-stat-val" style="color:${b.win_rate>=60?'var(--green)':'var(--amber)'}">${b.win_rate}%</div><div class="back-stat-lbl">Taux de succès</div></div>
          </div>
          <div class="back-mini">Dernières occurrences : ${mini}</div>
        </div>`;
      }).join('')
    : '<div style="padding:16px;color:var(--text-3);text-align:center;font-size:13px">Données insuffisantes pour le backtesting.</div>';

  panel.innerHTML = `
    <div class="cal-header">
      <span class="cal-title">📊 Analyse avancée</span>
      <button class="icon-btn" onclick="toggleAnalytics()">✕</button>
    </div>
    <div class="analytics-body">
      <div class="analytics-section">
        <div class="analytics-title">🎯 Score de confiance par onglet</div>
        <div class="conf-grid">${confScores}</div>
      </div>
      <div class="analytics-section">
        <div class="analytics-title">⚡ Divergences détectées (20 derniers jours)</div>
        <div class="div-list">${divHtml}</div>
      </div>
      <div class="analytics-section">
        <div class="analytics-title">🔗 Matrice de corrélation (rendements 1 an)</div>
        ${corrHtml}
      </div>
      <div class="analytics-section">
        <div class="analytics-title">📈 Backtesting RSI historique</div>
        <div style="font-size:11px;color:var(--text-3);margin-bottom:12px">Performance observée 90 jours après chaque signal sur l'historique complet.</div>
        <div class="back-grid">${backHtml}</div>
      </div>
    </div>`;
}

async function loadCalendar() {
  const base = BACKEND.replace(/\/$/, '');
  if (!base) return;
  try {
    const r = await fetch(`${base}/api/calendar?days=180`);
    if (!r.ok) return;
    const d = await r.json();
    APP.calendar = d.events || [];
    const badge = gel('cal-badge');
    if (badge) {
      const soon = APP.calendar.filter(e=>e.days_from_now<=7).length;
      badge.textContent = soon > 0 ? soon : '';
      badge.style.display = soon > 0 ? 'flex' : 'none';
    }
  } catch(e) { console.warn('[calendar]', e.message); }
}

function toggleCalendar() {
  APP.calendarOpen = !APP.calendarOpen;
  const panel = gel('calendar-panel');
  if (!panel) return;
  if (APP.calendarOpen) {
    const CAT = {bourse:'📈',crypto:'₿',matieres:'🥇'};
    const IMP = {high:'var(--red)',medium:'var(--amber)',low:'var(--text-3)'};
    const items = APP.calendar.map(ev => {
      const dDay = ev.days_from_now===0?"Aujourd'hui":ev.days_from_now===1?"Demain":`Dans ${ev.days_from_now}j`;
      return `<div class="cal-item ${ev.days_from_now<=3?'cal-urgent':''}">
        <div class="cal-date">
          <div class="cal-day">${new Date(ev.date+'T12:00:00').toLocaleDateString('fr-FR',{day:'numeric',month:'short'})}</div>
          <div class="cal-dday" style="color:${ev.days_from_now<=7?'var(--amber)':'var(--text-3)'}">${dDay}</div>
        </div>
        <div class="cal-info">
          <div class="cal-event">${CAT[ev.category]||'📅'} ${ev.event}</div>
          <div class="cal-impact" style="color:${IMP[ev.impact]||'var(--text-3)'}">
            ${'●'.repeat(ev.impact==='high'?3:ev.impact==='medium'?2:1)} ${ev.impact==='high'?'Impact fort':ev.impact==='medium'?'Impact modéré':'Impact faible'}
          </div>
        </div>
      </div>`;
    }).join('');
    panel.innerHTML = `
      <div class="cal-header">
        <span class="cal-title">📅 Calendrier macro — ${APP.calendar.length} événements</span>
        <button class="icon-btn" onclick="toggleCalendar()">✕</button>
      </div>
      <div class="cal-body">${items||'<p style="color:var(--text-3);padding:20px;text-align:center">Aucun événement<br><small>Configurez le backend dans ⚙</small></p>'}</div>`;
    panel.classList.add('open');
  } else {
    panel.classList.remove('open');
  }
}

/* ══════════════════════════════════════════════════════════════════
   HISTORIQUE DES SIGNAUX
   ══════════════════════════════════════════════════════════════════ */
async function reportSignals() {
  const base = BACKEND.replace(/\/$/, '');
  if (!base) return;
  try {
    const body = {};
    ['bourse','crypto','matieres'].forEach(tab => {
      if (!APP.data[tab]) return;
      const r = computeReco(APP.data[tab], tab);
      body[tab]=r.sig; body[`${tab}_bp`]=r.bp; body[`${tab}_sp`]=r.sp;
    });
    await fetch(`${base}/api/signals`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  } catch(e) { console.warn('[signals]', e.message); }
}

async function loadHistory() {
  const base = BACKEND.replace(/\/$/, '');
  if (!base) return;
  try {
    const r = await fetch(`${base}/api/history?limit=60`);
    if (!r.ok) return;
    const d = await r.json();
    ['bourse','crypto','matieres'].forEach(tab => {
      APP.history[tab] = d.history.filter(h=>h[tab]).map(h=>({ts:h.ts,sig:h[tab].sig,bp:h[tab].bp,sp:h[tab].sp}));
    });
  } catch(e) { console.warn('[history]', e.message); }
}

/* ══════════════════════════════════════════════════════════════════
   TOOLTIP avec explications pédagogiques
   ══════════════════════════════════════════════════════════════════ */
const INDICATOR_INFO = {
  rsi_spx:{what:"Le RSI mesure la vélocité des variations de prix sur 14 jours, de 0 à 100.",why:"Il permet de détecter les excès de marché. L'un des indicateurs les plus utilisés par les traders professionnels depuis 1978.",how:"< 30 : survendu, rebond probable. > 70 : suracheté, correction possible. Zone 30-70 : neutre.",creator:"J. Welles Wilder Jr. (1978)"},
  macd_spx:{what:"Différence entre EMA 12j et EMA 26j. La ligne de signal est l'EMA 9j du MACD.",why:"Combine tendance et momentum. Le croisement avec sa ligne de signal est un des signaux de retournement les plus fiables.",how:"MACD > ligne signal : haussier. En dessous : baissier. La divergence MACD/prix anticipe les retournements.",creator:"Gerald Appel (1979)"},
  vix:{what:"Mesure la volatilité implicite attendue du S&P 500 sur 30 jours, calculée sur les prix d'options.",why:"Reflète l'anxiété institutionnelle. Un VIX élevé = panique = opportunité contrariante. Un VIX bas = complaisance = danger.",how:"< 15 : complaisance. 15-25 : normal. > 30 : peur, opportunité contrariante. > 40 : capitulation, points d'achat historiques.",creator:"CBOE (1993)"},
  cape:{what:"Divise le cours S&P 500 par la moyenne des bénéfices réels des 10 dernières années, éliminant les biais cycliques.",why:"Shiller a prouvé qu'un CAPE élevé prédit des rendements futurs faibles sur 10 ans. Confirmé historiquement.",how:"Moy. historique ~16x. > 30 : marchés chers. > 40 : bulle. < 12 : attractif. Peut rester élevé en période de taux bas.",creator:"Robert Shiller, Yale (1988)"},
  fg_spx:{what:"Agrège 7 indicateurs CNN : momentum, force des actions, options Put/Call, junk bonds, demande de valeur refuge, volatilité.",why:"Quantifie l'irrationnalité du marché. 'Soyez avide quand les autres ont peur' (Buffett). Exploite les extrêmes émotionnels.",how:"0-24 : peur extrême → opportunité. 25-49 : peur. 50-74 : cupidité. 75-100 : cupidité extrême → prudence.",creator:"CNN Money"},
  putcall:{what:"Compare le volume d'options Put (vente) aux options Call (achat).",why:"Les options révèlent les anticipations institutionnelles. Excès de puts = peur déjà dans les prix = signal contrariant positif.",how:"> 1.0 : excès de puts, haussier contrariant. < 0.7 : excès de calls, euphorie dangereuse. ~0.85 : équilibré.",creator:"CBOE"},
  bollinger:{what:"Deux bandes à ±2 écarts-types de la MM20. 95% des prix restent dans les bandes.",why:"Mesurent la volatilité relative. La compression des bandes précède les grands mouvements directionnels.",how:"Position > 80% : surachat technique. < 20% : survente. Compression = mouvement fort imminent (direction inconnue).",creator:"John Bollinger (1980s)"},
  mvrv:{what:"Compare la capitalisation BTC (Market Value) à la valeur réalisée (coût moyen de tous les BTC). Z-Score normalise l'écart.",why:"L'un des meilleurs indicateurs on-chain pour identifier bulles et capitulations. Mesure si les holders sont globalement en profit.",how:"Z > 7 : bulle (vente). Z 2-7 : optimisme. Z 0-2 : neutre. Z < 0 : capitulation, achat historique majeur.",creator:"David Puell & Murad Mahmudov"},
  nupl:{what:"Mesure le pourcentage net de BTC en profit non réalisé parmi tous les détenteurs.",why:"En euphorie, tout le monde est en profit → pression vendeuse latente. En capitulation → signal de fond de marché.",how:"< 0 : capitulation. 0-0.25 : espoir. 0.25-0.5 : optimisme. 0.5-0.75 : excitation. > 0.75 : euphorie → distribuer.",creator:"Glassnode"},
  sopr:{what:"Ratio profit/perte des BTC déplacés chaque jour. SOPR > 1 = vendeurs en profit.",why:"Quand SOPR < 1, les holders vendent à perte → capitulation → fond de marché historique.",how:"> 1.14 : distribution forte. ≈ 1 : équilibre sain. < 0.98 : capitulation → opportunité historique.",creator:"Renato Shirakashi / Glassnode"},
  cdd:{what:"Pondère les mouvements BTC par leur ancienneté. 1 BTC immobile 100j qui bouge = 100 coin days destroyed.",why:"Détecte les 'anciens holders' (baleines, early adopters). Quand ils bougent après longtemps → souvent pour prendre profits aux sommets.",how:"CDD très élevé : anciens holders distribuent → sommet potentiel. Faible : ils conservent → signal haussier.",creator:"Glassnode"},
  nvt:{what:"Network Value to Transactions. Le 'P/E du Bitcoin' : capitalisation / volume de transactions blockchain.",why:"Mesure si le réseau est correctement valorisé vs son activité réelle. NVT élevé = prix déconnecté de l'utilisation.",how:"< 50 : réseau activement utilisé, solide fondamentalement. 50-150 : normal. > 150 : surévaluation potentielle.",creator:"Willy Woo"},
  picycle:{what:"Croisement MM111 avec 2×MM350. Ces nombres approximent π (Pi ≈ 3.14), d'où le nom.",why:"A prédit les 3 derniers sommets de cycle BTC avec une précision de quelques jours. Signal de fin de bull market.",how:"MM111 < 2×MM350 : pas de signal, favorable. Croisement : signal de sommet historique → réduire drastiquement l'exposition.",creator:"Harold Christopher Burger"},
  puell:{what:"Compare les revenus journaliers des mineurs à leur moyenne 365j. Mesure la profitabilité relative du minage.",why:"Les mineurs sont des vendeurs naturels. Revenus très élevés → pression vendeuse max. Revenus très bas → fond de marché.",how:"> 4 : distribution (vente). 0.5-4 : normal. < 0.5 : mineurs en détresse → accumulation historique.",creator:"David Puell"},
  rainbow:{what:"Modélise le prix BTC sur une régression logarithmique depuis 2009, avec 9 bandes de couleur.",why:"BTC suit une croissance logarithmique avec cycles de 4 ans. Visualise où le prix se situe dans le cycle long terme.",how:"Zones 1-2 (bleu) : achat exceptionnel. 3-4 : accumuler. 5 : conserver. 6-7 : vigilance. 8-9 (rouge) : vendre.",creator:"Über Holger (modèle log)"},
  mayer:{what:"Prix BTC actuel divisé par sa MM200. Mesure l'écart entre le prix et sa tendance long terme.",why:"La MM200 est la référence universelle. Trace Mayer a calculé que > 2.4 correspond aux bulles, < 1.0 aux opportunités.",how:"< 0.8 : sous la MM200, achat historique. 0.8-1.5 : neutre. > 2.4 : zone de vente historique. Moyenne historique ≈ 1.34.",creator:"Trace Mayer"},
  btcrsim:{what:"RSI calculé sur les clôtures mensuelles BTC plutôt que journalières.",why:"Filtre le bruit et capte les signaux de cycle. Historiquement, RSI mensuel > 90 = proche d'un sommet majeur.",how:"> 90 : sommet de cycle probable, réduire l'exposition. 70-90 : bull market avancé. 40-70 : sain. < 40 : survente mensuelle.",creator:"Analyse technique classique"},
  hashrate:{what:"Puissance de calcul totale du réseau Bitcoin en Exahash/seconde.",why:"Les mineurs investissent des millions. Hash Rate en hausse = ils anticipent des prix futurs plus élevés. ATH Hash Rate = confiance professionnelle maximale.",how:"Croissant : signal haussier. Chute soudaine : capitulation des mineurs (souvent aux fonds de bear market).",creator:"Blockchain.info (temps réel)"},
  cfg:{what:"Agrège 5 facteurs : volatilité (25%), momentum (25%), réseaux sociaux (15%), dominance BTC (10%), Google Trends (10%).",why:"Les marchés crypto amplifient peur et FOMO. Cet indice quantifie ces extrêmes émotionnels pour les exploiter de manière contrariante.",how:"0-24 : peur extrême → accumulation. 25-49 : peur. 50-74 : cupidité. 75-100 : cupidité extrême → distribuer.",creator:"Alternative.me"},
  funding:{what:"Taux d'intérêt payé entre longs et courts sur les futures perpétuels, rééquilibré toutes les 8h.",why:"Mesure l'excès spéculatif en temps réel. Funding positif élevé = longs surpayent = risque de liquidations en cascade.",how:"> 0.05%/8h : excès de longs, prudence. 0-0.05% : neutre. Négatif : excès de shorts, compression possible.",creator:"BitMEX (pionnier, 2014)"},
  dxy:{what:"Mesure le dollar contre 6 devises majeures (EUR 57.6%, JPY 13.6%, GBP 11.9%, CAD 9.1%, SEK 4.2%, CHF 3.6%).",why:"Les matières premières sont libellées en dollars. Dollar fort → commodités plus chères pour les acheteurs étrangers → baisse demande.",how:"DXY en hausse : pression sur les matières premières. En baisse : soutien structurel. MM200 = frontière clé.",creator:"ICE Futures US"},
  realrates:{what:"Taux nominaux MOINS l'inflation anticipée. Mesuré directement par les TIPS (obligations indexées inflation).",why:"L'or ne génère pas de revenus. Son coût d'opportunité dépend des taux réels. Taux réels négatifs = détenir de l'or est rationnel.",how:"< 0% : très favorable à l'or. 0-1% : neutre. > 2% : pression sur l'or. Chaque +1% de taux réels = -10-15% sur l'or.",creator:"Réserve Fédérale / FRED"},
  goldsil:{what:"Nombre d'onces d'argent pour acheter une once d'or. Fluctue entre ~40 et ~120 historiquement.",why:"Quand le ratio est élevé, l'argent est sous-évalué vs l'or et tend à surperformer lors du prochain cycle haussier.",how:"> 80 : argent bon marché, le favoriser. 60-80 : normal. < 60 : argent cher vs or. Moy. historique ~50-60.",creator:"Analyse historique des métaux"},
  gold_oil_ratio:{what:"Prix de l'or divisé par le prix du pétrole WTI. Indique combien de barils achète une once d'or.",why:"Indicateur macroéconomique : en expansion, le pétrole s'apprécie plus (ratio bas). En récession, l'or surperforme (ratio haut).",how:"< 15 : expansion, favorable aux actifs risqués. 15-30 : normal. > 30 : stress économique ou récession.",creator:"Analyse macroéconomique"},
  platpall:{what:"Compare Platine et Palladium, tous deux utilisés dans les convertisseurs catalytiques automobiles.",why:"Historiquement Pt > Pd. Depuis 2018 inversé (diesel→essence). La transition VE réduira les deux. Normalisations attendues.",how:"Ratio < 1 (Pt < Pd) : platine à décote historique, potentiel de rattrapage. > 1 : normalisation en cours.",creator:"London Platinum & Palladium Market"},

  /* ── ETFs boursiers ──────────────────────────────────────────── */
  rsi_cw8:  {what:"RSI 14 jours de l'ETF Amundi MSCI World (CW8), couvrant ~1600 grandes et moyennes capitalisations dans 23 pays développés.",why:"Le MSCI World représente l'essentiel de la capitalisation boursière mondiale développée. C'est le baromètre d'un portefeuille diversifié international.",how:"< 30 : marché mondial survendu, opportunité. > 70 : suracheté. La MM200 est le signal de tendance long terme le plus fiable pour ce type d'ETF.",creator:"Amundi / MSCI"},
  macd_cw8: {what:"MACD de l'ETF CW8 (Amundi MSCI World).",why:"Détecte les retournements de tendance sur les marchés développés mondiaux. Signal clé pour les investisseurs long terme.",how:"Positif : tendance haussière mondiale confirmée. Négatif : tendance baissière. À croiser avec la MM200.",creator:"Amundi / MSCI"},
  mm200_cw8:{what:"Position du CW8 par rapport à sa moyenne mobile 200 jours.",why:"La MM200 est le filtre de tendance long terme par excellence. Au-dessus = bull market. En dessous = bear market.",how:"Au-dessus : rester investi. En dessous : prudence, envisager de réduire l'exposition.",creator:"Analyse technique"},
  rsi_ese:  {what:"RSI 14 jours de l'ETF Amundi S&P 500 (ESE), répliquant les 500 plus grandes entreprises américaines.",why:"Le S&P 500 est l'indice le plus suivi au monde. Son RSI capte les excès du marché américain, dominant mondial.",how:"< 30 : marché US survendu. > 70 : suracheté. En tendance haussière forte, le RSI peut rester > 70 plusieurs mois.",creator:"Amundi / S&P Dow Jones Indices"},
  mm200_ese:{what:"Position de l'ESE (S&P 500 ETF) par rapport à sa MM200.",why:"La MM200 du S&P 500 est LA frontière surveillée par tous les gérants institutionnels. Franchissement = signal majeur.",how:"Au-dessus : bull market US intact. En dessous avec pente descendante : bear market → prudence importante.",creator:"Analyse technique"},
  rsi_paeem:{what:"RSI 14 jours de l'ETF Amundi MSCI Emerging Markets (PAEEM), couvrant 24 marchés émergents.",why:"Les marchés émergents offrent une croissance supérieure mais avec plus de volatilité. Un RSI bas peut offrir des opportunités.",how:"< 30 : marchés émergents survendus, potentiel intéressant. > 70 : suracheté. Très sensible au dollar (DXY) et aux taux.",creator:"Amundi / MSCI"},
  mm200_paeem:{what:"Position du PAEEM par rapport à sa MM200.",why:"Les émergents sous leur MM200 sont souvent pénalisés par un dollar fort ou des flux sortants. Signal de tendance essentiel.",how:"Au-dessus : dynamique positive sur les émergents. En dessous : flux sortants, dollar fort → réduire l'exposition.",creator:"Analyse technique"},
  rsi_paasi:{what:"RSI 14 jours de l'ETF Amundi MSCI Asia Pacific ex Japan (PAASI).",why:"L'Asie (Chine, Inde, Corée, Taïwan...) représente le moteur de croissance mondial de demain. Forte pondération technologie.",how:"< 30 : Asie survendue, potentiel de rebond. > 70 : suracheté. Très sensible aux tensions géopolitiques et au cycle Chine.",creator:"Amundi / MSCI"},
  mm200_paasi:{what:"Position du PAASI (Asie Pac. ex-Japon) par rapport à sa MM200.",why:"Filtre de tendance long terme sur les marchés asiatiques. Déterminant pour les décisions d'allocation géographique.",how:"Au-dessus : dynamique asiatique positive. En dessous : ralentissement ou risques régionaux → prudence.",creator:"Analyse technique"},

  /* ── Altcoins ────────────────────────────────────────────────── */
  rsi_eth:{what:"RSI 14 jours d'Ethereum (ETH-USD), la deuxième plus grande cryptomonnaie.",why:"Ethereum est la plateforme de smart contracts dominante (DeFi, NFT, Layer 2). Son RSI détecte les excès spéculatifs sur cet actif à forte volatilité.",how:"< 30 : ETH survendu, opportunité. > 70 : suracheté. Les cycles d'ETH sont corrélés à BTC mais amplifiés.",creator:"Analyse technique / CoinGecko"},
  vs_btc_eth:{what:"Performance relative d'Ethereum vs Bitcoin sur 90 jours.",why:"Mesure si ETH surperforme ou sous-performe BTC. Quand ETH surperforme, c'est souvent le signal d'une phase 'altseason'.",how:"Positif (ETH > BTC) : rotation vers les altcoins en cours. Négatif : BTC dominant, altcoins en retrait.",creator:"Analyse de marché"},
  rsi_sol:{what:"RSI 14 jours de Solana (SOL-USD), blockchain haute performance.",why:"Solana est une des blockchains à la croissance la plus rapide. Très volatile, son RSI identifie les points extrêmes de spéculation.",how:"< 30 : SOL survendu. > 70 : suracheté. SOL est très corrélé à BTC/ETH mais amplifie fortement les mouvements.",creator:"Analyse technique / CoinGecko"},
  vs_btc_sol:{what:"Performance relative de Solana vs Bitcoin sur 90 jours.",why:"Indicateur de force relative. SOL surperformant BTC = marché risk-on, appétit pour les altcoins à fort bêta.",how:"Fortement positif : SOL en phase spéculative. Négatif : rotation vers BTC (risk-off crypto).",creator:"Analyse de marché"},
  rsi_hype:{what:"RSI 14 jours de Hyperliquid (HYPE), token du DEX perpétuel le plus utilisé.",why:"HYPE est lié à l'écosystème DeFi et aux volumes de trading décentralisé. Token récent (2024) à très forte volatilité.",how:"< 30 : survendu, potentiel rebond. > 70 : suracheté. Données limitées (token < 1 an) → interprétation avec prudence.",creator:"Analyse technique / CoinGecko"},
  vs_btc_hype:{what:"Performance relative de HYPE vs Bitcoin sur 90 jours.",why:"Mesure si le secteur DeFi/DEX surperforme BTC. Signal d'intérêt pour les actifs DeFi spéculatifs.",how:"Très positif : intérêt fort pour les DEX et DeFi. Négatif : rotation vers des actifs plus sûrs.",creator:"Analyse de marché"},

  /* ── Uranium & énergie ───────────────────────────────────────── */
  rsi_ura:{what:"RSI 14 jours du URA ETF (Global X Uranium ETF), panier de sociétés liées à l'uranium.",why:"L'uranium est en déficit structurel d'offre. Le RSI de l'ETF capte les excès de spéculation sur ce secteur de niche à fort potentiel.",how:"< 30 : secteur uranium survendu. > 70 : suracheté après une hausse. La tendance long terme reste haussière (demande nucléaire).",creator:"Global X / Analyse technique"},
  rsi_urnm:{what:"RSI 14 jours du URNM (Sprott Uranium Miners ETF), le plus pur proxy des mineurs d'uranium.",why:"URNM est concentré sur les producteurs purs (Cameco, Kazatomprom) contrairement à URA plus diversifié. Signal direct sur le secteur.",how:"< 30 : mineurs uranium survendus, opportunité. > 70 : excès spéculatif. Plus volatil que URA.",creator:"Sprott Asset Management"},
  rsi_ccj:{what:"RSI 14 jours de Cameco Corp (CCJ), le plus grand producteur d'uranium occidental.",why:"Cameco est le baromètre du secteur uranium. Contrôle ~15% de la production mondiale. Son RSI reflète la santé du secteur.",how:"< 30 : Cameco survendu, opportunité d'entrée sur le leader du secteur. > 70 : excès spéculatif.",creator:"Analyse technique / yfinance"},
  rsi_wti:{what:"RSI 14 jours du pétrole WTI (West Texas Intermediate), référence américaine du pétrole brut.",why:"Le pétrole est le commodity le plus influent sur l'économie mondiale. Son RSI capte les déséquilibres offre/demande à court terme.",how:"< 30 : pétrole survendu (souvent lié à une récession ou excès d'offre). > 70 : suracheté (tensions géopolitiques, OPEC).",creator:"NYMEX / CME Group"},
  mm200_wti:{what:"Position du WTI par rapport à sa moyenne mobile 200 jours.",why:"La MM200 du pétrole sépare les régimes haussiers (favorable à l'inflation) des régimes baissiers (désinflation).",how:"Au-dessus : tendance haussière, favorable aux actifs réels. En dessous : pression déflationniste.",creator:"Analyse technique"},
  rsi_brent:{what:"RSI 14 jours du pétrole Brent, référence internationale du brut.",why:"Le Brent est la référence de 2/3 des échanges mondiaux de pétrole. Différentiel avec WTI reflète les dynamiques géopolitiques.",how:"< 30 : survendu. > 70 : suracheté. À surveiller avec le DXY et les décisions OPEC+.",creator:"ICE Futures Europe"},
  rsi_ng:{what:"RSI 14 jours du Gaz Naturel (Natural Gas Futures, NG=F).",why:"Le gaz est extrêmement volatil — le RSI est particulièrement utile pour identifier les excès sur ce marché saisonnier.",how:"< 30 : gaz survendu (souvent en été). > 70 : suracheté (hiver, tensions géopolitiques). Saisonnalité forte.",creator:"NYMEX / CME Group"},

  /* ── Métaux industriels ──────────────────────────────────────── */
  rsi_copper:{what:"RSI 14 jours du cuivre (HG=F), le métal le plus utilisé dans la transition énergétique.",why:"Le cuivre est le 'Dr. Copper' — son prix prédit l'activité économique mondiale. Un RSI bas peut offrir une entrée sur ce métal structurellement demandé.",how:"< 30 : cuivre survendu, opportunité. > 70 : suracheté. Demande structurelle par EVs, IA, réseaux électriques.",creator:"CME Group / LME"},
  mm200_copper:{what:"Position du cuivre par rapport à sa MM200.",why:"La MM200 du cuivre est un baromètre de la croissance mondiale. Au-dessus = expansion économique. En dessous = ralentissement.",how:"Au-dessus : croissance mondiale confirmée, favorable aux actifs risqués. En dessous : signal de ralentissement.",creator:"Analyse technique"},
  rsi_gold:{what:"RSI 14 jours de l'or (GC=F, Gold Futures).",why:"L'or est la valeur refuge ultime. Son RSI identifie les moments où la peur ou la cupidité ont poussé le prix à un extrême.",how:"< 30 : or survendu, accumulation opportune. > 70 : or suracheté à court terme, mais tendance haussière peut continuer si contexte macro favorable.",creator:"COMEX / CME Group"},
  perf1y_gold:{what:"Performance de l'or sur les 12 derniers mois.",why:"La performance annuelle de l'or reflète le contexte macro global : inflation, géopolitique, confiance dans les banques centrales.",how:"> +15% : tendance haussière forte. 0-15% : appréciation modérée. Négatif : or sous pression (taux réels élevés ou dollar fort).",creator:"COMEX / CME Group"},
};


function openTooltip(indId) {
  let ind = null;
  for (const tab of ['bourse','crypto','matieres']) {
    for (const g of (APP.data?.[tab] || [])) {
      const f = g.indicators.find(i=>i.id===indId);
      if (f) { ind={...f,group:g.name,tab}; break; }
    }
    if (ind) break;
  }
  if (!ind) return;
  const overlay=gel('tooltip-overlay'), modal=gel('tooltip-modal');
  if (!overlay||!modal) return;
  const info = INDICATOR_INFO[ind.id] || null;
  const dots = [1,2,3].map(i=>`<span class="wd ${i<=ind.w?'on':'off'}"></span>`).join('');
  const SIG_LBL={buy:"↑ Achat",sell:"↓ Vente",neutral:"— Neutre"};
  const SIG_COL={buy:'var(--green)',sell:'var(--red)',neutral:'var(--amber)'};
  const educ = info ? `
    <div style="padding:1.25rem 1.5rem;border-bottom:1px solid var(--border)">
      <div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--text-3);margin-bottom:12px">📚 Comprendre cet indicateur</div>
      <div style="margin-bottom:10px"><div style="font-size:11px;font-weight:600;color:var(--accent);margin-bottom:3px">Qu'est-ce que c'est ?</div><p style="font-size:13px;color:var(--text-2);line-height:1.65;margin:0">${info.what}</p></div>
      <div style="margin-bottom:10px"><div style="font-size:11px;font-weight:600;color:var(--accent);margin-bottom:3px">Pourquoi c'est pertinent ?</div><p style="font-size:13px;color:var(--text-2);line-height:1.65;margin:0">${info.why}</p></div>
      <div style="padding:12px;background:var(--bg-panel);border-radius:var(--radius-sm);border:1px solid var(--border)"><div style="font-size:11px;font-weight:600;color:var(--text-1);margin-bottom:4px">🎯 Comment l'interpréter</div><p style="font-size:13px;color:var(--text-1);line-height:1.65;margin:0">${info.how}</p></div>
      ${info.creator?`<div style="margin-top:8px;font-size:11px;color:var(--text-3)">📖 ${info.creator}</div>`:''}
    </div>` : '';
  modal.innerHTML = `
    <div class="modal-header">
      <div><div style="font-size:11px;color:var(--text-3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">${ind.group}</div><h2 class="modal-title">${ind.name}</h2></div>
      <button class="icon-btn" onclick="closeTooltip()">✕</button>
    </div>
    <div style="padding:1.25rem 1.5rem;border-bottom:1px solid var(--border)">
      <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px;flex-wrap:wrap">
        <span class="badge badge-${ind.sig}" style="font-size:13px;padding:5px 14px">${SIG_LBL[ind.sig]}</span>
        <span style="font-size:24px;font-weight:700;color:${SIG_COL[ind.sig]}">${ind.raw}${ind.unit}</span>
      </div>
      <div class="meter" style="height:8px;margin-bottom:6px"><div class="meter-fill ${ind.sig}" style="width:${ind.val}%"></div></div>
      <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-3)"><span>Survente</span><span>Neutre</span><span>Surachat</span></div>
      <p style="margin:12px 0 0;font-size:13px;color:var(--text-2);line-height:1.6">${ind.desc}</p>
    </div>
    ${educ}
    <div style="padding:1rem 1.5rem;display:flex;justify-content:space-between;align-items:center;background:var(--bg-panel)">
      <div><div style="font-size:10px;color:var(--text-3);margin-bottom:4px">Importance</div><div class="weight">${dots}<span class="weight-label" style="margin-left:6px">${['','Faible','Modérée','Forte'][ind.w]}</span></div></div>
      <div style="text-align:right"><div style="font-size:10px;color:var(--text-3);margin-bottom:4px">Source</div><span style="font-size:12px;color:${ind.source==='live'?'var(--green)':'var(--amber)'}">● ${ind.source==='live'?'Temps réel':'Donnée simulée'}</span></div>
    </div>`;
  overlay.style.display='block'; modal.style.display='block'; modal.scrollTop=0;
}
function closeTooltip() {
  const o=gel('tooltip-overlay'),m=gel('tooltip-modal');
  if(o) o.style.display='none'; if(m) m.style.display='none';
}

/* ══════════════════════════════════════════════════════════════════
   COMPARAISON + EXPORT + PARAMÈTRES + CONTRÔLEUR
   ══════════════════════════════════════════════════════════════════ */
function enterCompare(id) { APP.compareMode=true; Config.compareTab=id; renderContent(); }
function exitCompare()    { APP.compareMode=false; Config.compareTab=''; renderContent(); }
function exportPDF() {
  document.title=`MarketSense — ${TABS.find(t=>t.id===APP.tab)?.label} — ${new Date().toLocaleDateString('fr-FR')}`;
  window.print();
  setTimeout(()=>{ document.title='MarketSense — Aide à l\'investissement'; }, 2000);
}
function openSettings() {
  const emEl = gel('alert-email');
  if (emEl) emEl.value = Config.alertEmail;
  gel('settings-overlay').style.display = 'block';
  gel('settings-modal').style.display   = 'block';
}
function closeSettings() {
  gel('settings-overlay').style.display = 'none';
  gel('settings-modal').style.display   = 'none';
}
function saveSettings() {
  Config.alertEmail = (gel('alert-email')?.value || '').trim();
  closeSettings();
  refresh();
}
function toggleTheme() {
  const next=document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark';
  document.documentElement.setAttribute('data-theme',next); Config.theme=next;
  const moon=document.querySelector('.icon-moon'),sun=document.querySelector('.icon-sun');
  if(moon&&sun){moon.style.display=next==='dark'?'':'none';sun.style.display=next==='dark'?'none':'';}
}
function switchTab(id) { APP.tab=id; renderContent(); }

/* ── Refresh principal ───────────────────────────────────────────── */
async function refresh() {
  if (APP.loading) return;
  APP.loading=true;
  setStatus('loading','Actualisation…');
  const btn=gel('refresh-btn');
  if(btn) btn.style.opacity='0.4';
  try {
    APP.data=await fetchLiveData(defaultData());
    APP.lastUpdate=new Date();
    const ts=APP.lastUpdate.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});
    const bk=BACKEND?'· Backend ✓':'· ⚠ Backend non configuré';
    setStatus('live',`${ts} · ${APP.liveCount} live ${bk}`);
    await Promise.all([reportSignals(), loadHistory(), loadCalendar()]);
    renderContent();
  } catch(e) {
    console.error('[refresh]', e);
    setStatus('error', `Erreur : ${e.message || e}`);
    if (APP.data) renderContent();
  }
  APP.loading=false;
  if(btn) btn.style.opacity='1';
}

/* ── Boot ────────────────────────────────────────────────────────── */
(async function init() {
  document.documentElement.setAttribute('data-theme', Config.theme);
  const moon=document.querySelector('.icon-moon'),sun=document.querySelector('.icon-sun');
  if(Config.theme==='light'&&moon&&sun){moon.style.display='none';sun.style.display='';}
  APP.data=defaultData();
  APP.history={};
  setStatus('loading','Connexion aux sources de données…');
  renderContent();            // Affichage immédiat avec données par défaut
  await refresh();            // Puis enrichissement live
  setInterval(refresh, 5*60*1000);
})();
