# 阶段37计划：离线评测输入与报告边界

2026-10-25，用户授权规划后实施。从阶段36最终3325dda建立codex/evaluation-artifact-boundaries/.stage37，不改原工作区，不强推、不合并main。使用brainstorming和planning-with-files，沿项目阶段设计约定。

## 缺口与设计

对比新增对比报告、执行真实模型、先补原有评测入口：检查发现evaluate_quality/schema_linking/sql_validation仍直接read_text/json.loads及write_text。输出可覆盖输入，重复JSON键被吞、过大/过深输入不受统一控制，写入失败可能截断旧报告。优先第三条，复用阶段36已有strict JSON和原子文件工具，不增模型或线上接口。

三个CLI统一16MiB/64层JSON限制、重复键/非有限值拒绝、UTF-8 BOM兼容、输出与输入（含硬链接/别名）隔离、先全验证后同目录原子替换；失败清理临时文件并保留旧报告。sql_validation增加可选--dataset，缺省仍原fixture。历史--baseline-stage10行为/固定SHA保持，文件门禁先于git show/模块替换。输入/IO/可分类评测错误固定脱敏消息和退出码2，正常决策不匹配仍保留原退出码1，历史回放仍按原规则0。

新增纯app/evaluation/inputs.py做结构与资源预检：各案例/文档/查询/预测最多10000，query k整数1–1000，必填文本/布尔/列表、重复标识/集合重复项及有限JSON检查。文本每条最多1MiB UTF-8，身份/域等短文本受限；Schema最多1000表、列/引用/命中列表受限，总列/引用最多10000。空检索文档或空Schema允许，检索queries和节点cases必须非空；纯检索不强制SQL案例存在，有预测时保持SQL案例非空原规则。

不重写gold标签或强制相关文档/引用/命中必须属于当前Schema：失效命中、跨库、缺表FK等是既有边界fixture要测试的内容。只校验数据形状，不冒充业务标注正确性。预测非字符串/空SQL仍按原EMPTY_SQL计入分母，明确failed/非stop仍原错误码；仅超限字符串拒绝。原v1/v2预测哈希验证和遥测合法性在BM25工作前完成，gold实际执行错误仍由原评测器判数据集错误。

纯函数evaluate入口亦预检，三种报告文件字段/哈希算法/评测口径不变。CLI stdout改为counts/source/report hash摘要，详细SQL错误/命中内容仅写到操作者报告，不回显到错误正文。旧程序调用仍抛ValueError；旧CLI未捕获ValueError退出1/traceback明确改为固定退出2，相应测试更新，不屏蔽实现错误。

SQL验证fixture连接初始化/建Schema也纳入try/finally，避免初始化失败漏关闭。Harness节点顺序、权限、只读、模型预算/重试及费用估算不变；无真实LLM/embedding/外部数据库调用。

## 实施与验收

1. 失败回归：三个CLI输入覆盖/硬链接/替换失败、坏JSON/大小/深度/未知结构、重复IDs/布尔/上限、BOM正常读；预检前不启动BM25/节点/gold库或baseline载入；旧报告/源码/输入字节保持；连接初始化异常关闭。
2. 实现共用预检与CLI边界；真实SQLite/真实节点复跑默认合成集，保持指标/报告内容及原预测v1/v2/估算兼容。stdout不含详细私有错误/SQL；历史基线本地回放单独验证，不承诺浅克隆自动补历史对象。
3. 定向/全量pytest、Ruff、原strict36+12及新增预检strict；两个节点评测脚本依赖13项既有错误，新增--follow-imports=silent脚本strict入口，依赖仍由全仓178项无新增核对。182前端契约和完整构建。
4. 更新README/评测契约/总计划/进度/报告及原始证据，普通提交推送并核验代码精确SHA CI；补记验收文档再核验最终CI，不自动合并main。

自检：不新增同集对比、线上模型采集或价格功能；仅解决已确认的文件/输入/资源边界。统计通过不代表真实模型质量或生产性能。

补充：底层SQL fixture亦作为输入路径保护；预测SQL只限制体积，含NUL的非法SQL保留原失败分母，不把模型错误升格为整批数据错误。两个节点CLI决策不匹配仍写报告/退出1，quality产出报告为0，不代表质量通过。
