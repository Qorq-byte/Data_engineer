# 阶段32：离线预测导出与评测接线

2026-10-24，用户授权先规划后实施。独立codex/sql-prediction-export/.stage32，基线阶段31最终fb03cb0；承接未合并阶段29—31，不自动合并main，保留主工作区pyproject.toml/docs/audits及既有工作区。设计与验收见[先行计划](stage-32-plan.md)。

## 方案与实现

审计v1刻意不存SQL，当前CandidateResult也没有逐候选结束标记；选择显式本机文件导出，避免扩展在线敏感数据持久化、从审计猜SQL或把请求attempt的stop套给所选候选。本阶段不改变Harness计划/节点生命周期/预算/重试/模型选择和在线协议，不运行真实模型。

新增app/evaluation/predictions.py纯契约：按case/query/candidate/db显式绑定标准QueryResponse，成功只选一个SQLite候选，失败workflow导出failed/sql=null，缺绑定案例继续留在全案例分母；重复case/query、错误库/候选/方言、错问题绑定和非法usage拒绝。请求范围用量只归属一个case，包含重试/其他候选/目标库，不均摊或重复计数。费用/生成latency均null；候选明确结束信息才保留，缺失仍legacy_unknown，拒绝/工具参数仅转固定other。

输出仍是平铺预测列表，附request-prediction-v1、allowlist provenance及规范JSON行/案例集/输入哈希。可选只读audit-db核对outcome/预算/用量，缺失或冲突中止；metadata_match仅证明元数据一致，不能认证SQL或输入真实性。既有evaluate_quality保留旧列表格式，对新行核验版本/行/案例集和范围；完整evaluate额外核对dataset哈希。删除全部导出标记或重算哈希仍可人为改写，哈希不是签名。

新增scripts/export_predictions.py文件CLI：每输入16MiB、64层JSON、对象/案例列表10000、SQL按UTF-8上限1MiB，重复键、非有限数含浮点溢出、过深JSON拒绝。验证序列化后同目录临时文件原子替换输出，异常保留旧输出/清理临时文件，禁止覆盖输入和硬链接别名，固定脱敏错误/只打印条数与哈希。无SQL执行和SDK/MySQL导入；只有显式audit-db执行审计只读查询。

为复用既有allowlist，将app/audit/store.py私有_usage公开命名为usage_snapshot，函数行为不变，旧审计回归保留。没有新增审计SQL字段/迁移、采集模型请求、HTTP导出API、MCP候选推断或前端页面。用法与边界见[导出契约](prediction-export.md)，已同步评测/审计契约、README和总计划。

## 验证与调整

新增54项回归。实现前29失败/1旧格式兼容通过，[红灯日志](results/stage-32-before.txt)；失败以新模块/CLI缺失为主，不称为29项原有产品故障。首版30通过；扩展阶段450通过/2失败因新增测试使用copy但导入曾被早期lint删除，补回测试导入后通过。[中间日志](results/stage-32-targeted-initial.txt)保留。

最终导出54项通过（12.93秒），[导出日志](results/stage-32-export-final.txt)；最终包含旧评测/SQL边界/LLM/审计的定向 **456通过、2警告，17.13秒**，[日志](results/stage-32-targeted.txt)。覆盖多候选/多库精确选择、失败/缺失分母、候选结束拒绝与请求兄弟不串标记、未知/SDK来源、审计只读和不认证SQL的反例、内部一致性、篡改/案例集错配、输入保护/原子替换失败、硬链接、重复键/浮点溢出/UTF8字节/64层JSON、无SQL执行/无SDK副作用及真实Router+合成SDK/SQLite/审计→导出→只读评测闭环。

首轮全量3731通过/1平台跳过（135.75秒），随后加入字节/完整CLI/嵌套边界。嵌套测试发现Python JSON解码器实际接受2000层，不能靠解释器RecursionError定义输入上限：中间全量3733通过/1失败/1跳过，[证据](results/stage-32-pytest-depth-before.txt)。改为显式64层迭代器栈检查，额外内存只随深度增长；最终全量 **3734通过、1跳过、2警告，197.39秒**，[日志](results/stage-32-pytest.txt)，包含全部54项新增回归。

Ruff、核心strict36、LLM边界strict2、审计strict2、评测strict1和新增导出strict2均通过；全仓mypy仍178项，按file/code/message多重集新增0，[类型摘要](results/stage-32-types-summary.json)。前端182函数契约/三页46文件完整构建、重复性和故意坏JS保留旧产物通过，无页面改动不重复视觉检查。复用已验收Windows/Python3.12环境；异步测试需本机socketpair，沙箱外执行。前端使用锁文件离线缓存；无生产凭据或外部模型/数据库请求。

## 可重放功能样例

固定[responses](../../tests/fixtures/prediction_export_responses.json)和[bindings](../../tests/fixtures/prediction_export_bindings.json)配既有retrieval_eval.json，可按导出契约两条CLI重放。[预测文件](results/stage-32-predictions.json)含2条：1条显式选定候选、1条生成失败；[评测输出](results/stage-32-smoke.json)仍以全部4题为分母，结果匹配1、GENERATION_FAILED1、MISSING_PREDICTION2，完成状态legacy_unknown1/failed1/missing2，费用及生成latency均无样本/null。请求用量4/2 token只是合成fixture的已知部分，含缺usage兄弟，未重复计数。

输入/输出哈希与评测预测哈希相同，可重跑核对；FTS5词法烟测仍8文档/7查询/K3。上述计数是功能口径验证，不是模型正确率、费用或生产性能改善；0次模型调用不等于生成免费。样例SQL可随代码公开，真实导出SQL需操作者控制文件权限。

## 限制与交付

真实provider输出、逐候选完成来源、源库快照与SQL真实性、原始usage/账单、多方言业务质量和生产负载未验收；MCP精简投影不具备足够候选信息，不自动补全。原子替换不承诺断电/强杀后的磁盘持久性。费用估算和真实模型评测继续独立规划。

本地最终检查已通过，提交推送与精确SHA CI待补记；不强推、不合并main。
