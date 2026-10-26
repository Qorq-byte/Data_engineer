# 阶段 41：MySQL 大结果读取与取消资源边界

2026-10-26。基线 main `fecb7b9`，承接规划 `9069489`；独立分支 `codex/mysql-bounded-results`、工作区 `.stage41`。[先行计划](stage-41-plan.md)。本地功能验收已完成，远程精确 SHA 验收待执行；此时不登记整体阶段完成、不合并 main。

## 实现与选择

普通 SELECT 在 `cursor.execute()` 前增加服务端结果上限 `max_rows + 1`，继续使用普通游标。`app/db/mysql_query.py` 先通过共享只读策略按 MySQL 方言验证，再解析顶层查询：已有整数 LIMIT 保留较小值或仅替换较大值的原文字面量；没有顶层 LIMIT 时，在最后一个非分号 token 后插入上限，保留尾部注释与分号。OFFSET、内部子查询 LIMIT、CTE、UNION 和排序保持。结果仍保留一行截断证据，应用层返回 N 行，列、NULL/Decimal、空结果及全局 max_result_rows 语义不变。

不重新序列化整条 AST：MySQL 无别名表达式的列名包含原始表达式文本，改成规范空格会改变公开列名。真实 `id+1` 列名回归已覆盖。未选择只替换 SSCursor：已检查安装的 aiomysql0.3.2 源码，SSCursor.close 会调用 `_finish_unbuffered_query()` 排空剩余结果；服务器结果上限直接解决实际缓冲规模，保留已有生命周期。另用真实SSCursor对1000行结果只读取2行后关闭，实际继续读取999个协议包（998剩余行+EOF），并验证连接可复用；该控制探针直接使用SELECT-only账号，明确不代表应用的有界执行路径。

不支持顶层参数化/非整数字面量 LIMIT（本执行入口本来不接收绑定参数）、无法安全定位的 LIMIT；明确报错，不回退到原始无界 SQL。未解析/写入/锁/INTO OUTFILE/多语句及可执行注释仍由现有策略拒绝。共享策略只增加忽略 sqlglot 对结尾注释生成的独立 Semicolon 节点，仍要求恰好一个只读查询；新增注释后藏第二条语句的拒绝回归。

EXPLAIN 使用独立路径，不把结果 LIMIT 套在计划语句上；其计划行不属于普通 SELECT 的 N+1 证明。直接 EXPLAIN 的大小写/换行识别也进入 sql_notes 控制。初始 sql_notes=0/1 在配置 EXPLAIN 和直接 EXPLAIN 后精确恢复；恢复或游标关闭失败均丢弃连接并报错。普通 SELECT 不增加会话 SET。

## 失败证据与真实读取

[首轮](results/stage-41-initial-services.pytest.txt) **5失败、10通过**：四组原始查询结果为1000/10000行、N=1/100时，普通驱动实际均缓冲了1000/10000行，尽管响应仅返回N行；另有分号后合法注释被误拒绝。初轮两个容器均已核验移除。[结构化读取证据](results/stage-41-buffer-evidence.json)直接来自真实 Cursor._do_get_result 后 `_rows`（与 MySQLResult.rows 同一缓冲）长度及JUnit属性，不以响应长度或一次RSS差值代替。

修复后同四组实际缓冲分别是 **2、2、101、101**。临时表固定10000行小整数/短字符串，WHERE使原始结果规模分别为1000和10000；N固定时缓冲不随结果总规模扩大。截断后在同一连接继续查询成功。

[最终真实套件](results/stage-41-delivery-services.pytest.txt)、[服务/清理报告](results/stage-41-delivery-services.json)**82通过、0跳过（PostgreSQL19、MySQL63），12.24秒，3项既有进程列表警告，两个容器均移除**。覆盖0/N/N+1/远超N、较小/较大/零LIMIT、逗号/OFFSET、CTE/UNION/嵌套查询、排序、分号和合法注释、表达式列名、全局上限；保留原38项双库验收。

