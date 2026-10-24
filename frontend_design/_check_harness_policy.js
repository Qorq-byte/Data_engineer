// Exercise actual page functions; fixture values are synthetic, not measured policy.
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
const snapshot = vm.runInNewContext(`${extract('harnessPolicySnapshot', 'ragConfigurationSnapshot')}; harnessPolicySnapshot;`);
function policy() {
  const limits = {};
  for (const [key, value] of Object.entries({ read_only: true, max_llm_calls: 10, max_retries: 0,
    max_result_rows: 1000, statement_timeout_ms: 30000, query_timeout_s: 120, session_ttl_s: 3600 })) {
    limits[key] = { value, source: 'synthetic-source' };
  }
  return { status: 'ok', mode: 'read_only', editable_fields: [], legacy_stored_settings_applied: false,
    scope: 'query', candidate_policy: 'request', direct_sql_scope: 'separate', snapshot_scope: 'read time',
    workflow: { plan_id: 'request_query', controller: 'WorkflowRunner', source: 'plan.yml',
      node_order: [{ id: 'parse', node: 'parse_nl' }], max_iterations: 5 }, limits };
}
function harness(read = async () => policy()) {
  const state = { settings: policy(), error: 'previous failure', loading: false, calls: [] };
  const context = { harnessPolicySnapshot: snapshot, harnessReadSeq: { current: 0 },
    useCallback: fn => fn, setHarnessLoading: value => { state.loading = value; },
    setHarnessError: value => { state.error = value; },
    setHarnessSettings: value => { state.settings = value; },
    api: { _fetch: (...args) => { state.calls.push(args); return read(...args); } } };
  const run = vm.runInNewContext(`${extract('fetchHarnessPolicy', 'fetchSettings')}; fetchHarnessPolicy;`, context);
  return { state, run };
}
(async () => {
  let checked = 0;
  assert(snapshot(policy())); checked++;
  const zeroBudget = policy(); zeroBudget.limits.max_llm_calls.value = 0;
  zeroBudget.limits.query_timeout_s.value = 0.25; assert(snapshot(zeroBudget)); checked++;
  for (const mutate of [
    data => { data.status = 'updated'; }, data => { data.mode = 'editable'; },
    data => { data.editable_fields = ['read_only']; },
    data => { data.legacy_stored_settings_applied = true; },
    data => { data.limits.read_only.value = false; },
    data => { data.limits.max_result_rows.value = 0; },
    data => { data.limits.max_retries.value = -1; },
    data => { data.limits.query_timeout_s.value = Infinity; },
    data => { data.limits.max_llm_calls.value = '10'; },
    data => { data.limits.session_ttl_s.value = 1.2; },
    data => { data.limits.statement_timeout_ms.source = ''; },
    data => { delete data.limits.max_retries; },
    data => { data.workflow.controller = 'LLM'; },
    data => { data.workflow.node_order = []; },
    data => { data.workflow.node_order.push(data.workflow.node_order[0]); },
    data => { data.workflow.max_iterations = NaN; },
    data => { delete data.scope; },
  ]) { const data = policy(); mutate(data); assert.equal(snapshot(data), null); checked++; }
  for (const value of [null, {}, { status: 'ok', read_only: false }]) {
    assert.equal(snapshot(value), null); checked++;
  }
  const good = harness(); await good.run();
  assert.equal(good.state.error, null); assert.equal(good.state.loading, false);
  assert.equal(good.state.settings.limits.max_retries.value, 0);
  assert.deepEqual(good.state.calls, [['/api/v1/settings/harness']]); checked++;
  for (const read of [async () => { throw new Error('private backend error'); }, async () => ({ status: 'ok' })]) {
    const failed = harness(read); await failed.run();
    assert.equal(failed.state.settings, null); assert.equal(failed.state.loading, false);
    assert(failed.state.error.includes('未知')); assert(!failed.state.error.includes('private'));
    assert.equal(failed.state.calls.length, 1); checked++;
  }
  let firstDone, secondDone;
  const race = harness(() => new Promise(resolve => {
    if (!firstDone) firstDone = resolve; else secondDone = resolve;
  }));
  const first = race.run(); const second = race.run();
  firstDone(policy()); await first;
  assert.equal(race.state.loading, true); checked++;
  const updated = policy(); updated.limits.max_result_rows.value = 7;
  secondDone(updated); await second;
  assert.equal(race.state.settings.limits.max_result_rows.value, 7);
  assert.equal(race.state.loading, false); checked++;
  let oldDone, newDone;
  const late = harness(() => new Promise(resolve => { if (!oldDone) oldDone = resolve; else newDone = resolve; }));
  const oldRead = late.run(); const newRead = late.run();
  newDone({ status: 'ok' }); await newRead; oldDone(policy()); await oldRead;
  assert.equal(late.state.settings, null); assert(late.state.error); checked++;
  let recovering = false;
  const recovery = harness(async () => recovering ? policy() : null);
  await recovery.run(); recovering = true; await recovery.run();
  assert.equal(recovery.state.error, null); assert(recovery.state.settings); checked++;
  const block = html.slice(html.indexOf('{activeTab === "harness" && ('), html.indexOf('{activeTab === "rag" && ('));
  assert(!block.includes('<input')); assert(!block.includes('handleSave'));
  assert(!html.includes('const handleSaveHarness ='));
  assert(block.includes('role="alert"') && block.includes('aria-busy'));
  console.log(`[PASS] Harness policy: ${checked} actual-function contract scenarios; read-only UI structure`);
})().catch(error => { console.error(error); process.exitCode = 1; });
