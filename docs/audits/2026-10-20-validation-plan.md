# 验证与评测方案（待实施）

对应[项目评估](2026-10-20-project-assessment.md)。本文件分清本次已经运行的检查和下一阶段建议执行的实验。所有尚未采集的数值均为“未测”，下述规模、梯度及停止条件是**实验设计**，不是系统已经达到的性能或质量。

## 1. 本次验证记录与边界

### 1.1 环境

- 时间：2026-10-20，Asia/Shanghai；仓库基准 `118f9d6`＋工作区，其中 pyproject 原有修改未覆盖。
- 默认命令 `python --version`：3.11.15，不符合项目声明；`.venv/Scripts/python.exe` 指向已不存在的旧Python313。
- 替代验证：已有 `D:\Users\anaconda\python.exe` 3.13.9，设置 PYTHONPATH 指向项目 `.venv/Lib/site-packages`。这是本次取证方式，不是建议长期混用虚拟环境。
- 隔离目录为 `.verify_tmp_assessment_20261020`，复制 app、tests、main.py、pyproject.toml、frontend_design；未复制 `.env` 和原 data。该目录匹配原有 `.verify_tmp*/` 忽略规则，收尾清理。
- `LLM_MOCK_MODE=1`、`RAG_EMBEDDING_PROVIDER=mock`、`LITELLM_LOCAL_MODEL_COST_MAP=True`，禁止把测试结果解释为真实LLM/语义embedding质量。
- 已安装版本：FastAPI 0.141.1、Pydantic 2.13.5、LanceDB 0.38.0、fastmcp 4.0.5、mcp 2.2.0、LiteLLM 1.102.0、sqlglot 30.18.0、pytest 9.1.1、pytest-asyncio 1.4.0、Ruff 0.12.0。仅记录当地环境，不据此推荐升级。
- 未运行完整 main/lifespan，避免对现有MySQL、持久索引和配置产生初始化写入。ASGI测试没有进入应用lifespan；因此不能证明依赖就绪、启动时索引或优雅退出可用。
- 沙箱内LanceDB创建event loop的socketpair等待；在允许的沙箱外只读导入/隔离测试中正常。该等待属于验证环境限制，不能作为项目性能证据。

### 1.2 已执行命令及结果

在隔离副本、使用上述解释器和环境变量运行：

```powershell
python -m pytest -q --disable-warnings --tb=short --junitxml=audit-test-results.xml
python -m mypy app/ --no-incremental
```

在原目录只读运行：

```powershell
ruff check . --no-cache --statistics
ruff check app main.py --no-cache --statistics
ruff check . --no-cache --select F821
```

- pytest：2842项，2833通过，9失败，0跳过，4 warnings，55.35秒。9项同属MCP构建，错误为旧 `mcp.server.fastmcp` 导入已不可用，详见[pytest.txt](evidence/2026-10-20/pytest.txt)。测试的总运行时间不等于业务请求耗时或系统吞吐。
- mypy：140个源文件，62个文件285项错误，详见[mypy.txt](evidence/2026-10-20/mypy.txt)。第三方缺types和业务签名问题混合，需分类清理，不能将285全部当成可触发运行错误。
- Ruff：全仓192项，app＋main158项；包括104项全仓E501、3项F821，后者位于auth/database.py延迟注解引用aiomysql。`from __future__ import annotations` 存在，所以不能仅据F821断言auth导入崩溃。详见[ruff.txt](evidence/2026-10-20/ruff.txt)。
- 未使用 `--fix`，未收集coverage；不输出覆盖率。

### 1.3 隔离行为探针

一次性脚本只在临时/内存数据库运行，结束关闭资源，未保留为产品测试或业务代码。原始结果：[probes.txt](evidence/2026-10-20/probes.txt)。

- SQLite `/* audit */ DELETE ...` 与 `WITH x AS (SELECT 1) DELETE ...`：detected_write=false，执行成功，剩余行数0。
- DuckDB `SELECT 1; DELETE ...`：detected_write=false，执行成功，剩余行数0。
- SQLite注册50ms的临时UDF，节点设置timeout_ms=1：仍成功，单次51ms；**这是约束复现，不是吞吐/延迟基线**。
- 无Authorization访问databases/settings-harness/workflows：均HTTP200。
- 150行临时users表，POST execute的max_rows=1、120：均返回100行；被拦截的普通DELETE为HTTP200＋status:error。
- 同步Mock查询：SQL占位字符串、HTTP200、execution=null、errors=[]，有Mock/自愈耗尽warnings；内存session有记录，但响应无session_id。
- 同样指定session_id的SSE：有done、无error事件，内存session未保存。
- QueryRequest传run_validation=false：实际true；传validate=false：实际false。
- 空registry运行缺节点plan：status=completed、error=null。
- 真实临时LanceDB/FTS5文档9块→1块：LanceDB1／BM25 9；Schema2列→1列：两端仍2。
- 文档chunk含正文 `body needle detail`，embedding_text只有 `audit — shared first line`。

