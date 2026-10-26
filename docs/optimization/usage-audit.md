# 请求级用量持久审计

本阶段提供本机、可选、仅元数据的SQLite审计。现有MySQL查询历史和WorkflowTrace继续独立使用；本审计不保存问题、SQL、工具参数、原始异常或密钥，不是计费账本或防篡改日志。

## 启用与读取

默认LLM_AUDIT_PATH为空，不写文件，JSON/SSE done/MCP nl_query返回llm_audit.status=disabled。设置LLM_AUDIT_PATH=data/llm_usage.sqlite3并重启即可启用；相对路径以服务启动工作目录解析，推荐部署时使用私有本机目录的绝对路径。不要指向业务数据库、共享网络盘或公开静态目录。本阶段不会修改用户.env或自动开启现有部署。

LLM_AUDIT_MAX_RECORDS默认1000、合法1–10000。每次成功写事务同时按插入顺序保留最新N条。重复写同一快照不刷新保留顺序；不同内容的同query_id拒绝覆盖。保留约束是逻辑条数，不是按天过期；无后台清理、自动VACUUM或固定文件大小承诺，SQLite文件可能保留高水位空间。单条JSON上限1MiB，attempt最多1000，超过则审计失败，不影响查询结果。

响应的llm_audit包含status和query_id；status仅有persisted/disabled/failed。persisted表示本次SQLite事务已提交；failed附固定warning，不暴露路径或底层异常，不改写已完成工作流的状态。不要把内存llm_usage或HTTP200等同落盘成功。

认证管理员可调用GET /api/v1/usage-audits/{query_id}读取记录。匿名401、非管理员403、非法ID422、关闭503/USAGE_AUDIT_DISABLED、读失败503/USAGE_AUDIT_UNAVAILABLE、未找到或已被保留策略淘汰404/USAGE_AUDIT_NOT_FOUND。GET不创建文件/目录或初始化库，不提供写入API。MVP仍是私有共享工作台，不提供按用户/租户隔离。没有列表/搜索接口；需保留查询返回或SSE workflow_start中的query_id，未获得ID的中断查询仅可由本机运维检查审计库。

## 版本化记录

schema_version=1；query_id沿用请求ID；session_ref是原session_id UTF-8的SHA256，仅为伪名，可关联且低熵ID可能被猜测，不宣称匿名化。started_at/finished_at为UTC ISO时间，finished_at在尝试写入前生成，不是提交完成时间。

outcome是completed/failed/cancelled/error：completed和failed来自工作流业务结果；error为未交付正常结果的基础设施异常；取消/HTTP截止在工作流未完成时记cancelled，不能推断远端provider是否已经计费。请求完成后在写审计期间被取消，已存completed表示工作流已完成，不表示客户端成功收到响应。本阶段不记录传输交付确认。

llm_calls保持预算预留次数；usage保存第29/30阶段的逐次尝试、已知token部分和、覆盖率、缺测与usage_basis。保存采用allowlist，聚合值按保留attempt重算，不读取或复制完整查询响应。provider/model仅接受限定长度/字符的标识，含地址结构或非法文本改为redacted，因此这些标识可能不同于内存摘要；计数和完成状态保持原语义。未测token为null，合法0保留；sdk_normalized不能证明SDK补零前的原始provider字段存在。complete仍不是计费覆盖率。

## 生命周期与故障边界

取得查询admission和session gate后创建审计状态；容量拒绝、等待会话时取消不生成记录，也不伪造provider调用。成功、业务失败、异常和取消退出时都尝试保存。原生LLM/DB资源沿原Runner路径先完成释放，再写最终快照；审计I/O移到线程并在取消（含重复取消）时等待关闭，不遗留后台写任务。

SQLite锁等待设置0.2秒，写入与保留删除同一事务，失败回滚，旧记录不被部分删除。线程池排队和操作系统/文件系统挂起不受该锁等待上限保障，取消可能延迟；不要据此承诺严格总截止时间。进程崩溃/强制终止、磁盘满、文件丢失、文件系统故障不能保证审计成功；无持久化重试队列或崩溃恢复账本。读写每次新建并关闭连接，未知数据库版本/已有非审计表拒绝修改，空version0库允许初始化。

本机真实SQLite、线程并发、新进程读取和故障注入有回归；多机共享、多进程并发写、生产负载与磁盘故障实测没有验收。目录权限、备份、容量监控和清理属部署运维责任。费用估算、预测导出、远端消耗核对继续后续独立规划。


阶段32提供可选只读审计核对的[离线预测导出](prediction-export.md)；必须另外提供保存的QueryResponse SQL和显式案例绑定，metadata_match不能认证SQL。审计schema及默认关闭策略未改。

阶段35新增可选LLM_AUDIT_CANDIDATE_DIGESTS=false，开启后记录payload v2的候选摘要列表；表结构及SQLite user_version仍1，新旧payload可共存，关闭保持v1。摘要可被枚举猜测，不是加密或签名，不存SQL/库名原文。正常返回的最终候选才绑定；取消/error为空列表不代表未生成SQL。摘要失败只报告审计failed，取消及资源收尾政策不变。新版导出核对及旧二进制回退边界见[候选摘要绑定](candidate-audit-binding.md)。

阶段36提供独立的[离线请求用量估算](usage-cost-estimates.md)，只读显式query_id和人工平坦价格表，不改变审计schema或写入费用。sdk_normalized数值仍只是SDK观察值，缺记录/矛盾摘要拒绝，缺token/价格总额保持未知；不是实际账单。
