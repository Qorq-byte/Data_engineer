// Execute actual workspace functions; fixtures represent API contracts, not live model quality.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('frontend_design/workspace.html', 'utf8');
function extract(name, endName) {
  const start = html.indexOf(`const ${name} =`);
  const end = html.indexOf(`const ${endName} =`, start + 1);
  assert(start >= 0 && end > start, `Cannot locate actual ${name}`);
  return html.slice(start, end);
}
const rows = vm.runInNewContext(`${extract('schemaIndexRows', 'SettingsPanel')}; schemaIndexRows;`);
const result = rows({ status: 'degraded', schema_index: {
  refresh_required_databases: ['orphan', 'shared'], recovery_failed_databases: ['shared'],
  startup_schema_results: { historical: false },
}}, [{ db_id: 'live', database_name: 'Live' }, { db_id: 'shared' }]);
assert.deepEqual(Array.from(result, row => [row.dbId, row.state, row.connected]), [
  ['live', 'unrestricted', true], ['orphan', 'refresh_required', false],
  ['shared', 'recovery_failed', true],
]);
console.log('[PASS] RAG maintenance: actual status row construction');

const statusData = () => ({ status: 'degraded', schema_index: {
  status: 'degraded', refresh_required_databases: ['orphan'], recovery_failed_databases: [],
}});
const counts = n => ({ lancedb_count: n, bm25_count: n });
const statsData = n => ({ status: 'ok', schema: counts(n), metrics: counts(0), documents: counts(0) });
const connections = [{ db_id: 'live' }, { db_id: "quoted' name" }];
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
let checked = 1;

function readHarness(api) {
  const state = {};
  const ctx = {
    api, ragReadSeq: { current: 0 }, useCallback: fn => fn, useEffect() {}, activeTab: 'rag',
    setRagLoading: value => { state.loading = value; },
    setRagStats: value => { state.stats = value; },
    setRagStatus: value => { state.status = value; },
    setRagConnections: value => { state.connections = value; },
    setRagLoadError: value => { state.error = value; },
  };
  const invoke = vm.runInNewContext(`${extract('fetchRagStats', 'handleRebuildSchemaIndex')}; fetchRagStats;`, ctx);
  return { state, invoke };
}

function actionHarness(overrides = {}) {
  const state = { calls: [], messages: [], confirmations: [], reads: 0, busy: false };
  const ctx = {
    schemaIndexRows: rows, ragStatus: statusData(), ragConnections: connections,
    ragBusyRef: { current: false }, ragLoading: false, ragLoadError: null,
    window: { confirm: text => { state.confirmations.push(text); return true; } },
    api: {
      _fetch: async (path, options) => {
        state.calls.push([path, options]);
        return { status: 'cleared', scope: 'schema', db_id: 'orphan', removed: counts(1) };
      },
      schemaRefresh: async id => { state.calls.push([id]); return { rag_indexed: true }; },
    },
    setRagMsg: value => state.messages.push(value), setRagAction: value => { state.action = value; },
    setRagBusy: value => { state.busy = value; }, fetchRagStats: async () => { state.reads++; },
    ...overrides,
  };
  const invoke = vm.runInNewContext(`${extract('handleSchemaMaintenance', 'handleUploadDoc')}; handleSchemaMaintenance;`, ctx);
  return { state, ctx, invoke };
}

