# 阶段23：Harness真实约束只读状态

2026-10-23；按[先行计划](stage-23-plan.md)执行，基线87b911d（Actions39912481496成功），分支codex/harness-policy-status，main不合并，原工作区改动保留。

## 实现与依据

已确认旧settings.py Harness PUT仅保存未消费的参数并吞持久化错误；GET透传旧值，页面能关闭只读并声称保存。旧2例先[失败](results/stage-23-before.txt)，最小实现后[2通过](results/stage-23-first.txt)。采用真实状态只读方案，未增加跨来源热更新或改变运行约束。

- app/workflow/query_service.py:describe_query_policy直接使用请求同一_CONFIG_LOADER/_request_plan和Settings；返回实际预算/较小重试与SQL上限、毫秒语句超时与秒查询总时限、内存TTL、节点顺序及来源。只读根据执行器无条件检查，不以未消费的布尔声明判断。快照无写副作用，不是数据库健康或在途请求历史。
- app/api/settings.py:get_harness_settings不依赖旧设置存储，读取异常固定503 HARNESS_POLICY_UNAVAILABLE、不返回旧值。update_harness_settings明确409 HARNESS_SETTINGS_READ_ONLY，删除旧可编辑模型；聚合GET覆盖旧harness段。管理员边界不变。旧表保留但不消费，兼容变化及部署步骤见[契约](harness-settings.md)。
- frontend_design/workspace.html:harnessPolicySnapshot/fetchHarnessPolicy严格检查结构、单位类型、只读/Runner与来源；移除保存/输入/开关，显示七项真实状态与来源、范围说明。独立读取/失败未知/重试、请求序号防迟到覆盖，读取中隐藏旧值。保留LLM/RAG/数据库其他行为。

固定计划/Runner/节点执行代码未变，LLM没有节点流转权限，没有跨节点调用或放宽SQL安全。未接通的数据库池设置仍待下一阶段单独核查。

## 实测验证

Windows Python3.13.9、Node24、Ruff0.17.1、mypy2.4.0；沿用原site-packages与type-deps/driver-types。环境PYTHONPATH指向已有依赖，LLM_MOCK_MODE=1（conftest）、RAG_EMBEDDING_PROVIDER=mock、LITELLM_LOCAL_MODEL_COST_MAP=True，DE_TRACE_DIR为忽略目录。

- 新增24项Python用例，真实临时配置/计划mtime_ns热重读、原生SQLite设置表保持不变、只读与两边行数最小值、1毫秒原生进度中断后可继续SELECT、来源缺失/坏YAML不泄漏、聚合与401/403。定向命令python -m pytest tests/test_api/test_harness_settings.py tests/test_harness/test_request_invariants.py tests/test_nodes/test_execute_sql_node.py -q：**45通过，22.91秒**，[日志](results/stage-23-targeted.txt)。
- python -m pytest -q：**3366通过、1跳过、4警告，223.94秒**，[日志](results/stage-23-pytest.txt)。Windows uvloop待Linux补验；测试运行并发其他检查，不用耗时判断性能。全仓Ruff通过，[日志](results/stage-23-ruff.txt)；既有CI同一mypy核心命令**35文件零错误**，[日志](results/stage-23-core.txt)。全仓mypy app --output json仍退出1，**186项/42文件**，相对188项新增0、移除2（两个Harness端点返回类型），[诊断](results/stage-23-after.jsonl)/[摘要](results/stage-23-summary.json)，不称全仓类型检查通过。
- npm run check:frontend：5段源码编译、原83+新**29实际函数契约，共112项**，[日志](results/stage-23-frontend.txt)。覆盖只读结构/缺字段/非有限值/零预算与重试、旧响应丢弃、失败未知/恢复/仅GET；附UI无输入/保存结构检查。npm run check:frontend:build三页46文件，本地资源/字体/许可证、SHA256、重复构建及编译失败保留通过，[日志](results/stage-23-build.txt)，Expected identifier为故意负例。
- gstack /browse **7项**：[结构化证据](results/stage-23-browser.json)，正常真实值（10次LLM、0重试、1000行、30000ms语句、120s请求）、真实409无副作用、删除临时agent.yml后503/未知、恢复重读、QA畸形200拒绝、实际Settings变更后7行/1700ms/9.5s/81s、375px不横向溢出且按钮在视口。临时FastAPI :8023关闭lifespan避免真实MySQL，临时SQLite旧表故意read_only=false/max_llm_calls=99而实际展示true/10；临时合成认证/QA路由/CSP只在忽略目录，未交付。外部资源0；[桌面](results/stage-23-desktop.png)/[手机](results/stage-23-mobile.png)均已查看，不称完整无障碍审计。[控制台](results/stage-23-console.txt)保留未认证前401、mock未就绪503、显式PUT409与配置缺失503，没有React运行异常。写拒绝为人工验证请求，不是页面自动写入。

## 调整与限制

新增SQLite用例初次漏填NodeInput.query_text，修正测试输入后定向45通过；Ruff行宽按格式修复，未关规则或弱化断言。初次页面patch锚点参数不匹配，核对实际签名后修改；初期rg路径误猜改为实际文件。中断使临时服务退出，浏览器自动重启Windows超时，改用gstack随包Node兼容服务；确认FastAPI就绪后重新导航/认证与完整验证，先前连接拒绝/无来源localStorage/PowerShell引用及-i参数失败不计产品结论。敏感认证脚本输出被隐藏，拆分为请求和不含凭据的结果读取才归档。

本阶段只修正状态/接口，没有统一配置源、跨进程广播或动态安全配置。真实MySQL/生产模型/性能/语义质量未测，现存186项类型债务与7项构建期依赖告警保留。原工作区pyproject.toml SHA256 E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3未变，docs/audits及隔离工作区旧未跟踪计划保留。

## Git与下一步

待范围/凭据审查后提交推送，核对精确SHA/Linux CI，再补记远程证据；不合并main。下一候选先核对数据库连接设置与实际连接池/方言能力，独立规划最小范围，避免无效保存。
