# 阶段44：30题受限NL2SQL验收

2026-10-28，基线d07a6fc，独立codex/nl2sql-acceptance/.stage44。用户授权先规划再实施；真实付费模型执行等待本批具体清单的明确授权。当前不把合成SDK控制称为真实模型质量。

## 实施与边界

新增独立synthetic-nl2sql-v3 profile、run_nl2sql_acceptance CLI和监督worker，保留旧v1/v2的4次上限。复用冻结30题、20/10分割、三组SQLite结果契约，n=1、每题至多1次、总30次、每次1024输出token参数，SDK/Router均无重试、无fallback。模型别名deepseek-v4-flash、固定DeepSeek公共endpoint；模型别名不等于供应商不可变版本。费用保持null，无硬美元配额。

prepare仅本地读文件/依赖版本并生成清单，不导入SDK或初始化客户端。清单绑定全部app/scripts Python/YAML、pyproject、冻结题集、30份实际提示词摘要与依赖版本。发送前核对精确消息身份和字节限制，写入私有提示词及持久请求账本后才进入原生SDK；写入失败或清单策略失败停止批次，不补发未知请求。父进程监督worker1500秒另5秒清理，独占新运行目录；64MiB只在阶段后检测，不是硬文件系统配额或供应商资源限制。终止客户端不保证服务端撤销计费。

使用正式request_query的parse→link→gen→validate→exec固定YAML与Runner，execute=false；通过内部可选参数注入生产SchemaRetriever无RAG的keyword路径，线上默认配置保持既有行为，未新增对外API参数。无embedding请求，不称为阶段43 BM25排名对照。节点keyword fallback如实保留，原始实际节点/检索/校验事件与trace只存私有目录。

生产提示词最初携带SQLite绝对路径导致摘要漂移，预检主动阻止模型调用。评测改用隔离workspace下固定相对fixture.sqlite3，冻结30份提示词共83923UTF8字节、单份最大3083字节；不发送主机绝对路径。Schema估计行数属于实际元数据，不输入fixture数据行、gold SQL、expected_rows或反例。已保持线上PII列掩码和提示词模板，没有为成绩更改生成规则。

原生预算、每题预算、批次预算共同约束；候选以v2审计摘要绑定导出，再将同一SQL在base/edge/empty各检查一次。30题/90检查始终完整，生成失败、拒绝/工具响应、截断、网络、超时、校验/SQL策略/语法/结果错误和缺预测保留分母。90个本地检查不算90次生成或三份费用。报告生成/校验/robust正确率、开发/保留和标签分层、P50/P95/P99、SDK用量覆盖；部分或未知用量不补0，已知子和与完整总量分别表达。原始响应/SQL/审计/trace不上传。真实批次只调用三协议共享的run_query服务；JSON/SSE/MCP公共语义由既有离线回归核验，不宣称为三套真实模型批次。

## 离线验证

先行缺模块失败见results/stage-44-initial.txt。原生合成SDK控制发现生成指标误读generation_source为llm，已按实际model_response修正；提示词路径漂移曾在任何SDK调用前被拒绝，保留初始证据。最终28项针对性控制通过：真实30题Harness、90项gold结果自检、错SQL/截断/拒绝/网络/缺用量、审计绑定、精确提示词、取消关闭连接、请求账本写入失败立即停止、新目录/进程所有权/父进程超时、无SDK准备；另在全新子进程验证同清单实际提示词全部匹配。

全量离线4226通过、1平台跳过，真实双库82通过（PG19/MySQL63）且两个自有容器均清理。前端182项与重复构建/失败构建保留通过，Ruff与新增3文件strict通过；17项配置strict全部通过；全仓类型178项，按文件/错误码/消息比较新增0、消除0。测试SDK的30/30仅为工具正控制，不代表真实模型分数。

## 待执行清单

[具体清单](results/stage-44-execution-manifest.json) SHA256：`bdf95e0f754d2faf673154c4cf6387a9dbea031306a8a555e460959ee47106f2`。最多30次真实调用，每题1次/n1/无重试或fallback；83,923UTF8字节冻结消息，输出参数合计上限30,720token。单请求30秒、单题45秒、worker1500秒另5秒清理。输入token未假装精确已知，实际费用未知且没有硬美元限额。按照路线另请求本批执行授权，未收到授权不调用。

尚未合并main；阶段45及后续本次不实施。

## 远端验收与当前完成边界

代码提交 `9eae3ea28556f9c43c662a4494bf9e6765869dce` 的[常规CI](https://github.com/Qorq-byte/Data_engineer/actions/runs/40668363089)和[真实双库CI](https://github.com/Qorq-byte/Data_engineer/actions/runs/40668363123)均成功，headSha精确一致。Linux离线4227项、真实双库82项；17项strict、Ruff、前端182契约/构建通过。见[代码CI证据](results/stage-44-code-ci.json)。本次文档补记也另核验精确SHA双CI。

已交付阶段44的计划、离线工具、提示词/执行清单和门禁，真实30题付费批次尚未执行。本阶段不能标为真实质量验收完成，也不推进阶段45。用户确认清单bdf95e0f后才能执行一次；旧4题和阶段43的授权均不外推到此批。原始响应、候选SQL、审计、trace仍只在成功执行后的私有运行目录保留，公开只有摘要。