### 1.4 页面验证

按AGENTS.md使用gstack `/browse`，未用其他浏览器自动化工具。只监听loopback的静态服务提供 frontend_design；不连接业务后端，不登录/注册/提交查询。

- 工作台1280×720与390×844可挂载，document.scrollWidth分别为1280、390，初始首页无横向溢出；首页10个button未发现完全缺少文本/aria-label/title的情况。这不是全站WCAG合格结论。
- 登录/注册两组控件同时出现在交互快照，需后续检查翻转背面是否应从Tab/无障碍树隐藏。
- 登录页英文、中文工作台和3D落地页的字体/边框/动效风格可见差异。
- 临时静态服务的API404是预期边界，不能算实际后端路由故障。
- 本地Babel8仅react preset解析workspace失败；浏览器Babel7显式transform同样失败，定位HTML第3846行TS注解。预览DOM却存在一段已编译inline脚本并实际挂载。编译来源/缓存与新浏览器行为未完全验证；报告保留矛盾，不推断“当前所有浏览器白屏”。建议在干净用户数据目录复验并建立正式构建。
- 截图：[桌面工作台](evidence/2026-10-20/workspace-desktop.png)、[手机工作台](evidence/2026-10-20/workspace-mobile.png)、[登录页](evidence/2026-10-20/login-desktop.png)、[落地页](evidence/2026-10-20/landing-desktop.png)。

## 2. 闭环与安全回归测试设计

先建立以下少量强断言测试，优先于继续增加“非空/200即成功”的测试。

### 2.1 固定计划与生命周期

- 从实际YAML加载计划，记录setup、execute、update、evaluate调用；断言顺序与数量、plan_id/version和节点输入来源。节点替身只代替LLM/I/O，不代替Runner本身。
- 缺节点、无效配置、未知metric、依赖不满足、setup/execute/update异常、达到调用预算、重试与迭代上限：必须有明确failed/aborted/exhausted状态，并停止后续不允许的步骤。
- 权限deny和ask未批准的工具从未被调用，LLM调用次数包括候选、fallback、自愈、时间解析等所有实际调用。
- reflection成功直接结束，需修订时按固定计划执行且有限次；未实现的计划语义在加载时拒绝。
- AST/静态规则验证节点不实例化其他节点来驱动execute，不直接依赖其他节点私有实现；Explain公共逻辑下沉后单独测。

### 2.2 SQL执行与隔离

- 在专用数据库逐类覆盖SELECT/只读CTE、块/行注释、写CTE、多语句、EXPLAIN包装、副作用函数、PRAGMA/ATTACH/COPY/导出、事务/会话设置，以及语法无法解析时拒绝。
- 用数据库最小权限账户再验证一次，即使应用检测失误也不能写表/文件/切换授权范围；检查表数据摘要/行数与临时目录，不对真实库试攻击。
- max_rows为1、默认值、上限、超上限和非法值；空结果仍保留列名；整数/Decimal/日期/时区/NULL/二进制的JSON契约；DuckDB columns必须为字段名列表而非description元组。
- 运行受控慢查询：应用总deadline、DB statement_timeout、用户取消、连接断开分别能终止底层工作；下一条请求仍可借到正常连接。
- 同库双请求/不同库双请求、执行时断开、重复db_id/同名不同host、连接测试probe竞争、shutdown后零遗留任务/连接。
- 身份A/B在独立数据源、会话、文档与领域上做允许/拒绝矩阵；单管理员模式则明确禁止普通用户改变全局设置。
- 领域名称含../、反斜杠、绝对路径、保留名、空白、Unicode边界：只在临时根内验证拒绝和不覆盖；设置出口不得含密钥/密文/密码。

### 2.3 API/前端联调

- 同一服务用固定SQL生成替身打通JSON和SSE，断言候选/主SQL/校验/执行/来源/会话一致，不能只比较字段存在。
- SSE覆盖：任意字节位置分包（包括UTF-8字符）、跨包event/data、连续事件、多data行、终止尾包、客户端断开、流式错误、完整done且只出现一次；HTTP前置校验与流内错误分开。
- 取消不得触发同步fallback；断线重连用相同query_id不能重复执行。HTTP200业务错误、4xx、5xx、429、超时分别显示可理解的错误与安全的重试入口。
- Browser通过gstack检查核心六步；使用干净浏览器context、冷缓存/暖缓存、CDN失败；校验演示与真实数据永久区分、不混入统计。
- 视口390/768/1280/1440，测试空结果、宽表、长SQL、多候选、超长错误、历史列表、中文输入法；Tab顺序、可见焦点、Escape关闭、focus恢复、dialog/表单关联、读屏进度和减少动画。
- 用独立MySQL验证登录、密码更新、删除账户、JWT过期、401退出；不要把无后端的静态页面检查当成鉴权验收。

