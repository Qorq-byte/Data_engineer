# 阶段7：Harness与模型严格类型边界

日期：2026-10-21；基准493f3e9，上一阶段及其文档提交Actions均成功。按照[先行六步计划](stage-7-plan.md)完成，保持既有计划、Runner和节点分层，不做架构重写。

## 实施结果与代码依据

- `app/harness/events.py`：用TypedDict明确节点事件必填/可选字段、三种真实状态及可await回调。`runner.py`评估接口使用NodeDef/NodeOutput，保留固定计划流转和生命周期；失败说明安全处理可空评估。查询服务只适配此事件类型，SSE字段与执行顺序不变。
- `harness/config_loader.py/plan_loader.py`：补字符串键的异构配置容器，拒绝空值、标量、列表等非映射根/agent/plan段并给出ValueError。有效配置、默认值、缓存和mtime热更新保持；这不是完整YAML业务字段验证。
- `harness/permission.py/session.py` 与模型：明确已有字典/列表、候选结果、数据库绑定的容器参数；外键引用为(table,column)二元组；SQLite活动时间转为float后比较，避免Any返回bool。异构JSON和SQL单元格仍是动态值，不把显式字典参数化描述为完整Schema验证。
- `db/runtime.py`：Hashable键与Semaphore表有明确类型，表仍归属于实际serving loop，不增加全局loop缓存。`connections.py`补配置容器，并在关闭分支重申已验证非空连接。取消等待、同连接lease、线程中断、池关闭、只读策略和权限/预算未放宽。
- `llm/router.py`：非流式complete拒绝非字典响应；mock流明确AsyncIterator；移除已失效的litellm import屏蔽。物理预算、限流、超时、重试、fallback和流关闭规则保持。
- `typings/aiomysql/__init__.pyi`：按已安装aiomysql0.3.2的pool/connection/cursors/utils源码声明项目实际使用接口，保留await/上下文、池/游标具体类型，参数和资源对象不以Any代替。游标重载保留传入子类，因此DictCursor返回字典行列表。没有__getattr__兜底；这是局部类型表面，不是运行时驱动或整个库的完整声明，详见[说明](../../typings/README.md)。
- `pyproject.toml`：开发工具锁定mypy2.4.0，补正式types-PyYAML和asyncpg-stubs，并接线本地typings。asyncpg-stubs0.32对应开发环境asyncpg0.32；生产最低依赖未改。`.github/workflows/checks.yml`增加标准 `mypy app/harness app/models`，不加silent/skip、ignore_errors或放宽strict，既有Ruff和全量测试保留。

## 验证命令与实际结果

本机Windows/Python3.13.9；mypy2.4.0、目标Python3.12；类型工具/正式stub隔离安装在忽略目录。运行测试仍用原site-packages：LiteLLM1.102.0、aiomysql0.3.2、asyncpg0.31.0，LLM与embedding强制mock，真实本地SQLite/DuckDB/LanceDB/FTS5。类型检查使用正式asyncpg0.32声明与对应开发驱动；没有把本机原驱动版本和类型声明混称同一运行环境。

