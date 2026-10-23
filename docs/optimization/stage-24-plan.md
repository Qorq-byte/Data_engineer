# 阶段24计划：数据库连接能力真实状态

2026-10-23；基线5761992，阶段23最终Actions39921359522成功。独立分支codex/database-capability-status；不合并main，不触碰原工作区pyproject.toml/docs/audits及隔离工作区旧未跟踪文件。使用brainstorming比较方案，planning-with-files沿用本目录计划/报告/进度，按用户授权先规划后实施。

## 依据与方案

app/api/settings.py数据库PUT仅存储dialects_enabled/connection_pool_size/connection_timeout_s，ConnectionFactory没有读取这些字段。workspace.html失败时仍回退七类方言启用和池5/超时10秒，混淆SQL方言与实际连接能力。connections.py实际四类连接，另七类NotImplementedError；SQLite/DuckDB原生连接，PostgreSQL单asyncpg连接，MySQL建池后获取并持有一个连接，不能称共享请求池调度。

采用只读真实能力报告，停止无效写入；仅隐藏页面而保留误导API不能闭环。接通全局池/超时/开关需明确新旧连接、重连、关闭及驱动差异，超出本阶段MVP收益。不会借此重构连接管理，也不把模块可发现当作驱动可用/连接健康。

## 顺序与验收

1. 写旧GET应报告真实能力/PUT应拒绝的失败回归并留证。
2. ConnectionFactory增加无连接副作用的能力快照：11类型、四类实现/七类未实现、实现类型驱动模块可发现性（失败为未知）、管理方式、按类型注册数；不返回主机/库名/ID/凭据，不探测网络/导入驱动/调用connect，不报告通用池与超时假值。保持实际分派不变，用全枚举拒绝/真实SQLite和DuckDB生命周期测试校对声明。
3. 专用GET返回mode=read_only、legacy_stored_settings_applied=false及能力视图；内部失败固定503未知。PUT无论旧/未知/空字段均409 DATABASE_SETTINGS_READ_ONLY且不读写旧表；聚合GET同一视图覆盖旧值。鉴权保持401/403。
4. 页面独立严格读取/错误/序号控制，移除旧默认方言与5/10值；显示实现状态、驱动发现、连接管理/注册数，明确注册不是健康、SQL方言支持不等于连接实现。只允许重读，不提供编辑/保存；旧响应不覆盖新状态，失败未知。
5. 真实临时SQLite/DuckDB、七类拒绝、存储不消费/不泄漏、发现异常/来源未知、聚合与权限回归；新增实际页面函数契约和gstack浏览器正常/故障/恢复/375px。运行pytest全量、Ruff、strict35及全仓mypy相对186项无新增、既有112项前端及新增、构建完整性/重复性/失败保留。
6. 更新API/部署/README/阶段报告及进度，记录命令/实测/未验证项，范围与凭据检查后普通提交推送分支；核对精确SHA/Linux CI，再补记远程证据文档并提交推送/核对CI。

验收：旧表不能伪装能力；未实现类型明确显示而非绿色启用；驱动缺失/未知不称连接可用；GET不连接数据库、PUT无副作用；真实注册/释放状态可核对。保持固定计划/Runner、节点隔离、SQL只读及现有执行行为。未测真实MySQL/PostgreSQL网络、生产多进程/压测或RAG语义指标，不虚构性能；保留186类型存量与7构建期依赖告警。

## 进度

已在编码前确定范围和验收。初期按名称猜测数据库路由文件不存在，已用rg --files核对；实际入口随后按路由定义定位，不据此判定功能缺失。下一步先失败回归。

步骤1–5完成：2失败→2通过，新增24项、定向30、全量3390通过/Windows uvloop1跳过（106.41秒）；新29/总141前端、构建、Ruff/strict35通过，全仓184项/42文件（基线186、新增0）；gstack9场景与375px通过，文档已整理，待步骤6提交/远程验收。调整和边界见stage-24.md。

步骤6代码fd7da2c已普通推送，Actions39923472173精确SHA成功；Linux3391通过（52.80秒，uvloop实际执行）、141前端/构建/Ruff/strict35通过。验收证据已补记，文档另行提交推送/核对CI，不合并main。
