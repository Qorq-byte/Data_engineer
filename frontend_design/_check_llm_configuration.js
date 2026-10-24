// Execute actual page functions; never call external model services.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const html = fs.readFileSync('frontend_design/workspace.html', 'utf8');
function extract(name, next) { const a = html.indexOf(`const ${name} =`), b = html.indexOf(`const ${next} =`, a + 1); assert(a >= 0 && b > a); return html.slice(a, b); }
const snapshot = vm.runInNewContext(`${extract('llmConfigurationSnapshot', 'databaseCapabilitiesSnapshot')}; llmConfigurationSnapshot;`);
function data(options = {}) { return { status: 'ok', configuration_source: 'memory', default_model: 'test-model', fallback_model: '', routing_policy: 'primary_then_fallback', temperature_policy: 'caller_supplied', unsupported_fields: ['routing_strategy', 'temperature'], max_tokens: 4096,
  providers: { fixture: { enabled: true, models: ['test-model'], api_key_configured: true, base_url: null } }, runtime: { available: true, active_model: 'test-model', mock_mode: true, max_output_tokens: 4096 }, ...options }; }
function harness(options = {}) {
  const state = { settings: snapshot(data()), error: null, loading: false, busy: false, message: null, keys: { fixture: 'synthetic-key' }, calls: [] };
  const ctx = { useCallback: fn => fn, llmConfigurationSnapshot: snapshot, llmBusyRef: { current: false }, llmReadSeq: { current: 0 }, llmLoading: false, llmError: null, llmSettings: state.settings,
    providerKeys: state.keys, newProvider: { name: 'new', api_key: 'synthetic-new-key', models: 'new-model', base_url: '' }, BUILTIN_PROVIDERS: [], confirm: () => true,
    setLlmSettings: value => { state.settings = value; ctx.llmSettings = value; }, setLlmError: value => { state.error = value; ctx.llmError = value; }, setLlmLoading: value => { state.loading = value; },
    setLlmBusy: value => { state.busy = value; }, setSaveMsg: value => { state.message = value; }, setProviderKeys: value => { state.keys = value; }, setNewProvider: value => { state.newProvider = value; }, setShowNewProvider: () => {}, setAvailableModels: () => {},
    api: { _headers: () => ({}), _fetch: async (path, options) => { state.calls.push({ path, options }); return options ? data({ status: 'updated', persistence: 'persisted', runtime_applied: true, active_model: 'test-model', mock_mode: true }) : data(); }, deleteProvider: async name => { state.calls.push({ name }); return data({ providers: {}, provider: name, status: 'deleted', persistence: 'persisted', runtime_applied: true, active_model: 'test-model', mock_mode: true }); } }, ...options };
  ctx.api._headers ||= () => ({});
  const load = (name, next) => vm.runInNewContext(`${extract(name, next)}; ${name};`, ctx);
  ctx.commitLlmChange = load('commitLlmChange', 'handleSaveLLM');
  return { state, ctx, commit: ctx.commitLlmChange, read: load('fetchLlmConfiguration', 'fetchDatabaseCapabilities'),
    save: load('handleSaveLLM', 'handleProviderToggle'), toggle: load('handleProviderToggle', 'handleAddProvider'), add: load('handleAddProvider', 'handleDeleteProvider'), remove: load('handleDeleteProvider', 'handleSaveRag') };
}
(async () => {
  let n = 0;
  assert(snapshot(data())); n++;
  assert(snapshot(data({ runtime: { available: false, active_model: null, mock_mode: null, max_output_tokens: null } }))); n++;
  const secret = data({ api_key: 'private', api_key_enc: 'cipher' }); secret.providers.fixture.api_key = 'private';
  assert(!JSON.stringify(snapshot(secret)).includes('private')); assert(!JSON.stringify(snapshot(secret)).includes('cipher')); n++;
  for (const value of [null, {}, data({ status: 'error' }), data({ configuration_source: 'unknown' }), data({ max_tokens: NaN }), data({ max_tokens: 0 }), data({ routing_policy: 'unknown' }), data({ providers: { bad: { enabled: true } } }), data({ runtime: { available: true, active_model: null, mock_mode: false } })]) { assert.equal(snapshot(value), null); n++; }
  for (const value of [data({ unsupported_fields: [] }), data({ max_tokens: 32769 }), data({ runtime: { available: true, active_model: 'test-model', mock_mode: true, max_output_tokens: -1 } })]) { assert.equal(snapshot(value), null); n++; }
  const drift = data(); drift.runtime.max_output_tokens = 5; assert.equal(snapshot(drift).configured_max_tokens, 4096); n++;
  const uncapped = data(); uncapped.runtime.max_output_tokens = null; assert(snapshot(uncapped)); n++;
  assert.equal(snapshot(data({ runtime: { available: false, active_model: null, mock_mode: null, max_output_tokens: 3 } })), null); n++;
  const good = harness(); good.state.message = { type: 'ok', text: 'old success' }; await good.read(); assert(good.state.settings); assert.equal(good.state.loading, false); assert.equal(good.state.message, null); n++;
  for (const read of [async () => null, async () => { throw new Error('private'); }]) { const h = harness({ api: { _fetch: read } }); await h.read(); assert.equal(h.state.settings, null); assert(h.state.error.includes('未知')); assert(!h.state.error.includes('private')); n++; }
  const saved = harness(); assert.equal(await saved.commit({ max_tokens: 4096 }), true); assert.equal(saved.state.message.type, 'ok'); assert.equal(Object.keys(saved.state.keys).length, 0); assert.equal(saved.state.newProvider.api_key, ''); n++;
  for (const persistence of ['memory_only', 'persisted']) { const h = harness({ api: { _fetch: async () => data({ status: 'updated', persistence, runtime_applied: true, active_model: 'test-model', mock_mode: true }) } }); await h.commit({}); assert.equal(h.state.message.type, persistence === 'memory_only' ? 'warn' : 'ok'); if (persistence === 'memory_only') assert(h.state.message.text.includes('重启不保留')); n++; }
  for (const response of [null, { status: 'updated' }, data(), data({ status: 'updated', persistence: 'unconfirmed' }), data({ status: 'updated', persistence: 'persisted', runtime_applied: false }), data({ status: 'updated', persistence: 'persisted', runtime_applied: true, active_model: 'wrong', mock_mode: true })]) { const h = harness({ api: { _fetch: async () => response } }); assert.equal(await h.commit({}), false); assert(h.state.error); assert.equal(h.state.settings, null); assert.equal(h.state.busy, false); n++; }
  const mismatch = harness(); assert.equal(await mismatch.commit({ max_tokens: 888 }), false); n++;
  let calls = 0; const failed = harness({ api: { _fetch: async () => { calls++; throw new Error('private'); } } }); await failed.commit({}); await failed.commit({}); assert.equal(calls, 1); assert.equal(failed.state.settings, null); assert(!failed.state.message.text.includes('private')); n++;
  let release; const busy = harness({ api: { _fetch: () => new Promise(resolve => { release = resolve; }) } }); const pending = busy.commit({}); assert.equal(await busy.commit({}), false); release(data({ status: 'updated', persistence: 'persisted', runtime_applied: true, active_model: 'test-model', mock_mode: true })); await pending; assert.equal(busy.state.busy, false); n++;
  let oldDone; const race = harness({ api: { _fetch: (path, options) => options ? Promise.resolve(data({ status: 'updated', persistence: 'persisted', runtime_applied: true, active_model: 'test-model', mock_mode: true })) : new Promise(resolve => { oldDone = resolve; }) } }); const reading = race.read(); await race.commit({}); oldDone(null); await reading; assert(race.state.settings); assert.equal(race.state.loading, false); n++;
  const handlers = harness();
  handlers.ctx.api._fetch = async (path, options) => {
    const payload = JSON.parse(options.body); handlers.state.calls.push(payload);
    const response = data({ status: 'updated', persistence: 'persisted', runtime_applied: true, active_model: 'test-model', mock_mode: true });
    Object.assign(response, payload);
    response.providers = structuredClone(handlers.state.settings.providers);
    for (const [name, update] of Object.entries(payload.providers || {})) response.providers[name] = { enabled: true, models: [], base_url: null, api_key_configured: true, ...response.providers[name], ...update, base_url: update.base_url === '' ? null : update.base_url ?? response.providers[name]?.base_url ?? null };
    return response;
  };
  await handlers.save(); const payload = handlers.state.calls.pop(); assert.equal(payload.providers.fixture.api_key, 'synthetic-key'); assert(!('temperature' in payload) && !('routing_strategy' in payload)); n++;
  await handlers.toggle('fixture'); assert.deepEqual(handlers.state.calls.pop(), { providers: { fixture: { enabled: false } } }); n++;
  await handlers.add(); assert.equal(handlers.state.calls.pop().providers.new.api_key, 'synthetic-new-key'); assert(!JSON.stringify(handlers.state.settings).includes('synthetic-new-key')); n++;
  await handlers.remove('fixture'); assert.equal(handlers.state.calls.pop().name, 'fixture'); n++;
  const deleted = harness(); assert.equal(await deleted.commit(null, 'fixture'), true); assert(!deleted.state.settings.providers.fixture); n++;
  const invalid = harness(); invalid.ctx.llmSettings.max_tokens = 0; await invalid.save(); assert.equal(invalid.state.calls.length, 0); assert.equal(invalid.state.message.type, 'err'); n++;
  const unconfirmedCap = harness({ api: { _fetch: async () => data({ status: 'updated', persistence: 'persisted', runtime_applied: true, active_model: 'test-model', mock_mode: true, runtime: { available: true, active_model: 'test-model', mock_mode: true, max_output_tokens: null } }) } }); assert.equal(await unconfirmedCap.commit({}), false); n++;
  console.log(`[PASS] LLM configuration: ${n} actual-function contract scenarios`);
})().catch(error => { console.error(error); process.exitCode = 1; });