### 2.4 持久化与索引

- 保存后重启：session、query、version、domain、feedback和连接配置应遵循明确的存储约定；持久化失败时前端能知道，不静默宣称已保存。
- 文档长→短→删除→重传、同标题不同domain、Schema删列/删表/新增/改名/断开：逐项核对LanceDB与BM25的doc_id集合和召回，重复操作幂等。
- embedding切换同维不同模型、不同维、启动失败、中途写入失败、并发刷新、重启恢复：不混用模型，保留原始文档和可恢复旧版本。
- 配置设置保存后用实际运行参数验证，而非只读回字典；记录已应用的版本、重建状态及失败原因。

## 3. RAG标注集与质量基线

### 3.1 首批数据规模

先用30题证明评测链路，再建立**120条检索问题**，建议拆分Schema 60、业务指标/术语30、文档30。问题使用中英、同义词、缩写、错别字、模糊表达、跨表FK、正文深处条件、同名异库/异域、已删除内容和无答案等组合；关键概念用中英配对但放同一数据分区。

每条记录至少包含：

```text
question_id, question, language, domain_id, authorized_db_ids,
schema_version, corpus_version, query_type,
relevant_doc_ids, graded_relevance(0/1/2),
required_tables, required_columns, required_join_edges,
answerable, annotation_reason, reviewer, split
```

- 相关性由业务人员和工程师复核；分歧保留原因并裁决。文档同时标chunk级与parent级，防止同一篇文档多个近邻片段虚增效果。
- 建议60开发/30验证/30冻结测试；按业务模板/同义改写分组拆分，避免同一问题改写泄漏。调权重或选重排器只看开发/验证集。
- 同时保留无答案/越权负例；召回到“看起来相似但不应使用”的内容算错误，不以返回满K作为成功。

### 3.2 指标定义

在K=3/5/10报告macro均值和按语言、namespace、跨表、无答案、域过滤的分组结果：

- **Recall@K**＝Top-K命中相关文档数／该题相关文档总数。
- **Precision@K**＝Top-K相关文档数／K。过滤后不足K时缺位计未命中；另报实际返回数，不能用缩小分母掩盖不足K。
- **MRR@K**＝每题第一个相关结果名次的倒数；前K无相关为0，再对问题取均值。
- **nDCG@K**＝按0/1/2相关性和位置折扣计算的DCG／理想DCG，约定gain=2^rel−1。只有二元标注时仍记录使用的标注方案。
- 无答案题不混入Recall的零分母：另报**误召回率、拒答/无结果率**；越权命中为硬失败。
- NL2SQL专用：所需表/列召回率、join路径覆盖率、上下文有效信息占比、prompt token量、无关表占比。
- 工程指标：检索P50/P95/P99、embedding与dense/sparse/fusion分段耗时、索引构建/更新耗时、错误率、磁盘/内存与模型调用成本。

### 3.3 对照实验

冻结同一索引/embedding模型、查询集与配置，依次对比：

1. 当前关键词Schema Linking（不依赖语义向量的起点）。
2. BM25-only；dense-only；当前RRF。
3. 修复全文chunk索引和检索前过滤后的RRF。
4. 经验证的SchemaRetriever/FK扩展、领域术语和去重组装。
5. 如前述仍有明确排序错误，再评独立reranker；记录增加的延迟/成本，不能把RRF融合叫作已实现语义重排。

固定种子和模型；每次保存返回doc_id/rank/score/过滤范围与prompt实际使用内容。小样本先做paired比较和失败分析；120题的置信区间应同时报告，避免用小幅波动宣称显著提升。Mock embedding只测形状、索引和流程，不参加语义效果排名。

## 4. NL2SQL评测集与方法

### 4.1 样例规模与数据库

建议独立于检索集建立**120条业务任务**，中英各60；先筛30条作闭环冒烟。建议任务组成：简单过滤/排序25、聚合/时间25、多表/关联25、子查询/CTE/窗口15、术语/口径/强制规则15、歧义/无答案/权限/安全15；分类允许多标签，但每题一个主类，主类总计120。

