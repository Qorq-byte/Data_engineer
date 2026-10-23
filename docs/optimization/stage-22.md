# 阶段22：RAG配置与运行时生效闭环

2026-10-23，依据[先行计划](stage-22-plan.md)实施；基线1ffe66e及最终Actions39887572810成功，独立分支codex/rag-effective-settings。main不合并，原工作区未提交改动保留。

## 已确认问题与实现

`app/api/settings.py:update_rag_settings`原只改设置存储、吞持久化失败；`app/rag/engine.py`初始化时仅读vector_weight，现有三个RRFFusion不变。页面独立修改两个权重且无和约束，并提供没有全局消费者的Top-K/阈值/embedding模型保存；默认模型与实际provider可不一致。两个旧代码回归均失败后才改实现：[失败记录](results/stage-22-before.txt)，首次修复后2通过：[记录](results/stage-22-first.txt)。

- `app/models/rag_settings.py:FusionWeights`是纯冻结dataclass，验证有限范围并推导互补BM25权重。RAG专用请求模型仅接受数值权重、extra=forbid，空/null/和不为1拒绝。历史vector为权威读取入口，旧未生效字段保留在存储但不冒充运行配置。
- `settings.py:_load_rag_configuration/_rag_settings_view`区分配置来源、实际运行与未支持字段；聚合设置也用此视图。RAG持久化专用路径有10秒await截止，不复用吞错助手；成功后才同步发布运行/内存，无存储明确memory_only，未初始化明确runtime_applied=false。读写失败固定503不泄漏原异常，取消不吞；已提交但取消可能形成配置/运行漂移，不能称数据库回滚。默认字典深拷贝，避免修改会反向污染默认值。
- `engine.py:apply_fusion_weights/get_runtime_settings`读取实际三个实例与实际embedding provider/model/dimension，不从保存字段猜测。引擎初始化和设置操作共用loop gate；三个检索器新增set_fusion，在检索第一次await前捕获引用，保存只同步替换融合器，不改索引/维度/隔离。一次检索有固定快照，完整查询的多个检索调用仍可能跨配置版本，单进程/事件循环边界不变。
- `workspace.html:ragConfigurationSnapshot/fetchRagConfig/handleSaveRag`严格检查业务结构与三种运行权重，权重滑块互补；区分编辑/已配置/运行、展示实际embedding、未接通选项只读说明。读取失败未知并禁保存，保存只发权重；即时ref防重复，GET序号丢弃旧响应，不自动重试。反馈持久化/临时/未就绪分别表达，不吞失败。

未改节点调用、工作流计划、SQL只读策略、模型选择或数据库池行为；Harness/database页面类似未接通问题已写明，未借本阶段无依据改造。

## 实测与环境

Windows/Python3.13.9、Node24、Ruff0.17.1、mypy2.4.0、SQLite3.51.0、DuckDB1.5.5；沿用原site-packages与type-deps/driver-types，测试LLM_MOCK_MODE=1、RAG_EMBEDDING_PROVIDER=mock、LITELLM_LOCAL_MODEL_COST_MAP=True，追踪输出在忽略目录。没有生产模型/私有数据依赖。

