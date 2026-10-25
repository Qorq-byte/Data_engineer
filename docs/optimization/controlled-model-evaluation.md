# 受控合成集模型验收

第38阶段入口只支持仓库的4题 `retrieval_eval.json` SQL案例和 `test_schema.sql`，不支持业务数据路径。真实供应商调用与离线合成传输测试分别记录，合成小集结果不代表业务准确率；不初始化真实embedding或已有RAG索引。

从仓库根目录使用可用的Python3.12环境：

```powershell
python -m scripts.run_model_evaluation --prepare --manifest data/stage38-manifest.json --run-name synthetic-first
```

此命令不导入SDK或应用settings，不联网、不读取凭据。输出清单SHA，文件包含4个问题、模型、公开endpoint、凭据环境变量名、代码/配置/fixture摘要和固定资源上限，不含gold SQL或凭据值。准备和执行间来源变化需重新准备，清单不是签名或身份认证。

检查清单后，用输出的完整SHA显式执行：

```powershell
python -m scripts.run_model_evaluation --execute --manifest data/stage38-manifest.json --confirm-manifest-sha256 <SHA256> --env-file D:/python/data_engineer/.env
```

仅读取 `DEEPSEEK_API_KEY`，支持进程环境或指定env文件；不自动寻找env文件。显式mock开关会拒绝执行，不因失败降级Mock。默认 `deepseek-v4-flash` 是仓库配置中的模型名称，可在准备时用 `--model` 改名；该供应商别名不保证版本冻结。只允许公开 `https://api.deepseek.com/v1`，不接受代理或其他账号路由。`--execute`须在专用新CLI进程运行，不能在已启动应用的进程内调用main。

每批最多4次物理尝试、每题1次、n=1、JSON消息32KiB、输出请求参数1024token、SDK请求30秒、每题45秒、整批调用窗口180秒，retry=0、fallback为空。原Harness预算和额外请求/批次预算先全部检查，再一次预留，不双计。实际调用前检查指定provider实例、模型、消息大小及输出限制；额外参数/流式调用拒绝，不能改endpoint或key。客户端限制不能证明供应商实际输出或账单上限，取消也不能证明服务端未计费；真实费用始终null。

执行前验证清单与本地来源一致，独占新建 `data/model-evaluations/<run-name>`。目录已存在即拒绝，没有自动续跑或补调用；失败后也保留，不删除目录重新执行。需另行准备新名称，并重新评审调用范围。运行时清空业务环境变量，在新workspace内加载settings，仅连接新建fixture SQLite，不调用应用启动/数据库恢复/RAG初始化。各题用新session，无领域或历史上下文，gold只进入独立只读评测器，不进入prompt。在线请求 `execute=false`，本地评测仍按安全SQL策略执行预测和gold。

私有目录保存manifest、responses、bindings、v2审计数据库、trace、强绑定predictions及report。`report.cases`保留completed/failed/error/timeout/cancelled/unattempted/audit_failed，不将跳过案例移出4题分母。审计失败的案例不生成虚假强绑定预测；取消时报告保留完整案例清单，evaluation可能未运行而为null。`physical_calls_reserved`是客户端尝试预留数，不是供应商账单次数。正常结束 `finished` 表示验收工具产出报告，不表示全部SQL正确。CLI准备/正常完成退出0，执行前失败或中断退出2；stdout不输出SQL/供应商原始异常。

供应商异常在传输边界转为固定错误，SDK输出丢弃，数据库与SDK客户端在finally关闭。全局进程退出/掉电仍可能留下running检查点，不能当作完成；没有外部进程硬杀或服务端取消保证。结果目录已被gitignore忽略，不提交原始模型响应、trace或凭据；阶段文档只公开模型名、清单SHA和脱敏汇总。

此阶段未完成：业务标注集、真实embedding/语义检索、PG/MySQL集成、生产容量、复杂价格/真实账单校准、模型版本冻结、多进程强隔离及签名审计。该CLI的隔离依赖独立受控进程和本机可信代码，清单不是对恶意操作者的沙箱。

公开阶段清单是该次运行记录，不是可跨平台直接重放的授权：Python/SDK版本或源文件字节（含换行格式）变化都会要求重新prepare。模型别名内容仍可能变化，不能只靠清单保证供应商端可复现。

阶段39增加显式 `--profile synthetic-sqlite-model-v2`，使用独立题意/Schema/三组本地数据和输出契约；默认仍为本页v1。v2同样只生成4次，不将三组本地检查计为12次模型调用。准备会先核验独立手写标签，详情见[SQL输出契约](sql-output-contracts.md)。
