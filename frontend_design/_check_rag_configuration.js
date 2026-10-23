// Run the actual page handlers: no copied implementation and no live provider claims.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('frontend_design/workspace.html', 'utf8');
function extract(name, next) {
  const start = html.indexOf(`const ${name} =`);
  const end = html.indexOf(`const ${next} =`, start + 1);
  assert(start >= 0 && end > start, `Missing actual ${name}`);
  return html.slice(start, end);
}
const snapshot = vm.runInNewContext(`${extract('ragConfigurationSnapshot', 'SettingsPanel')}; ragConfigurationSnapshot;`);
const weights = alpha => ({ bm25_weight: Number((1 - alpha).toFixed(12)), vector_weight: alpha });
function data(alpha = 0.7, options = {}) {
  return { status: 'ok', ...weights(alpha), configuration_source: 'memory', configuration_valid: true,
    runtime: { available: true, matches_configured: true,
      weights: { schema: weights(alpha), metrics: weights(alpha), documents: weights(alpha) },
      embedding: { provider: 'Synthetic', model: 'synthetic-model', dimension: 16 } }, ...options };
}
function harness(options = {}) {
  const state = { settings: snapshot(data()), loading: false, error: null, message: null, saving: {}, calls: [] };
  const ctx = {
    ragConfigurationSnapshot: snapshot, useCallback: fn => fn,
    ragConfigReadSeq: { current: 0 }, ragConfigBusyRef: { current: false },
    ragSettings: state.settings, ragConfigLoading: false, ragConfigError: null,
    setRagSettings: value => { state.settings = value; },
    setRagConfigLoading: value => { state.loading = value; },
    setRagConfigError: value => { state.error = value; },
    setRagConfigMsg: value => { state.message = value; },
    setSaving: fn => { state.saving = fn(state.saving); },
    api: { ragSettings: async () => data(), updateRagSettings: async payload => {
      state.calls.push(payload);
      return data(payload.vector_weight, { status: 'updated', persistence: 'memory_only', runtime_applied: true });
    } }, ...options,
  };
  return { state, ctx,
    read: vm.runInNewContext(`${extract('fetchRagConfig', 'fetchSettings')}; fetchRagConfig;`, ctx),
    save: vm.runInNewContext(`${extract('handleSaveRag', 'fetchRagStats')}; handleSaveRag;`, ctx) };
}

(async () => {
  let checked = 0;
  assert(snapshot(data())); checked++;
  assert(snapshot(data(0.4, { runtime: { available: false, weights: null, embedding: null, matches_configured: false } }))); checked++;
  for (const value of [null, {}, data(2), data(NaN), data(0.7, { status: 'error' }),
    data(0.7, { bm25_weight: 0.6 }), data(0.7, { configuration_source: 'unknown' }),
    data(0.7, { runtime: { ...data().runtime, matches_configured: false } }),
    data(0.7, { runtime: { ...data().runtime, embedding: { provider: 'Synthetic', model: 'x', dimension: 0 } } }),
    data(0.7, { runtime: { available: false, weights: {}, embedding: null, matches_configured: false } })]) {
    assert.equal(snapshot(value), null); checked++;
  }
  const good = harness(); await good.read();
  assert.equal(good.state.settings.runtime.embedding.model, 'synthetic-model');
  assert.equal(good.state.loading, false); checked++;
  good.state.message = { type: 'warn', text: 'old unready feedback' }; await good.read();
  assert.equal(good.state.message, null); checked++;
  for (const read of [async () => { throw new Error('read denied'); }, async () => ({ status: 'ok' })]) {
    const h = harness({ api: { ragSettings: read } }); await h.read();
    assert.equal(h.state.settings, null); assert(h.state.error); assert.equal(h.state.loading, false); checked++;
  }
  let resolve;
  const delayed = harness({ api: { ragSettings: () => new Promise(done => { resolve = done; }) } });
  const pending = delayed.read(); delayed.ctx.ragConfigReadSeq.current++;
  delayed.state.settings = snapshot(data(0.2)); resolve(data(0.7)); await pending;
  assert.equal(delayed.state.settings.vector_weight, 0.2); checked++;
  const saved = harness(); await saved.save();
  assert.deepEqual(JSON.parse(JSON.stringify(saved.state.calls)), [weights(0.7)]);
  assert.equal(saved.state.message.type, 'warn');
  assert(saved.state.message.text.includes('重启不保留')); assert.equal(saved.state.saving.rag, false); checked++;
  for (const persistence of ['persisted', 'memory_only']) {
    const h = harness({ api: { updateRagSettings: async () => data(0.7, {
      status: 'updated', persistence, runtime_applied: false,
      runtime: { available: false, weights: null, embedding: null, matches_configured: false },
    }) } });
    await h.save(); assert.equal(h.state.message.type, 'warn'); assert(h.state.message.text.includes('未就绪')); checked++;
  }
  const durable = harness({ api: { updateRagSettings: async () => data(0.7, {
    status: 'updated', persistence: 'persisted', runtime_applied: true,
  }) } }); await durable.save(); assert.equal(durable.state.message.type, 'ok'); checked++;
  for (const response of [null, { status: 'updated' }, data(0.7),
    data(0.2, { status: 'updated', persistence: 'persisted', runtime_applied: true }),
    data(0.7, { status: 'updated', persistence: 'unconfirmed', runtime_applied: true }),
    data(0.7, { status: 'updated', persistence: 'persisted', runtime_applied: false })]) {
    const h = harness({ api: { updateRagSettings: async () => response } }); await h.save();
    assert(h.state.error); assert.equal(h.state.settings, null);
    assert.equal(h.ctx.ragConfigBusyRef.current, false); assert.equal(h.state.saving.rag, false); checked++;
  }
  let calls = 0;
  const rejected = harness({ api: { updateRagSettings: async () => { calls++; throw new Error('unconfirmed'); } } });
  await rejected.save(); assert.equal(calls, 1); assert.equal(rejected.state.settings, null); checked++;
  for (const overrides of [{ ragSettings: null }, { ragConfigLoading: true }, { ragConfigError: 'read failed' },
    { ragConfigBusyRef: { current: true } }]) {
    const h = harness(overrides); await h.save(); assert.equal(h.state.calls.length, 0); checked++;
  }
  let finish;
  calls = 0;
  const twice = harness({ api: { updateRagSettings: () => { calls++; return new Promise(done => { finish = done; }); } } });
  const first = twice.save(); await twice.save(); assert.equal(calls, 1);
  finish(data(0.7, { status: 'updated', persistence: 'memory_only', runtime_applied: true })); await first;
  assert.equal(twice.ctx.ragConfigBusyRef.current, false); checked++;
  console.log(`[PASS] RAG configuration: ${checked} actual-function contract scenarios`);
})().catch(error => { console.error(error); process.exitCode = 1; });
