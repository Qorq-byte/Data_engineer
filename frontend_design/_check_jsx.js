// Extract <script type="text/babel"> blocks from an HTML file and syntax-check them with Babel.
const fs = require('fs');
const Babel = require(process.env.TEMP + '\\babelcheck\\node_modules\\@babel\\standalone\\babel.js');

const file = process.argv[2];
const html = fs.readFileSync(file, 'utf8');

const re = /<script type="text\/babel">([\s\S]*?)<\/script>/g;
let match;
let idx = 0;
let failed = false;

while ((match = re.exec(html)) !== null) {
  idx++;
  const code = match[1];
  try {
    Babel.transform(code, { presets: ['react'] });
    console.log(`[PASS] ${file} - babel block #${idx} (${code.length} chars)`);
  } catch (e) {
    failed = true;
    console.log(`[FAIL] ${file} - babel block #${idx}`);
    console.log(e.message.split('\n').slice(0, 12).join('\n'));
  }
}

if (idx === 0) {
  console.log(`[WARN] no text/babel block found in ${file}`);
}
process.exit(failed ? 1 : 0);