- 初始全仓：**272项/63文件**，核心标准命令（默认追踪导入）**37项**。此基线已带YAML声明，尚未接线正式asyncpg与本地MySQL声明；不同于历史279项环境。
- 最终核心 `mypy app/harness app/models`：**Success: no issues found in 24 source files**，默认分析其DB/LLM依赖，没有隐藏依赖诊断。见[核心日志](results/stage-7-core.txt)。
- 最终全仓 `mypy app/ --output json`：**230项/49文件，仍未通过**。以文件/错误码/消息构成诊断指纹并保留重复计数，新增指纹为0；272→230包含正式/本地声明接线与代码修复，不能归因于单纯注解改动。见[初始全仓](results/stage-7-before.jsonl)、[最终全仓](results/stage-7-after.jsonl)、[分类对照](results/stage-7-summary.json)。没有对全仓失败设置通过或改成忽略。
- 定向Harness/DB/LLM/模型/查询契约：**270 passed，14.07秒**（包含第一批16项新增测试），[日志](results/stage-7-target.txt)。后加认证元数据5项纳入最终全量。
- 最终全量 `python -m pytest -q`：**2964 passed，1 skipped，4 warnings，54.17秒**，[日志](results/stage-7-pytest.txt)。比原2943新增22个用例，本机执行其中21项：畸形YAML与恢复、错误模型响应、真实空MySQL池上下文关闭、认证元数据拒绝/顺序。另1项检查uvicorn Linux使用的uvloop门闩兼容性，Windows无uvloop而明确跳过，待Linux CI执行；没有关闭任何既有测试。空池minsize=0不调用acquire，不打开外部MySQL连接。
- `ruff check .` 与 `npm run check:frontend`：全仓lint、三页五段脚本通过，[Ruff](results/stage-7-ruff.txt)/[前端](results/stage-7-frontend.txt)。未改前端行为，不重复浏览器性能或模型质量实验。
- `git diff --check`通过；原主工作区pyproject.toml SHA256仍为E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3，用户改动受保护。
- `docs/node-development.md`同步实际NodeOutput字段、await setup_input、单参数registry注册、YAML节点结构及事件回调；明确自定义示例不自动接入固定HTTP计划。文档中的质量节点/YAML/Runner已用空/非空合成结果实际执行通过，[日志](results/stage-7-doc-example.txt)，未新增此业务节点到项目主流程。

## 新发现与范围调整

真实驱动的cursor.description可空，初版声明揭示认证读取的五个直接遍历点。`app/api/auth.py:_auth_column_names`现在验证元数据，缺失/畸形时明确503，避免构造部分认证记录；5个调用点复用，既有登录/权限策略未改。第一次声明未保留DictCursor子类，误把fetchall退化成Sequence，已按真实返回类修正，没有把返回值伪标成Any或把可空信息删掉。

本阶段未新增ignore或cast。原生多驱动句柄、节点异构输出、YAML值和SQL单元格仍有既存动态边界；核心strict通过不能证明所有运行数据都正确或获得多线程/多进程隔离。类型声明源码核对与空池测试不等于真实PG/MySQL查询联调，更不等于真实模型质量验收。

资源复核保留原有动态属性语义，通过共享属性名访问loop持有表，避免要求所有Loop提供可访问的__dict__；新增uvloop实际兼容检查并纳入Linux CI。此调整后重新运行了最终全量测试。

## 保留事项与后续顺序

全仓230项仍含128个容器参数问题，以及节点/HTTP接口、MCP、LLM适配和第三方类型边界。下一阶段优先收敛执行节点与查询适配契约，尤其是GenerateSQLNode与BudgetedRouter协议、可空Schema目标、API请求/响应和session形状；先确认新的依赖闭包与基线再改，不直接扩展本阶段范围。真实模型/RAG标注评测和外部数据库稳态仍需凭据、业务样本与运行环境；本阶段无性能或准确率结论。

交付继续在codex/mvp-hardening独立提交、普通推送与远程核对，不合并main；远程Actions结果按对应提交核验后补记。

## 远程交付验收

代码提交 [ac87480ec97defebf545cc88097bcfec0ddc26cc](https://github.com/Qorq-byte/Data_engineer/commit/ac87480ec97defebf545cc88097bcfec0ddc26cc) 已推送，ls-remote核对一致。[Actions39531961041](https://github.com/Qorq-byte/Data_engineer/actions/runs/39531961041) **success**：干净安装、前端编译、全仓Ruff、标准核心mypy均通过；核心检查24源文件无问题，Linux全量 **2965 passed，1 warning，31.35秒**，uvloop用例实际执行通过。Windows本地1项平台跳过没有被当成远程已验证；现已由Linux补足。

[远程状态证据](results/stage-7-ci.json)记录对应headSha与各步骤。全仓mypy230项是前述本机测量，远程此次执行的是核心门禁，不能将此Actions成功解释为全仓类型通过。后续本次文档补记不改变已验证代码。