- 从现有电商SQLite fixtures扩充真实业务变体，另选一个已审定领域；不把当前库里的几条示例查询直接当泛化测试集。
- 初始SQLite与DuckDB，确定MVP承诺后加入PG/MySQL。每种方言用相同业务数据和对应gold SQL，明确NULL、重复、日期边界/时区、币种和精度规则。
- 数据至少包含空表/无匹配、多对多/一对多、重复join键、NULL、同名列、软删除、退款与跨年等边界；仅比SQL字符串不能证明结果正确。
- 60开发/30验证/30冻结测试，按业务模板拆分；不把冻结测试中的gold SQL或评分反馈写回few-shot库。

建议样例字段：

```text
case_id, nl_text, language, domain_id, db_id, dialect,
dataset_version, schema_version, business_definition,
gold_sql, expected_result_or_hash, ordering_required,
numeric_tolerance, time_reference, clarification_expected,
required_rules, forbidden_operations, difficulty, split, reviewer
```

### 4.2 指标与判分

- **语法通过率**与**Schema引用通过率**分开统计。
- **执行成功率**：应回答且有授权数据源的题，SQL在超时/只读约束内成功执行的比例；基础设施/模型失败计入端到端失败，另标具体原因。
- **结果正确率**：同一快照上与gold结果等价的比例；无ORDER BY时按多重集合比较，保留重复行；排序题按序比较；数字按约定容差；NULL、日期时区和列语义按题目规则处理。不能让总行数相同替代结果相同。
- **业务规则遵守率**、**安全拒绝率**、**应澄清问题的澄清正确率**独立报告；无答案/拒绝题不混入普通SQL执行成功分母。
- **主候选Top-1正确率**用于产品指标；**candidate oracle准确率**表示候选中至少一条正确的上界，只供分析，不冒充用户成功率。
- 端到端与分段P50/P95/P99、首阶段/首token时间、DB耗时、重试次数、调用次数、input/output tokens、单请求成本、每个正确答案成本。
- 如果LLM供应商未返回stream token用量，成本标“估算/缺失”并注明计价版本；不能填0假装无成本。无测量时结果栏为空值并写未测。

相同题目每配置至少重复3次，报告均值、波动和失败例；记录模型确切标识、temperature、prompt版本、候选数、自愈预算。模型即使temperature=0也不假定完全确定。人工复核所有与gold不符但可能等价的SQL，并修正标注而非随意放宽评分。

### 4.3 最小消融次序

当前默认路径 → 只接过滤Schema → 加审定领域规则 → 加相似查询 → 加自愈 → 调候选数量。每步只改一个主要变量；比较结果正确率与额外token/延迟，保留回归题。多Agent辩论、更多模型或reranker放在可解释基线之后。

## 5. 高并发与性能压测设计

### 5.1 执行条件与环境登记

先通过只读/鉴权/超时和功能冒烟，再在隔离环境压测；本次未运行。登记CPU型号/核数、RAM、磁盘类型、OS、Python/依赖版本、worker数、DB版本/部署位置、pool配置、embedding设备与模型、LLM配额/网络、索引大小、数据快照、缓存冷热状态。

工具可用Locust或k6，SSE需能解析done/error和断线而非只看HTTP200；这是后续选型，本次未安装。压测机与服务分开，先确保发生器不饱和。先单worker检查共享native连接问题，再按部署承诺测2/4 worker及状态一致性；不得先扩worker掩盖正确性问题。

### 5.2 数据规模

- DB小档：users 1千、orders 1万、items 3万；中档：users 1万、orders 10万、items 30万；大档：users 10万、orders 100万、items 300万。可按测试硬件缩放，所有档保留相同索引和选择性分布说明。
- Schema规模：50／500／2000张表，每表约20列，包含FK/同名列/长注释；此项用于内省与上下文预算，不必都填满业务数据。
- RAG规模：100／1000／10000 chunks，Schema元数据及指标单独计数；同一domain与跨domain混合，保留确定的标注问题。
- 至少一个同库热点、多个不同db_id，以及相同database_name不同host的隔离场景；禁止将未过滤的全库扫描结果当作正常高QPS流量。

### 5.3 分层场景

