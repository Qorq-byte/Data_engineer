// Actual page functions, synthetic fixtures; no database health claims.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('frontend_design/workspace.html', 'utf8');
function extract(name, next) {
  const start = html.indexOf(`const ${name} =`), end = html.indexOf(`const ${next} =`, start + 1);
  assert(start >= 0 && end > start); return html.slice(start, end);
}
const snapshot = vm.runInNewContext(`${extract('databaseCapabilitiesSnapshot', 'harnessPolicySnapshot')}; databaseCapabilitiesSnapshot;`);
function data() {
  return { status: 'ok', mode: 'read_only', editable_fields: [], legacy_stored_settings_applied: false,
    connection_pool_size: null, connection_timeout_s: null, scope: 'current process', configuration_source: 'implementation',
    connectors: ['sqlite','duckdb','postgresql','mysql','snowflake','starrocks','bigquery','redshift','clickhouse','databricks','trino'].map((type, index) => ({
      type, implemented: index < 4, driver_module: index < 4 ? 'synthetic' : null,
      module_discoverable: index < 4 ? true : null, management: index < 4 ? 'native_single_connection' : 'not_implemented', registered_connections: 0 })) };
}
function harness(read = async () => data()) {
  const state = { settings: data(), loading: false, error: 'old error', calls: [] };
  const ctx = { useCallback: fn => fn, databaseCapabilitiesSnapshot: snapshot, databaseReadSeq: { current: 0 },
    setDatabaseSettings: value => { state.settings = value; }, setDatabaseLoading: value => { state.loading = value; },
    setDatabaseError: value => { state.error = value; }, api: { _fetch: (...args) => { state.calls.push(args); return read(); } } };
  return { state, run: vm.runInNewContext(`${extract('fetchDatabaseCapabilities', 'fetchHarnessPolicy')}; fetchDatabaseCapabilities;`, ctx) };
}
(async () => {
  let checked = 0;
  assert(snapshot(data())); checked++;
  for (const discoverable of [false, null]) { const value = data(); value.connectors[0].module_discoverable = discoverable; assert(snapshot(value)); checked++; }
  for (const mutate of [
    value => { value.mode = 'editable'; }, value => { value.status = 'error'; },
    value => { value.connection_pool_size = 5; }, value => { value.connection_timeout_s = 10; },
    value => { value.editable_fields = ['pool']; }, value => { value.legacy_stored_settings_applied = true; },
    value => { value.connectors.pop(); }, value => { value.connectors[1].type = 'sqlite'; },
    value => { value.connectors[0].registered_connections = -1; }, value => { value.connectors[0].registered_connections = 0.2; },
    value => { value.connectors[0].implemented = 'true'; }, value => { value.connectors[0].module_discoverable = 'yes'; },
    value => { value.connectors[0].driver_module = ''; }, value => { value.connectors[0].management = 'unknown'; },
    value => { value.connectors[5].driver_module = 'fake'; }, value => { value.scope = ''; },
  ]) { const value = data(); mutate(value); assert.equal(snapshot(value), null); checked++; }
  for (const value of [null, {}, { status: 'ok', dialects_enabled: { sqlite: true } }]) { assert.equal(snapshot(value), null); checked++; }
  const good = harness(); await good.run(); assert.equal(good.state.error, null); assert.equal(good.state.loading, false);
  assert.deepEqual(good.state.calls, [['/api/v1/settings/database']]); checked++;
  for (const read of [async () => null, async () => { throw new Error('private backend detail'); }]) {
    const h = harness(read); await h.run(); assert.equal(h.state.settings, null); assert(h.state.error.includes('未知'));
    assert(!h.state.error.includes('private')); assert.equal(h.state.loading, false); assert.equal(h.state.calls.length, 1); checked++;
  }
  let oldDone, newDone;
  const race = harness(() => new Promise(resolve => { if (!oldDone) oldDone = resolve; else newDone = resolve; }));
  const oldRead = race.run(), newRead = race.run(); oldDone(data()); await oldRead;
  assert.equal(race.state.loading, true); checked++;
  newDone(null); await newRead; assert.equal(race.state.settings, null); assert(race.state.error); assert.equal(race.state.loading, false); checked++;
  let firstDone, latestDone;
  const late = harness(() => new Promise(resolve => { if (!firstDone) firstDone = resolve; else latestDone = resolve; }));
  const first = late.run(), latest = late.run(); latestDone(data()); await latest; firstDone(null); await first;
  assert(late.state.settings); assert.equal(late.state.error, null); checked++;
  let recovered = false; const recovery = harness(async () => recovered ? data() : null);
  await recovery.run(); recovered = true; await recovery.run(); assert(recovery.state.settings); assert.equal(recovery.state.error, null); checked++;
  const block = html.slice(html.indexOf('{activeTab === "database" && ('), html.indexOf('{activeTab === "account" && ('));
  assert(!block.includes('<input')); assert(!html.includes('const dbs = databaseSettings?.dialects_enabled'));
  assert(block.includes('role="alert"')); assert(!block.includes('|| 5') && !block.includes('|| 10'));
  console.log(`[PASS] Database capabilities: ${checked} actual-function contract scenarios; no fallback controls`);
})().catch(error => { console.error(error); process.exitCode = 1; });
