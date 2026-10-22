// Exercise the actual page handlers against HTTP-success/business-failure contracts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('frontend_design/workspace.html', 'utf8');

(async () => {
  let checked = 0;
  for (const [handler, next, flag] of [
    ['handleRebuildSchemaIndex', 'handleRefreshRAG', 'rag_indexed'],
    ['handleRefreshRAG', 'handleClearRAG', 'refreshed'],
  ]) {
    const start = html.indexOf(`  const ${handler} =`);
    const end = html.indexOf(`  const ${next} =`, start);
    assert(start >= 0 && end > start, `Cannot locate ${handler}`);
    const source = html.slice(start, end);
    for (const [values, successes, type] of [
      [[true, true], 2, 'ok'], [[true, false], 1, 'err'],
      [[false], 0, 'err'], [[undefined], 0, 'err'],
      [[new Error('SCHEMA_REFRESH_CONFLICT')], 0, 'err'], [[], 0, 'err'],
    ]) {
      const messages = [], rebuilding = [];
      let calls = 0;
      const refresh = async () => {
        const value = values[calls++];
        if (value instanceof Error) throw value;
        return { [flag]: value, table_count: 1 };
      };
      const invoke = vm.runInNewContext(`${source}; ${handler};`, {
        api: {
          databases: async () => ({ databases: values.map((_, i) => ({
            db_id: `fixture-${i}`, database_name: `fixture-${i}`,
          })) }),
          schemaRefresh: refresh, _fetch: refresh,
        },
        setRebuilding: value => rebuilding.push(value),
        ragBusyRef: { current: false }, ragLoading: false, ragLoadError: null,
        setRagBusy() {},
        setRagMsg: value => messages.push(value), fetchRagStats() {},
      });
      await invoke();
      assert.equal(calls, values.length, 'Handlers must not retry implicitly');
      const terminal = messages.at(-1);
      assert.equal(terminal.type, type, `${handler}: ${terminal.text}`);
      if (values.length && type === 'err') {
        assert(terminal.text.includes(`${successes} 成功`), terminal.text);
      }
      if (handler === 'handleRebuildSchemaIndex') assert.equal(rebuilding.at(-1), false);
      checked++;
    }
  }
  console.log(`[PASS] Schema/RAG refresh handlers: ${checked} contract scenarios`);
})().catch(error => { console.error(error); process.exitCode = 1; });