取消/超时使用原真实服务器锁等待和事件控制的实际游标fetch/close/restore边界，不以sleep制造取消窗口。预先登记：查询超时200ms、主动取消场景SQL截止5s，事件到达及取消/关闭收尾各3s，测试工具总截止300s；无运行后放宽。失败会话显式关闭，aclose后池closed且size=0、注册消失；重连后查询恢复。注入的close/restore错误必须execution_error且无结果，不能报成功。固定Harness/确定性Router下，JSON、SSE及真实FastMCP Client均验证普通/截断结果、候选SQL/身份/finish_reason、用量未知及取消后租约释放；模型/embedding调用为零。

中间的 [60项通过](results/stage-41-real-services.pytest.txt)、[75项通过](results/stage-41-verified-services.pytest.txt)、[79项通过](results/stage-41-accepted-services.pytest.txt)是逐次扩展的检查，不与最终计数混用。[中间6失败](results/stage-41-final-services.pytest.txt)来自新增测试误用候选字段sql/llm；按既有公开模型改为sql_text/model_response后通过，未修改响应结构。

## 完整兼容与环境

[离线全量](results/stage-41-pytest.txt) **4051通过、1 Windows平台跳过、79真实项明确排除、2既有SDK警告，155.12秒**。该轮之后仅补入2项真实EXPLAIN换行组合及对应识别修正，定向Ruff/strict复核通过，最终远程全量核验同一SHA。新增离线安全/语义回归15项；真实数据库另行运行，不能将明确排除项称为离线通过。

[Ruff](results/stage-41-ruff.txt)、[配置内14组strict门禁](results/stage-41-strict.txt)通过；新辅助函数也由显式strict复核。全仓mypy仍178项，[按文件/错误码/消息比较](results/stage-41-types-summary.json)新增0、消除0，不能表述为全仓类型通过。[前端](results/stage-41-frontend.txt)仍182项契约；[完整构建](results/stage-41-build.txt)验证3个页面、本地资源/字体/license、哈希、重复构建和编译失败保留旧产物。页面未改动，本阶段未新增浏览器UI验收。

专用Python3.12.13环境，aiomysql0.3.2/PyMySQL1.2.3/asyncpg0.32.0/sqlglot30.18.0；[依赖快照](results/stage-41-dependencies.txt)。首次新建Windows环境缺少tzdata导致PyArrow UTC转换失败，独立于应用可复现；[失败全量](results/stage-41-runtime-initial.txt)131失败/3920通过已保留。对照[原验收环境](results/stage-41-baseline-dependencies.txt)含tzdata2026.5，仅在测试环境补齐后全量通过，不改变仓库依赖声明，也未隐藏失败。默认Python无测试依赖、旧根.venv解释器失效，沙箱的TEMP/子进程问题已通过专用环境、工作区basetemp和受控提权执行解决。

本地镜像与限制复用既有所有权核验工具：PostgreSQL16.15/MySQL8.4.13、各1CPU、768MiB/1024MiB、512MiB tmpfs、环回随机端口、不挂载宿主数据。各轮容器完整ID、镜像ID及removed=true见对应服务报告。3项MySQL进程列表弃用警告未屏蔽。npm安装实测3项moderate构建期依赖告警，保留后续维护处理，不自动升级。

## 边界和后续

上限证明针对普通SELECT的返回/驱动缓冲行数，不是任意宽单元格的字节/RSS上限、服务器扫描/排序/JOIN中间结果上限或即时停止证明。EXPLAIN计划结果独立处理；网络分区、进程强杀、生产容量、多worker、业务准确率和其他数据库版本未据此验收。Harness固定YAML顺序、权限、预算、候选证据和未知费用保持。

下一阶段仅建议先规划42：冻结30条SQL/30条检索标注；本次不实施42–48。远程两工作流精确SHA核验后补记，不自动合并或发布。
