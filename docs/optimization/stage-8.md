# 阶段8：SQL生成/执行与预算适配契约

日期2026-10-21，基准40b809e，依[先行计划](stage-8-plan.md)实施。阶段7补记Actions39532373310成功后开始；全部修改在独立工作树codex/mvp-hardening，原主工作区改动受保护。

## 实施与依据

- `app/llm/protocols.py`定义SQL生成实际消费的CompletionRouter：config、异步complete与字符串异步流。`core/sql_generator.py`、`nodes/generate_sql.py`接受该结构协议，原生Router和预算包装都符合，不要求适配器继承具体类。
- `llm/budget.py`移除动态属性兜底并显式转发上述接口/选项。原生Router仍逐物理尝试计数，注入实现仍由包装器计数；流开始消费才预留一次预算，耗尽先拒绝再调用。新增实际拒绝非对象响应/非字符串片段，异常与取消不吞掉。协议保留原有provider动态关键字与JSON响应值，不冒充LLM响应完整语义验证。
- `db/execution_types.py`用TypedDict表达columns/rows/row_count/truncated，线格式仍是原字典。原生分发入口已有Any边界保留；SQLite/asyncpg/aiomysql辅助函数使用真实驱动类型，同步DuckDB/DB-API使用最小方法协议。SQL单元格依Schema和驱动动态变化仍是Any；这不是所有数据库方言或驱动的完整静态证明。
- `nodes/execute_sql.py`辅助接口和结果明确，AST只读、验证闸门、lease、max_rows、超时及清理逻辑未变。EXPLAIN解析及`nodes/explain_plan.py`的消费参数改用只读Mapping[str, Any]，兼容执行TypedDict和API附加status的JSON字典，不新增调用关系。
- `nodes/agentic.py`延迟资源成员/初始化/访问器使用实际可空Harness类型，不靠Any掩盖返回值；权限规则为list[dict[str,str]]。配置工具的非字符串名称明确ValueError，初始化不缓存无效registry。其他动态工具/服务输入保持既有边界。
- `workflow/query_service.py`目标列表明确SchemaSnapshot可空，保留无连接时仅生成、给出警告的路径；消除预算Router注入类型冲突。主计划和Runner完全未改。
- 两个SQL提取函数使用正则search/group(1)取得首个围栏文本，保持原先findall首项语义，避免Any返回/cast。
- CI扩展标准strict命令，README/AGENTS/开发指南同步。未放宽strict、默认follow_imports或规则，未新增ignore/忽略错误配置。

## 实测验证

Windows/Python3.13.9、Node24，Ruff0.17.1、mypy2.4.0目标Python3.12；沿用阶段7隔离类型依赖types-PyYAML6.0.12.20260906/asyncpg-stubs0.32和本地aiomysql0.3.2声明。运行测试使用原site-packages（asyncpg0.31.0、LiteLLM1.102.0），类型检查使用对应0.32开发驱动，未混称同一运行环境。测试LLM/embedding为mock，本地SQLite/DuckDB/LanceDB/FTS5是真实实现。

- `mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py`：**0错误，27源文件**，默认追踪导入；扩展前四目标基线24项，预算模块此前无额外诊断。[基线](results/stage-8-target-before.jsonl)/[最终](results/stage-8-core.txt)。
- `mypy app/ --output json`：**230项/49文件 → 201项/43文件，仍失败退出1**。同版本同声明环境，以文件/代码/消息多重集比较新增诊断0；初始来自当前基准提交的阶段7最终结果。[前](results/stage-8-before.jsonl)/[后](results/stage-8-after.jsonl)/[分类摘要](results/stage-8-summary.json)。未将全仓失败改成CI通过声明。
- `python -m pytest tests/test_llm tests/test_nodes tests/test_core/test_sql_generator.py tests/test_api/test_query_contract.py -q`：**645 passed，3 warnings，12.86秒**，[日志](results/stage-8-target.txt)。首次一项新增测试给mock流空输入却期待非空，改为明确SELECT输入和完整文本断言后复测通过，无关闭或弱化检查。
- `python -m pytest -q`：**2982 passed，1 skipped，4 warnings，50.26秒**，[日志](results/stage-8-pytest.txt)。新增18用例：适配器转发/预算耗尽/惰性流、4种非法非流响应、3种非法流片段、失败与取消传播、原生非流/流不重复计数、并发ContextVar隔离、2种无效工具名称。Windows继续因无uvloop跳过1项，交由Linux CI补足。
- `ruff check .`：全仓通过；`npm run check:frontend`：五段通过。[Ruff](results/stage-8-ruff.txt)/[前端](results/stage-8-frontend.txt)。本阶段无前端行为修改，不重复浏览器实验或压测，不给出性能/模型质量改善结论。
- 开发指南原示例把last_execution当QueryResult，与真实执行节点字典不符；现更正并用真实SQLite空/非空执行结果驱动文档节点、YAML和Runner，均通过。[记录](results/stage-8-doc-example.txt)。示例仍未接入HTTP固定计划。
- `git diff --check`通过；原主工作区pyproject.toml SHA256仍为E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3。无运行数据、凭据或依赖目录纳入交付。

命令环境：测试设置LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock、DE_TRACE_DIR为忽略目录；本机失效.venv解释器仍以D:/Users/anaconda/python.exe加原site-packages运行。mypy隔离目录和Ruff固定版本路径沿用阶段7；CI使用pyproject声明干净安装。

## 范围调整与已知限制

类型收窄初次暴露EXPLAIN消费处三项不兼容，使用只读Mapping消除并全仓复核无新增诊断；没有用cast强行转换JSON。文档示例契约修正随本次类型文档同步完成。

全仓尚有201项，主要在API/MCP/RAG；本阶段未将整个workflow/API纳入类型门禁。原生驱动分发与异构上下文仍动态，协议不会运行时自动检查任意适配器。真实PG/MySQL在线SQL、异步驱动EXPLAIN及空结果列元数据需专门环境回归；本次只改变声明，未宣称修复这些兼容性。真实LLM/语义检索质量、业务标注评测及生产容量继续待相应资源，不虚构指标。

下一候选阶段优先收敛查询API/SSE/MCP共享请求、事件与错误DTO；先核对依赖和新基线再制定范围，不直接展开全面重构。

## Git与远程验证

本阶段普通提交/推送后按实际SHA与Actions补记；未合并main、未强推。远程CI未完成前不宣称本阶段远程验收通过。
