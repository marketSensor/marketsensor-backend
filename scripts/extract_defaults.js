// Régénère indicator_defaults.json depuis le data.js du frontend :
//   node scripts/extract_defaults.js /chemin/vers/marketsensor/data.js > scripts/indicator_defaults.json
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const start = src.indexOf('function defaultData()');
if (start < 0) throw new Error('defaultData() introuvable');
let i = src.indexOf('{', start), depth = 0, end = -1;
for (let k = i; k < src.length; k++) {           // appariement des accolades
  const c = src[k];
  if (c === '{') depth++;
  else if (c === '}' && --depth === 0) { end = k + 1; break; }
}
const data = new Function(src.slice(start, end) + '; return defaultData();')();
const out = {};
for (const tab of ['bourse', 'crypto', 'matieres']) {
  out[tab] = data[tab].map(g => ({
    name: g.name,
    indicators: g.indicators.map(x => ({ id: x.id, name: x.name, w: x.w, sig: x.sig, source: x.source, h: x.h })),
  }));
}
process.stdout.write(JSON.stringify(out, null, 1));
