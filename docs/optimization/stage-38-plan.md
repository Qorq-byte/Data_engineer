# 阶段38计划：受控真实模型验收入口

2026-10-25，用户要求规划并实施，明确选择仓库合成SQLite标注小集。从阶段37最终1d79864建立codex/controlled-model-evaluation/.stage38。使用brainstorming和planning-with-files，不改原工作区、不合并main，不将合成数据结果宣称业务准确率。

## 方案与范围

比较直接在线跑应用、手工复制SQL、受控批次入口：采用第三条，复用正式run_query/Harness与审计/导出/SQL评测。只支持仓库内4题合成集和固定SQLite fixture，业务集/真实embedding尚不纳入本阶段。默认prepare不调用SDK；实际execute必须显式确认准备清单SHA，且代码/数据/fixture摘要一致、全新专用运行目录。失败目录不自动续跑，重新执行需新清单，不自动补调用。

清单包含profile、provider/model/base_url/API key环境变量名、4个问题及ID、固定数据/代码/配置哈希；不含凭据值。默认上限：总4次、每题1次、n=1、每次消息JSON最多32KiB、max_output_tokens=1024、请求30秒、单题45秒、批次180秒；重试0/fallback空。客户端参数不能证明供应商实际遵守输出上限或实际账单，费用保持未知，不引用价格或承诺金额。

原reserve_llm_call新增可选调用方预算组，先检查所有预算再统一预留、同对象不双计；默认空时线上行为不变。原生调用前新增可选context guard，只允许本批次指定ProviderConfig实例、模型、n、输出上限及消息体积，阻断其他默认路由/Mock/超限请求，检查发生于预留和SDK前。无包装complete导致重复计数。

live仅单独CLI进程运行，初始化隔离工作目录、空mock/关键词RAG、无领域/历史上下文、独立SQLite/audit v2/trace；不读取现有业务连接/索引/会话，不把root.env整体载入应用，只取明确选定的API key，拒绝mock opt-out和无凭据。仅受控Router私有传输层将供应商异常转为固定错误，保留原用量观察和取消传播；标准预算/Runner仍负责真实工作流顺序与权限。

每题num_candidates=1/execute=false/run_validation=true，Schema来自公共fixture，gold不进入prompt；评测器另在只读内存SQLite执行gold/预测。记录responses/bindings及审计、绑定导出和SQL评测，全部案例保留分母。失败/取消/未运行分开，未运行不伪造调用或成功。审计失败则不伪造强绑定结果，异常及取消保留可读状态、先释放连接/供应商资源再收尾，不重试。

运行产物仅存忽略的data/model-evaluations/<run-name>，绝不上传真实响应/原始trace/密钥；公开只提交工具、合成测试、准备清单和脱敏统计。stdout固定摘要、错误不含供应商原文。实际调用前展示具体资源清单，仅按本轮合成集范围执行；若需扩展调用数/数据/预算，不自动扩大。

## 实施与验收

1. 失败回归：默认无SDK准备、清单/来源/旧目录拒绝、预算原子预留/上下文隔离/不双计、任意路由/n/输入/输出越界在SDK前拦截。
2. 实现纯清单、运行器/CLI、调用预算与guard；真实SDK合成传输走4题正式Harness→审计绑定→SQL指标，核验gold不进prompt、失败分母、取消/关闭、异常脱敏和无业务写入。
3. 形成真实DeepSeek调用准备清单并展示上限；在确认的4题范围执行一次，不将离线合成传输冒充真实模型；凭据/网络/额度等不足时如实报告，不降级Mock。费用不可知保持null。
4. 定向/全量pytest、Ruff、原strict与新增清单strict/隔离运行器入口检查，全仓178债务无新增；182前端/完整构建。固定导出/估算兼容复核。
5. 文档及证据、普通推送，精确代码SHA CI，补记验收后最终CI；不强推、不合并main。真实业务集、生产容量、账单校准和模型版本冻结仍需后续验收。