- 新增38项Python回归，真实临时SQLite设置适配器、LanceDB/FTS5，不mock数据库。包含严格参数/权限、单项互补/边界、持久化后清内存加载、读写原生权限拒绝、真实await超时、提交后取消漂移、保存顺序/初始化竞争、三个在途检索快照及聚合视图/默认值不别名。定向原配置/启动加新增首轮**61通过**，[日志](results/stage-22-targeted.txt)；之后补两项聚合/默认引用验证，全量覆盖。
- 可控正交向量让真实dense/BM25排序冲突，保存alpha=0与1后结果分别为rank.a/rank.b；证明实际新检索融合接线，不能当真实语义Recall/准确率。真实MySQL驱动/事务没有运行环境，SQLite仅验证存储协议；延迟/取消用显式控制事件，原生SQLITE_INSERT/READ拒绝与人工超时分开标注。
- 最终`python -m pytest -q`：**3342 passed、1 skipped、4 warnings，102.85秒**，[记录](results/stage-22-pytest.txt)。Windows uvloop跳过待Linux补验。新增dataclass自动进入既有app/models门禁，核心strict由34扩至**35文件零错误**，[记录](results/stage-22-core.txt)；Ruff通过，[记录](results/stage-22-ruff.txt)。全仓mypy仍退出1，**188项/42文件**，相对194项新增0、移除6（_get_store返回类型及RAG端点类型完善）：[诊断](results/stage-22-after.jsonl)/[摘要](results/stage-22-summary.json)。不缩小导入或类型检查范围。
- `npm run check:frontend`：5段源码脚本编译、原49契约+新**33实际函数场景，共82项**，[记录](results/stage-22-frontend.txt)。覆盖无/有引擎快照、畸形/非有限权重、读失败/过期响应、仅权重payload、持久化/临时/未就绪、错误200不当成功、失败后未知、不重试、busy/ref/finally、重新读取清过时反馈。`npm run check:frontend:build`三页资源/字体/许可证、字节一致、真实编译失败保留旧产物均通过，[记录](results/stage-22-build.txt)；日志Expected identifier为故意负例。
- gstack `/browse`最终复跑**10项浏览器场景**，[结构化证据](results/stage-22-browser.json)。临时真实FastAPI仅127.0.0.1:8022，关闭lifespan避免真实MySQL，显式初始化SQLite源3行、SQLite设置协议、持久临时LanceDB/FTS5和mock embedding。合成管理员/QA控制仅忽略目录；同源CSP仅QA注入，外部资源请求0，不作为生产路由或安全策略交付。
- 键盘Home/ArrowRight将向量改0.2时BM25自动0.8，已配置值仍旧0.7直到保存；真实PUT后SQLite与三个运行实例0.2。无存储显示重启不保留；原生INSERT拒绝仅1次PUT、不自动写重试，显示未确认并禁保存；原生READ拒绝显示未知；外部存储改0.9后GET显示配置0.9/运行0.2漂移而不应用；显式关闭引擎保存不称已应用，再初始化/读取确认0.9并清除旧反馈。全过程源行数3不变。
- [桌面](results/stage-22-desktop.png)/[手机](results/stage-22-mobile.png)均查看；375px文档/正文宽均375，滑块及按钮在视口内、标签关联正确。不宣称完整无障碍审计。[控制台](results/stage-22-console.txt)保留mock readiness503和两轮故障注入503，无React运行异常。

## 发现后的调整与限制

浏览器首轮发现：重新就绪并成功读取后仍留“尚未应用”旧提示，已在fetchRagConfig清除旧反馈，并增加实际函数断言、重新构建/复跑全部浏览器情形，旧截图不计验收。侧栏动画导致过期ref、宽泛状态选择器匹配多个，改fresh snapshot/可见导航和精确role后重做；属于测试定位问题。Ruff格式与新增类型诊断已修复，未禁用规则或弱化测试。

本阶段只完成RAG权重闭环；Top-K/统一阈值/embedding切换、其他设置仍不自动生效。GET报告的是实例/配置状态，不是存储或provider实时健康。存储deadline不含gate排队，底层同步I/O能否及时被取消待真实环境验证；多进程不广播，存储取消可能已提交，没有跨系统事务。全仓188项类型债务及阶段21的7项构建期依赖告警保留，不新增检索质量/成本/容量结论。接口兼容、恢复与部署步骤见[配置生效契约](rag-settings.md)。

## Git与下一步

待审查暂存/凭据后普通提交推送codex/rag-effective-settings，核对远程SHA/Linux CI再补记；不合并main。下一候选先核对Harness设置与固定计划/Settings实际约束的对应关系，优先消除错误展示和无效保存，独立规划后再实施。