(async () => {
  assert.equal(rows(null, connections).length, 0);
  assert.equal(rows(statusData(), null).length, 0);
  checked += 2;
  for (const failure of [null, 'stats', 'status', 'connections', 'malformed']) {
    const harness = readHarness({
      _fetch: async path => path.endsWith('/stats')
        ? failure === 'stats' ? { status: 'error' } : statsData(3)
        : failure === 'status' ? { status: 'error' }
          : failure === 'malformed' ? { status: 'ok', schema_index: {} } : statusData(),
      databases: async () => {
        if (failure === 'connections') throw new Error('offline');
        return { databases: connections };
      },
    });
    await harness.invoke();
    assert.equal(harness.state.loading, false);
    assert.equal(Boolean(harness.state.error), failure !== null);
    if (failure === 'stats') assert.equal(harness.state.stats, null, 'Unknown counts cannot become zero');
    if (failure === 'connections') assert.equal(harness.state.connections, null, 'Unknown is not disconnected');
    checked++;
  }
  const old = [deferred(), deferred(), deferred()];
  const calls = [0, 0, 0];
  const latest = readHarness({
    _fetch: path => {
      const index = path.endsWith('/stats') ? 0 : 1;
      return calls[index]++ === 0 ? old[index].promise : Promise.resolve(index ? statusData() : statsData(9));
    },
    databases: () => calls[2]++ === 0 ? old[2].promise : Promise.resolve({ databases: connections }),
  });
  const stale = latest.invoke();
  await Promise.resolve();
  await latest.invoke();
  old[0].resolve(statsData(0)); old[1].resolve({ status: 'error' }); old[2].resolve({ databases: [] });
  await stale;
  assert.equal(latest.state.stats.schema.lancedb_count, 9);
  assert.equal(latest.state.error, null, 'Older responses must not overwrite latest state');
  checked++;

  for (const [field, value] of [['status', 'ok'], ['scope', 'all'], ['db_id', 'other'],
    ['removed', {}], ['removed', { lancedb_count: -1, bm25_count: 0 }], ['removed', counts(0)]]) {
    const h = actionHarness();
    h.ctx.api._fetch = async (...args) => {
      h.state.calls.push(args);
      return { status: 'cleared', scope: 'schema', db_id: 'orphan', removed: counts(1), [field]: value };
    };
    await h.invoke('orphan', 'clear');
    assert.equal(h.state.messages.at(-1).type, field === 'removed' && value.lancedb_count === 0 ? 'ok' : 'err');
    assert.equal(h.state.calls.length, 1);
    assert.equal(h.state.reads, 1);
    assert.equal(h.state.busy, false);
    checked++;
  }
  const encoded = actionHarness();
  encoded.ctx.api._fetch = async (...args) => {
    encoded.state.calls.push(args);
    return { status: 'cleared', scope: 'schema', db_id: "quoted' name", removed: counts(1) };
  };
  await encoded.invoke("quoted' name", 'clear');
  assert.equal(encoded.state.calls[0][0], `/api/v1/learning/rag/schema/${encodeURIComponent("quoted' name")}/clear`);
  assert.equal(encoded.state.calls[0][1].method, 'POST');
  assert(encoded.state.confirmations[0].includes("quoted' name"));
  assert(encoded.state.confirmations[0].includes('其他库、源数据、指标和上传文档均保留'));
  checked++;

  for (const guard of ['cancel', 'busy', 'loading', 'read_error', 'disconnected', 'missing']) {
    const h = actionHarness();
    if (guard === 'cancel') h.ctx.window.confirm = () => false;
    if (guard === 'busy') h.ctx.ragBusyRef.current = true;
    if (guard === 'loading') h.ctx.ragLoading = true;
    if (guard === 'read_error') h.ctx.ragLoadError = 'unavailable';
    await h.invoke(guard === 'missing' ? 'unknown' : 'orphan', guard === 'disconnected' ? 'rebuild' : 'clear');
    assert.equal(h.state.calls.length, 0);
    assert.equal(h.state.reads, 0);
    checked++;
  }
  for (const [error, text] of [
    ...[true, false, null].map(restored => [{ code: 'SCHEMA_INDEX_CLEAR_FAILED', body: { detail: { restored } } },
      restored === true ? '旧索引已恢复' : restored === false ? '检索仍受限' : '尚无法确认']),
    [{ status: 403 }, '维护权限'], [{ code: 'SCHEMA_REFRESH_CONFLICT' }, '已变化'],
    [{ code: 'API_TIMEOUT' }, '结果未确认'],
  ]) {
    const h = actionHarness();
    h.ctx.api._fetch = async (...args) => { h.state.calls.push(args); throw error; };
    await h.invoke('orphan', 'clear');
    assert.equal(h.state.messages.at(-1).type, 'err');
    assert(h.state.messages.at(-1).text.includes(text));
    assert.equal(h.state.calls.length, 1, 'Failed POSTs must not be retried automatically');
    assert.equal(h.ctx.ragBusyRef.current, false);
    assert.equal(h.state.action, null);
    checked++;
  }
  for (const success of [true, false, undefined]) {
    const h = actionHarness();
    h.ctx.api.schemaRefresh = async id => { h.state.calls.push([id]); return { rag_indexed: success }; };
    await h.invoke('live', 'rebuild');
    assert.equal(h.state.messages.at(-1).type, success === true ? 'ok' : 'err');
    checked++;
  }
  const waiting = deferred(), h = actionHarness();
  h.ctx.api._fetch = async (...args) => { h.state.calls.push(args); return waiting.promise; };
  const first = h.invoke('orphan', 'clear');
  await h.invoke('orphan', 'clear');
  assert.equal(h.state.calls.length, 1, 'Immediate double invocation must be blocked');
  waiting.resolve({ status: 'cleared', scope: 'schema', db_id: 'orphan', removed: counts(1) });
  await first;
  assert.equal(h.state.busy, false);
  checked++;

  // Bulk clear still detects sparse-only records; unknown counts cannot initiate deletion.
  for (const available of [true, false]) {
    const state = { posts: [], busy: false };
    const ctx = {
      ragBusyRef: { current: false }, ragLoading: false, ragLoadError: null, uploadingDoc: false,
      ragStats: available ? { ...statsData(0), schema: { lancedb_count: 0, bm25_count: 1 } } : null,
      window: { confirm: () => true }, setRagMsg() {}, setClearing() {},
      setRagBusy: value => { state.busy = value; }, fetchRagStats() {},
      api: { _headers() {}, _fetch: async (...args) => { state.posts.push(args); return { status: 'ok' }; } },
    };
    const invoke = vm.runInNewContext(`${extract('handleClearRAG', 'handleSchemaMaintenance')}; handleClearRAG;`, ctx);
    await invoke();
    assert.equal(state.posts.length, available ? 1 : 0);
    if (available) assert.equal(JSON.parse(state.posts[0][1].body).scope, 'all');
    assert.equal(state.busy, false);
    checked++;
  }
  for (const result of [{ rag_index_cleared: true }, { rag_index_cleared: false }, {}]) {
    const notifications = [], messages = [];
    const ctx = { api: { disconnectDb: async () => result }, setExpandedDb() {}, setDbPreviewData() {},
      refreshList() {}, onDbChange() {}, setMsg: value => messages.push(value),
      notify: (...args) => notifications.push(args),
    };
    const invoke = vm.runInNewContext(`${extract('handleDisconnect', 'handleToggleDb')}; handleDisconnect;`, ctx);
    await invoke('live');
    assert.equal(notifications.at(-1)[1], result.rag_index_cleared === true ? 'ok' : 'error');
    if (result.rag_index_cleared !== true) assert(messages.at(-1).text.includes('Schema 按库维护'));
    checked++;
  }
  console.log(`[PASS] RAG maintenance: ${checked} actual-function contract scenarios`);
})().catch(error => { console.error(error); process.exitCode = 1; });
