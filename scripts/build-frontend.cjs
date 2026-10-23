// Keep the reviewed HTML/JSX as input; browsers receive only compiled local assets.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const esbuild = require('esbuild');
const postcss = require('postcss');
const tailwind = require('tailwindcss');

const ROOT = path.resolve(__dirname, '..');
const OUTPUT = path.join(ROOT, 'frontend_dist');
const PAGES = ['landing_page', 'form', 'workspace'];
const digest = data => crypto.createHash('sha256').update(data).digest('hex');

async function buildFrontend({ sourceDirectory = path.join(ROOT, 'frontend_design') } = {}) {
  // Complete all compilation in memory before replacing an existing build.
  const files = new Map();
  const sources = PAGES.map(name => fs.readFileSync(path.join(sourceDirectory, `${name}.html`), 'utf8'));
  const css = (await postcss([tailwind({
    content: sources.map(raw => ({ raw, extension: 'html' })),
  })]).process('@tailwind base;\n@tailwind components;\n@tailwind utilities;', { from: undefined })).css;
  const cssName = `assets/utilities-${digest(css).slice(0, 16)}.css`;
  files.set(cssName, Buffer.from(css));
  files.set('assets/default-avatar.svg', fs.readFileSync(path.join(ROOT, 'frontend_design/default-avatar.svg')));

  for (const [index, name] of PAGES.entries()) {
    const code = [];
    let html = sources[index].replace(/<script\b([^>]*)>([\s\S]*?)<\/script>/g, (_, attrs, body) => {
      if (!/\bsrc\s*=/.test(attrs)) {
        if (/\btype=/.test(attrs) && !/type=["'](?:text\/babel|text\/javascript)["']/.test(attrs)) {
          throw new Error(`${name}: unsupported script type`);
        }
        code.push(body);
      }
      return '';
    }).replace(/<link\b[^>]*>/g, tag => /(?:href=["']https?:\/\/)/.test(tag) ? '' : tag);

    let imports = '';
    if (name === 'landing_page') {
      imports += "import * as ThreeRuntime from 'three'; window.THREE = ThreeRuntime;\n";
    } else {
      imports += "import React from 'react'; import ReactDOM from 'react-dom/client';\n";
      if (name === 'workspace') {
        imports += "import * as MotionRuntime from 'framer-motion'; window.FramerMotion = MotionRuntime;\n";
      }
    }
    const weights = name === 'form' ? [400, 500, 600, 700, 800, 900] : [300, 400, 500, 600, 700, 800, 900];
    const font = name === 'form' ? 'archivo' : 'barlow';
    for (const weight of weights) imports += `import '@fontsource/${font}/latin-${weight}.css';\n`;
    if (name !== 'form') {
      for (const weight of [400, 500]) imports += `import '@fontsource/jetbrains-mono/latin-${weight}.css';\n`;
    }
    const result = await esbuild.build({
      stdin: { contents: imports + code.join('\n;\n'), loader: 'jsx', resolveDir: ROOT, sourcefile: `${name}.jsx` },
      bundle: true, write: false, minify: true, format: 'iife', target: 'es2020',
      define: { 'process.env.NODE_ENV': '"production"' },
      outdir: path.join(OUTPUT, 'assets'), entryNames: `${name}-[hash]`,
      assetNames: '[name]-[hash]', loader: { '.woff': 'file', '.woff2': 'file' },
      legalComments: 'linked', metafile: true,
    });
    const links = [`<link rel="stylesheet" href="/${cssName}"/>`];
    let script;
    for (const output of result.outputFiles) {
      const relative = path.relative(OUTPUT, output.path).split(path.sep).join('/');
      if (relative.startsWith('../')) throw new Error('Build output escaped frontend_dist');
      files.set(relative, Buffer.from(output.contents));
      if (relative.endsWith('.css')) links.push(`<link rel="stylesheet" href="/${relative}"/>`);
      if (relative.endsWith('.js')) script = relative;
    }
    if (!script || !code.length) throw new Error(`${name}: missing page entry`);
    // Tailwind preflight precedes the existing page-specific rules.
    html = html.replace('<style>', links.join('\n') + '\n<style>')
      .replace('</body>', `<script defer src="/${script}"></script>\n</body>`);
    files.set(`${name}.html`, Buffer.from(html));
  }

  const packages = ['react', 'react-dom', 'framer-motion', 'three',
    '@fontsource/barlow', '@fontsource/archivo', '@fontsource/jetbrains-mono', 'scheduler', 'tslib'];
  let licenses = '';
  for (const pkg of packages) {
    const directory = path.join(ROOT, 'node_modules', pkg);
    const metadata = JSON.parse(fs.readFileSync(path.join(directory, 'package.json'), 'utf8'));
    const license = fs.readdirSync(directory).find(file => /^licen[sc]e(?:\.|$)/i.test(file));
    if (!license) throw new Error(`Missing license for ${pkg}`);
    licenses += `\n===== ${pkg}@${metadata.version} (${metadata.license}) =====\n` + fs.readFileSync(path.join(directory, license), 'utf8');
  }
  files.set('assets/THIRD-PARTY-LICENSES.txt', Buffer.from(licenses));
  const manifest = Object.fromEntries([...files.entries()].sort(([a], [b]) => a.localeCompare(b, 'en'))
    .map(([file, data]) => [file, { sha256: digest(data), bytes: data.length }]));
  files.set('manifest.json', Buffer.from(JSON.stringify(manifest, null, 2) + '\n'));
  if (path.dirname(OUTPUT) !== ROOT || path.basename(OUTPUT) !== 'frontend_dist') throw new Error('Unsafe output directory');
  if (fs.existsSync(OUTPUT) && fs.lstatSync(OUTPUT).isSymbolicLink()) throw new Error('Refusing symlink output directory');
  fs.rmSync(OUTPUT, { recursive: true, force: true });
  for (const [file, data] of files) {
    const dest = path.join(OUTPUT, file);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    if (file === 'manifest.json') {
      fs.writeFileSync(dest + '.tmp', data);
      fs.renameSync(dest + '.tmp', dest);
    } else {
      fs.writeFileSync(dest, data);
    }
  }
  console.log(`Built ${PAGES.length} pages, ${files.size} files, ${[...files.values()].reduce((sum, data) => sum + data.length, 0)} bytes`);
  return manifest;
}

if (require.main === module) buildFrontend().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { buildFrontend };