1. **协议/应用层**：固定生成替身返回合法gold SQL，保留实际API、Runner、校验、临时DB与持久化。单次候选/三候选分别测，分离模型网络波动。
2. **DB执行层**：只读点查、低选择性查询、聚合、join、返回上限、空结果、受控慢查询；同库与异库分别测。验证超时不只是HTTP结束，底层查询确实停下。
3. **RAG层**：warm/cold query embedding、dense/sparse/hybrid、domain过滤、并发刷新/检索、模型服务不可用。mock只测软件开销，真实embedding独立跑。
4. **真实LLM完整链路**：受预算限制的独立批次，从1/2/5并发起，按供应商配额扩到10/20；记录429和重试，不把配额限制混为本机CPU瓶颈。
5. **混合交互负载**：初始建议60%查询、20%直接执行、10%Schema读取、5%历史、5%设置/学习只读；设置/索引更新作为独立低频干扰场景，不随机清库。后续按真实使用日志调整比例。
6. **异常与稳态**：10%请求主动取消、SSE中途断线、模拟provider429/5xx/超时、DB连接耗尽或断开、存储失败；正常负载30分钟观察内存/句柄/连接是否持续增长。依赖故障使用测试代理/替身，不能操作生产服务。

### 5.4 并发、时长和停止条件

- 去模型的基准梯度：**1、2、5、10、20、50**；只有稳定且硬件/队列允许时再测100。真实LLM遵守前述更小梯度与预算。
- 每档30秒升压、60秒预热、5分钟采样；关键档重复3次。纯静态/极快接口与真实LLM分开设计采样数，不强行给低样本真实LLM的P99高置信结论。
- 先用闭环固定虚拟用户测并发，再用到达率控制的开环逐级增加请求，观测排队，避免coordinated omission低估尾延迟。RPS梯度根据第一轮单并发服务时间设定，不在未知容量时臆定峰值QPS。
- 初始停止规则：连续60秒错误率>5%，或资源>90%且队列持续增长，或数据库/模型预算耗尽，或出现写入/越权/数据串扰，立即停止并保存证据。该规则是测试护栏，不是产品SLO；未触发也不等于达到生产要求。

### 5.5 必采指标与口径

- **吞吐量**：实际完成请求/秒、成功完成业务请求/秒、SQL执行/秒分别记录；SSE以done且业务成功为完成，HTTP200不能替代。
- **延迟**：端到端P50/P95/P99，排队、解析、内省、检索、prompt、LLM、校验、自愈、DB、持久化耗时；SSE首阶段/首token/完成各计时。包含失败/取消时另给分布，避免只看成功的幸存者偏差。
- **错误**：HTTP类别、业务失败、取消、超时、provider429/5xx、连接冲突、权限拒绝、检索失败、持久化失败和静默fallback计数；标注拒绝类不等于系统故障。
- **资源**：进程/worker CPU、RSS、event-loop lag、线程/FD/句柄、连接池活跃/空闲/等待、DB CPU/IO/锁/慢查询、LanceDB/FTS5 IO、GPU/Ollama显存与队列、网络。
- **工作放大与成本**：每请求LLM/embedding/DB次数、重试/自愈、候选数量、生成/执行扇出、tokens、成本；按单库/多库、冷热缓存分组。
- **正确性**：并发过程中结果hash、db_id/session归属、trace阶段完整性；性能更快但结果串库不能视作优化。

### 5.6 定位瓶颈的证据要求

事件循环lag与同步阶段耗时上升才能支持“阻塞影响吞吐”；pool等待与DB慢查询上升才能区分连接竞争和DB计算；tokens/429变化用来判断模型侧限制。保存负载曲线、时间序列、原始直方图和服务/DB日志，不能凭一条慢请求或测试套件耗时下结论。

每次报告必须包含：

```text
run_id, code_commit, dirty_state, environment_id, started_at,
dataset/schema/corpus_version, model/embedding/prompt_version,
scenario, arrival_model, concurrency_or_rate, duration, request_count,
throughput_all, throughput_business_success, latency_p50/p95/p99,
error_categories, cancellation_count, resource_peaks,
llm_calls/tokens/cost, correctness_failures,
baseline_run_id, change_under_test, limitations, raw_artifact_paths
```

## 6. 基线建立与后续决策

当前能填入的基线仅为第1节的测试/静态检查/行为复现。以下状态必须保持明确：

- 检索Recall/Precision/MRR/nDCG：未测，需标注与真实embedding。
- SQL执行成功/结果正确率：未测，需gold数据与真实模型；Mock占位字符串不能纳入。
- 请求吞吐与P50/P95/P99：未测，需稳定环境和负载发生器。
- 单请求模型tokens/成本：未测，需真实usage或明确估算方法。
- 测试覆盖率：未采集；后续在固定环境用coverage分支报告辅助寻找未覆盖路径，不能替代业务验收。

第一轮只建立可重跑结果，检查分母和失败分类；第二轮再比较F01–F24对应的单项变化。安全与权限属于硬门槛；其他目标由业务风险、用户等待容忍度和实际基线共同确定，写成明确SLO后才作为发布门禁。
