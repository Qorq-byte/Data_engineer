// Compile the JSX version used by the browser, with no machine-local paths.
const fs = require('node:fs');
const Babel = require('@babel/standalone');
const files = process.argv.slice(2);
if (!files.length) throw new Error('Pass at least one HTML file');
let failed = false;
for (const file of files) {
  const html = fs.readFileSync(file, 'utf8');
  let count = 0;
  for (const [, attrs, code] of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
    if (/\bsrc\s*=/.test(attrs)) continue;
    const isBabel = /type=["']text\/babel["']/.test(attrs);
    if (/\btype=/.test(attrs) && !isBabel && !/type=["'](?:module|text\/javascript)["']/.test(attrs)) continue;
    count++;
    try {
      Babel.transform(code, { presets: isBabel ? ['react'] : [], sourceType: 'unambiguous' });
      console.log(`[PASS] ${file} script #${count}`);
    } catch (error) {
      failed = true;
      console.error(`[FAIL] ${file} script #${count}\n${error.message}`);
    }
  }
  if (!count) { failed = true; console.error(`[FAIL] ${file}: no inline scripts checked`); }
}
process.exitCode = failed ? 1 : 0;
