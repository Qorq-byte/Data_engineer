const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const os = require('node:os');
const { buildFrontend } = require('./build-frontend.cjs');

const root = path.resolve(__dirname, '..');
const output = path.join(root, 'frontend_dist');
const hash = data => crypto.createHash('sha256').update(data).digest('hex');

async function check() {
  const first = await buildFrontend();
  for (const [filename, info] of Object.entries(first)) {
    const data = fs.readFileSync(path.join(output, filename));
    assert.equal(hash(data), info.sha256);
    assert.equal(data.length, info.bytes);
    if (filename.endsWith('.html')) {
      const html = data.toString();
      assert(!/text\/babel|react\.development|cdn\.tailwindcss|unpkg\.com|fonts\.googleapis|images\.unsplash/.test(html));
      assert.equal([...html.matchAll(/<script\b/g)].length, 1);
      assert.match(html, /<script defer src="\/assets\//);
      for (const match of html.matchAll(/(?:src|href)="(\/assets\/[^"?#]+)"/g)) {
        assert(first[match[1].slice(1)], `${filename}: missing ${match[1]}`);
      }
      for (const match of html.matchAll(/<(?:script|link|img)\b[^>]*(?:src|href)="([^"]+)"/g)) {
        assert(!/^(?:https?:)?\/\//.test(match[1]), `External page resource: ${match[1]}`);
      }
    }
    if (filename.endsWith('.css')) {
      for (const match of data.toString().matchAll(/url\((?:["']?)([^)"']+)/g)) {
        assert(!/^(?:https?:)?\/\//.test(match[1]), `External CSS resource: ${match[1]}`);
        const target = path.posix.normalize(path.posix.join(path.posix.dirname(filename), match[1]));
        assert(first[target], `Missing CSS resource: ${target}`);
      }
    }
  }
  const workspaceJs = Object.keys(first).find(file => /^assets\/workspace-.*\.js$/.test(file));
  assert(fs.readFileSync(path.join(output, workspaceJs), 'utf8').includes('/assets/default-avatar.svg'));
  assert.deepEqual(await buildFrontend(), first, 'Consecutive builds must be byte-identical');

  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'nl2sql-build-check-'));
  try {
    for (const page of ['landing_page', 'form', 'workspace']) {
      let source = fs.readFileSync(path.join(root, 'frontend_design', `${page}.html`), 'utf8');
      if (page === 'workspace') source = source.replace('<script type="text/babel">', '<script type="text/babel">\nconst = ;');
      fs.writeFileSync(path.join(fixture, `${page}.html`), source);
    }
    await assert.rejects(buildFrontend({ sourceDirectory: fixture }), /Expected identifier/);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(output, 'manifest.json'), 'utf8')), first);
    for (const [filename, info] of Object.entries(first)) {
      assert.equal(hash(fs.readFileSync(path.join(output, filename))), info.sha256);
    }
  } finally {
    if (path.dirname(fixture) !== os.tmpdir() || !path.basename(fixture).startsWith('nl2sql-build-check-')) throw new Error('Unsafe fixture directory');
    fs.rmSync(fixture, { recursive: true, force: true });
  }
  console.log('[PASS] Three local pages, assets/fonts/licenses, SHA-256 integrity, repeatable build and failed-build preservation');
}
check().catch(error => { console.error(error); process.exitCode = 1; });
