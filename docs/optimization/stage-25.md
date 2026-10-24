# 阶段25：LLM设置持久化与运行时发布

2026-10-23，按[先行计划](stage-25-plan.md)实施；基线53a6ccd（Actions39923884366成功），分支codex/llm-settings-persistence，不合并main。

## 已确认与实现

旧settings.py在验证策略前修改共享字典、_persist_section吞写失败，DELETE不更新路由；读取/启动回退掩盖存储失败，应用异常响应带原异常。页面四类写入口只按HTTP成功判断，新增提供商曾将明文key放入providers状态。旧2回归[失败](results/stage-25-before.txt)，修复后含既有LLM测试首轮[9通过](results/stage-25-first.txt)。

- app/api/settings.py:_read_llm_configuration/_llm_candidate/_commit_llm_configuration：专用限时读取与深拷贝候选，先构建、确认保存、同步发布；无存储明确memory_only，失败503/unconfirmed且不发布，取消不吞。PUT/DELETE/读取/启动同一loop gate，删除重建路由；聚合GET覆盖公开LLM段，available-models不吞LLM读取错误。
- app/llm/factory.py:current_llm_router/publish_llm_router允许只读查看当前实例及同步发布预构建路由；原apply入口复用，既有fallback/mock行为保持。旧在途请求不强制中断。SettingsRoute只对LLM参数错误固定422，公开视图和错误不回显明密文密钥，异常链隐藏原始敏感原因。
- workspace.html:llmConfigurationSnapshot/fetchLlmConfiguration/commitLlmChange统一四类写入口；校验持久化/运行真值、普通字段和提供商变更；仅目标提供商patch，删除核对缺失。同步ref防重复、读取序号丢弃旧响应、失败未知先重读、成功/失败清敏感输入，providers白名单无明文key。新增/开关后模型选项同步刷新，重读清旧反馈，memory_only为警示。

保持固定计划/Runner/节点隔离与SQL只读；未改模型选择策略、加密算法或数据库池。接口/恢复/兼容及保留字段边界见[契约](llm-settings.md)。

## 实测环境与结果

Windows Python3.13.9/Node24/Ruff0.17.1/mypy2.4.0，沿用原site-packages与type-deps/driver-types。LLM_MOCK_MODE=1、RAG_EMBEDDING_PROVIDER=mock、LITELLM_LOCAL_MODEL_COST_MAP=True、追踪在忽略目录；新增测试显式隔离环境provider密钥，合成密钥不调用真实provider。

- 新增26项Python回归，真实SQLite设置协议/原生SQLITE_INSERT与READ拒绝、AES密文冷恢复、删除同步、超时、提交前/后取消、两写+启动gate、准备失败/无部分修改、畸形存储/不回显校验input及401/403。python -m pytest tests/test_api/test_llm_persistence.py tests/test_api/test_settings_llm.py -q：**33通过，9.80秒**，[日志](results/stage-25-targeted.txt)。候选构建错误有注入回归；加密异常走同一外层固定失败路径，未单独注入加密函数失败。
- python -m pytest -q：**3416通过、1跳过、4警告，115.60秒**，[日志](results/stage-25-pytest.txt)；Windows uvloop现已由下述Linux实际执行补验。Ruff通过，[日志](results/stage-25-ruff.txt)，既有核心mypy命令**35文件零错误**，[日志](results/stage-25-core.txt)。全仓mypy app --output json仍退出1，**181项/42文件**，相对184项新增0/移除3（LLM API返回类型完善），[诊断](results/stage-25-after.jsonl)/[摘要](results/stage-25-summary.json)，未弱化检查。
- npm run check:frontend：5段源码编译、既有141+新**33实际函数契约，共174项**，[日志](results/stage-25-frontend.txt)。严格快照、密钥白名单、读取失败、持久化/内存、畸形成功响应、字段不符、防重复/未知后禁写、交错读取与四写入口实际函数；npm run check:frontend:build本地三页/资源/字体/许可证、SHA256、重复构建/失败保留通过，[日志](results/stage-25-build.txt)，Expected identifier是故意负例。最终前端变更均重跑契约与构建，Python未变沿用全量有效结果。
- gstack /browse **13场景**，[结构化证据](results/stage-25-browser.json)：正常、持久化、仅内存警示、原生写/读拒绝、畸形写200/读200、重读恢复、新增加密、开关、删除、持久化重建路由、375px布局。临时FastAPI127.0.0.1:8025，lifespan关闭避免真实MySQL，临时SQLite/合成JWT与qa-synthetic-key，mock路由，QA模式和同源CSP仅忽略目录。新增临时提供商加密标志true/明文存储false，开关false、删除及重建后缺失，响应/页面无key；外部资源0。[桌面](results/stage-25-desktop.png)/[手机](results/stage-25-mobile.png)均已查看，375px无横向溢出，重读按钮在视口；不是完整无障碍审计。[控制台](results/stage-25-console.txt)保留预期mock就绪503/原生读写503等，无React异常；中断前后浏览器会话分开，截图/状态按实际阶段保留。

## 调整与限制

初始SQLite适配器默认跨线程限制，会把TestClient线程异常误当存储故障；改check_same_thread=False后临时恢复HEAD旧源码重跑并finally恢复新代码，两个旧回归真实失败后重新验证通过。序列化测试原先受环境密钥触发既有模型fallback，显式隔离凭据而不改fallback或弱化目标；VM跨realm对象比较改结构比较，测试替换api忘记_headers已补，handler测试改执行实际提交路径。Ruff格式/返回类型按检查修复，未关规则。一次无效编码默认读取产生UnicodeDecodeError，未写文件；后续均明确UTF-8。无关Harness/RAG格式变化已还原。

审查额度上限曾拒绝浏览器续验命令，未执行且未绕过；用户继续后原审批恢复。旧后端仍存活，先核实PID/QA数据再恢复独立gstack会话，完成未执行的切换/删除/恢复/手机检查。一次重复启动因日志占用失败，没有另建服务或覆盖数据；临时服务与令牌均不提交。

本阶段是单进程/loop内发布，不是持久化与运行时的分布式事务；取消可能已提交，GET不自动应用漂移。同步驱动真正取消、跨worker一致性、真实MySQL/生产模型/压测/语义质量未测，单测耗时不是性能结论。密钥URL/SSRF策略未新增，routing_strategy/temperature/max_tokens全链路消费仍待独立核对；181项类型债务与7项构建期依赖告警保留。原工作区pyproject.toml/docs/audits及隔离旧未跟踪文件保留。

## Git与下一步

代码[5e1bd01](https://github.com/Qorq-byte/Data_engineer/commit/5e1bd01ac4028c14fa90920fac57b4387c02b28e)已普通推送codex/llm-settings-persistence，远程SHA一致；[Actions39947535210](https://github.com/Qorq-byte/Data_engineer/actions/runs/39947535210)成功且headSha精确匹配。Linux干净安装、174前端契约/构建完整性与重复性/失败保留/Ruff/strict35均通过，全量**3417 passed、1 warning，59.23秒**，uvloop实际执行；[结构化证据](results/stage-25-ci.json)。阶段25代码验收完成，远程补记文档另行提交推送并核对其CI；不合并main。下一候选核对已保存的路由/生成参数实际消费者，先规划最小范围再实施。
