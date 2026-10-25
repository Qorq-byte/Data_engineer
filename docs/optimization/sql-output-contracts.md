# 版本化SQL输出契约

阶段39新增 `synthetic-sqlite-model-v2`，只用于公开合成数据。旧v1问题、gold、底层共享fixture和评分保持；两个版本的题意、Schema、数据和评分条件不同，不能将分数差直接解释成模型能力提升。

## 固定题意与数据

固定4题要求：订单ID一列升序；completed订单的amount合计、不扣退款、空集返回0；用户ID及订单数两列、含零订单用户、按ID升序；category精确等于小写unknown的商品ID升序、排除NULL和其他类别。输出别名和列顺序均明确，题目声明排序的查询须有最外层ORDER BY，不能靠存储顺序或子查询偶然有序通过。

独立fixture位于tests/fixtures/model_eval_v2，三组数据共享同一Schema。base包含零订单用户、退款和非completed状态、没有unknown商品；edge增加同名用户、重复金额、多位零订单用户、两个unknown商品及NULL/unknownish/Unknown类别；empty没有业务行。手写expected_rows与gold独立保存，准备/评测先在三库核验12项gold结果、列名、顺序和标签一致性。错标签直接拒绝，不自动用gold结果改写标签。该自检证明内部一致，不能证明标注符合真实业务。

本地共享查询原语强制只读SQL策略、默认2秒/1000行，并释放cursor/进度回调。每组SQLite初始化和执行均在finally关闭范围；query_only在载入可信fixture后启用。只接收固定profile，不能通过数据文件路径选择任意数据库或初始化脚本。

## 离线检查

```powershell
python -m scripts.evaluate_sql_contracts --output data/v2-label-check.json
python -m scripts.evaluate_sql_contracts --predictions data/saved-v2-predictions.json --output data/v2-evaluation.json
```

无predictions时仍核验12项gold；四题预测均标记缺失，不能把标签自检当模型通过。预测可为明确id/sql/status/finish_reason的普通JSON列表，或既有候选审计导出。导出需核对数据集/案例/行摘要，不能跨版本借用；普通列表只代表操作者提供的SQL，不认证真实调用。

新评分要求明确stop，缺完成标记为UNCONFIRMED_COMPLETION；失败、缺预测、截断、refusal/tools或空SQL不执行预测SQL，保留4题分母。每条预测在三组数据本地执行，检查列名/列序、行值/行序和顶层排序；全三组匹配才计该题robust_match。报告保留12项检查及各错误码，不返回预测SQL或数据行。execution_success与contract_match分开；能执行不代表契约正确。费用actual_cost_usd始终null，不把预测附带报价当实际账单。

文件通过共用严格JSON读取（16MiB/64层、拒绝重复键/非有限值）、输入路径/硬链接保护、原子替换。输出不能覆盖标注、任何schema/seed或预测输入；错误保留旧报告，固定提示/退出2。成功写报告退出0，包括零匹配情形，不表示全部模型答案正确。

## 受控真实调用

```powershell
python -m scripts.run_model_evaluation --prepare --profile synthetic-sqlite-model-v2 --manifest data/v2-manifest.json --run-name v2-first
python -m scripts.run_model_evaluation --execute --manifest data/v2-manifest.json --confirm-manifest-sha256 <SHA256> --env-file D:/python/data_engineer/.env
```

默认profile仍为v1，v2必须显式选择。execute只接受清单绑定的profile；源文件/fixture/软件版本变化需重新prepare，目录须全新。清单包含4个明确问题、三个本地评测场景及来源哈希，不含gold/expected_rows或凭据。prepare和离线CLI不导入SDK；v2准备会做本地标签自检。

沿用阶段38的专用进程、业务环境清理、新session和base fixture唯一连接，正式Harness按固定计划执行。仅发送Schema及题意，不发送gold、手写结果或edge/empty行；同一条生成SQL在本地复用于三组数据，不重复生成。v2审计绑定后导出并评分，失败/取消/审计失败不伪造强绑定成功。

预算仍最多4次、每题1次，n=1，消息32KiB、输出参数1024token，请求30秒/题45秒/批次调用窗口180秒，无重试/fallback。12次本地检查不是12次模型调用；不把4次请求的token/费用复制三份。客户端上限不证明供应商账单，费用未知不补0。原始响应/SQL/trace/审计存忽略目录，公开阶段文档只保留清单和脱敏汇总。

范围不含业务质量结论、真实embedding、PG/MySQL、生产容量、账单校准。profile/摘要仍依赖可信本机代码和独立进程，不能作为签名、恶意代码沙箱或模型版本冻结证明。
