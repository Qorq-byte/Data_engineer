// One-off: extract module-type babel block and syntax-check with Babel.
const fs = require('fs');
const Babel = require(process.env.TEMP + '\\babelcheck\\node_modules\\@babel\\standalone\\babel.js');
const file = process.argv[2];
const html = fs.readFileSync(file, 'utf8');
const re = /<script type="text\/babel"[^>]*>([\s\S]*?)<\/script>/g;
let match, idx = 0, failed = false;
while ((match = re.exec(html)) !== null) {
  idx++;
  try {
    Babel.transform(match[1], { presets: ['react'], sourceType: 'module' });
    console.log(`[PASS] ${file} - babel block #${idx} (${match[1].length} chars)`);
  } catch (e) {
    failed = true;
    console.log(`[FAIL] block #${idx}\n` + e.message.split('\n').slice(0, 12).join('\n'));
  }
}
if (idx === 0) console.log('[WARN] no babel block found');
process.exit(failed ? 1 : 0);
