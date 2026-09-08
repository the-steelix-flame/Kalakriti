// Dumps the authored catalogues to JSON. The blocks are plain JS object literals
// (string values, `+` continuations, // comments), so evaluating them is exact -
// a regex kept mis-joining the multi-line entries.
import { readFileSync, writeFileSync } from 'fs';

const src = readFileSync('src/i18n/catalog.ts', 'utf8');

function block(startMarker, closeMarker) {
  const s = src.indexOf(startMarker);
  if (s < 0) throw new Error('missing ' + startMarker);
  const open = src.indexOf('{', s);
  const close = src.indexOf(closeMarker, open);
  if (close < 0) throw new Error('missing close for ' + startMarker);
  return src.slice(open, close + 1);   // closeMarker starts with '}'
}

const en = eval('(' + block('export const EN = {', '} as const;') + ')');
const hi = eval('(' + block('export const HI: Record<StringKey, string> = {', '};') + ')');

writeFileSync('src/i18n/locales/en.json', JSON.stringify(en, null, 2), 'utf8');
writeFileSync('src/i18n/locales/hi.json', JSON.stringify(hi, null, 2), 'utf8');

const missing = Object.keys(en).filter((k) => !(k in hi));
const extra = Object.keys(hi).filter((k) => !(k in en));
console.log('en keys:', Object.keys(en).length, '| hi keys:', Object.keys(hi).length);
if (missing.length) console.log('MISSING IN HI:', missing.join(', '));
if (extra.length) console.log('EXTRA IN HI:', extra.join(', '));
