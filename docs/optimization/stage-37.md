# 阶段37：离线评测输入与报告边界

2026-10-25，基线阶段36最终3325dda；独立codex/evaluation-artifact-boundaries/.stage37。先行设计见[计划](stage-37-plan.md)，接口见[评测文件边界](evaluation-artifact-boundaries.md)。承接尚未合并main的阶段29—36，不改原工作区，不强推、不自动合并。

## 结果与边界

三个旧评测CLI接入共用strict JSON/路径保护/原子报告写入，修复输出可覆盖数据集/预测/底层SQL fixture（含别名/硬链接）及直接write_text截断旧报告的缺口。输入每文件16MiB/64层、重复键/非有限值拒绝，UTF-8 BOM兼容。错误保留旧报告和输入字节、清理临时文件，固定CLI提示/退出2不回显私密内容或traceback。

新增纯inputs.py预检结构、标识/集合重复、布尔、文本UTF-8大小、k/条数及Schema大小。quality结构/预测遥测/费用溢出/导出绑定在BM25前检查，来源哈希不匹配保留原优先拒绝。SQL验证初始化也进入finally关闭范围，失败时不漏连接。SQL验证增加可选--dataset，默认fixture及历史stage10固定SHA不变，历史代码读取后置于文件门禁。

非法/空/非字符串模型SQL仍计入原失败分母，含NUL的SQL保留SQL_POLICY_REJECTED，不变成整批数据错误；仅超限字符串拒绝。失效/跨库命中、缺表FK等仍保留给节点处理，预检不重写gold/expected标签。原指标/报告字段与哈希算法保持，纯函数evaluate仍抛ValueError；CLI stdout只给计数/哈希，详细内容改读报告。两个节点评测器有效决策不匹配仍写报告/退出1，quality成功产出为0不表示预测正确，历史回放仍按原规则0。

Harness节点顺序、权限、预算/重试、只读SQL、JSON/SSE/MCP和费用/审计功能未改。无真实LLM/embedding/外部业务数据库请求，不新增模型质量或账单结论。

## 回归与类型

新增55项。首轮[43项失败回归](results/stage-37-initial.txt)全失败，直接覆盖复制输入破坏风险/非法数据提前工作及初始化连接泄漏；实现后107项通过，再补12项退出码/上限/空索引/非法SQL分母/显式数据集/SQL fixture保护。一次[广关联](results/stage-37-target-initial.txt)666通过/1失败：新增结构检查覆盖了旧来源哈希错误的优先级，已恢复原拒绝顺序。最终[173项定向](results/stage-37-target.txt)通过，保留旧来源校验及失效模型样本口径。

本地[全量pytest](results/stage-37-pytest.txt) **3944通过、1平台跳过、2警告，190.39秒**。实际SQLite/真实节点验证三CLI默认合成集、输入/fixture覆盖保护、替换失败清理、BOM、固定错误和stdout摘要、有效决策不匹配退出1；初始化失败关闭连接。旧CLI错误测试明确从未捕获ValueError更新为SystemExit(2)，未屏蔽程序调用错误。

[Ruff](results/stage-37-ruff.txt)、[原核心strict36](results/stage-37-core-types.txt)、[原审计/估算/导出等加新预检strict13](results/stage-37-strict.txt)通过。两个旧节点评测脚本的原依赖有[13项存量错误](results/stage-37-script-baseline.txt)，来自阶段36四个依赖模块；新增[脚本入口strict2](results/stage-37-script-types.txt)使用--follow-imports=silent，仅检查入口，不把这些债务伪装成已修复。原strict门禁不改，全仓mypy相对阶段36仍178项，按file/code/message新增0，[摘要](results/stage-37-types-summary.json)。

[182前端函数契约](results/stage-37-frontend.txt)、[三页完整构建](results/stage-37-build.txt)、重复性/故意坏JS保留旧产物通过；无页面变更不重复视觉检查。复用Python3.12环境及锁文件离线npm缓存，Windows异步测试/CLI需沙箱外socketpair。

## 相同输入对照

用同一环境/PYTHONHASHSEED=0，对阶段36和本阶段运行三个CLI及stage10历史回放。[对照证据](results/stage-37-smoke.json)比较全部报告字段，排除实际运行耗时值、保留SQL执行样本计数；四份报告语义完全相同：quality为4案例/2预测/匹配1，Schema 12/12，SQL校验18/18，历史回放7/18。哈希算法和底层fixture字节不变，不以旧历史差异宣称本阶段提高准确率或性能。

另重跑[旧导出/费用样例](results/stage-37-compat.json)：阶段32两条预测内容和哈希3a3b12662c3d27ebddddee556c805504bba5ddf9adb6f52ae532763b7182d4f8不变；阶段36估算报告完全相等，report_sha256仍5670d9cc3ba4c7d962c79ecc3f37a0a4b7abfcf85b00d614981cf6b2633bc7f1。未知费用/完成状态不补零，候选绑定与平坦人工价格语义不变。

真实模型、业务标签、账单/复杂价格、签名审计及同集对比后续独立规划。浅克隆可能缺历史Git对象，不承诺自动补全或替换基线。

本地验收完成，代码推送及最终文档提交的精确SHA CI结果随后补记。
