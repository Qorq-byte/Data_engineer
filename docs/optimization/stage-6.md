# 阶段6：全仓 Ruff 清零与 CI 门禁

日期：2026-10-21。先完成阶段5提交3d638d55dc7b6ebdd5e8dfbd67974c604c1aff79及其[Actions验收](https://github.com/Qorq-byte/Data_engineer/actions/runs/39525221687)（Python3.12，2943 passed，30.61秒），再依照[六步计划](stage-6-plan.md)开始本阶段代码修改。原主工作区未动。

## 完成内容与边界

初始本机Ruff0.12.0发现100项/20文件：61个长行，其余为导入、格式、简化及类型名称问题。先安全自动修复，再手工审查剩余分支/上下文/注解；没有使用unsafe-fixes、增加noqa、扩大ignore或删除测试。

- 20个相关文件按既有100字符规则整理导入/格式；`app/auth/database.py` 在 TYPE_CHECKING 引入 aiomysql，删除原有三个过时 name-defined 屏蔽。
- `domains.py/settings.py` 的八处 try/except Exception/pass 等价替换为 suppress(Exception)，没有扩大异常范围或改变降级策略。持久化失败仍可能降级，这一历史限制并未在本阶段消除。
- `mysql_store.py` 合并三个嵌套异步上下文，退出顺序保持；去掉不必要的前向注解引号。Schema链接、设置更新、学习判断合并等价条件；移除PII检测中未使用的类型大写计算。
- `prompt_builder.py` 的长提示词改为相邻字面量，修改前后完整模块AST与字符串常量相同。DDL及SQL拼接文本保留；mysql_store模块常量集合仅少两个前向注解字符串。
- `scripts/gen_api.py` 是历史脚手架，运行会覆盖当前API，未在验证中执行。格式化其四份内嵌Python模板，生成源码空白变化，逐份AST完全相同。增加用途说明，不把旧桩代码当作当前接口。
- `.github/workflows/checks.yml` 改为 `ruff check .`，取代上一阶段指定三个文件的检查。`pyproject.toml` 将开发Ruff锁定为远程实际使用的0.17.1，保证同一检查器，不降级CI。既有Ruff select/行宽/目标Python、mypy strict及pytest范围均未放宽。固定计划、Runner、节点权限/预算和SQL约束没有调整。

## 验证与证据

Windows/Python3.13.9；既有site-packages，mock LLM/embedding，真实本地SQLite/DuckDB/LanceDB/FTS5。Ruff0.17.1仅安装在忽略目录，明确调用其bin/ruff.exe，避免python -m包装器误用全局0.12.0。

- `ruff check .`：0.17.1与0.12.0两版均 **All checks passed**。基线 [JSON](results/stage-6-ruff-before.json)，最终 [当前版本](results/stage-6-ruff.txt) / [旧版](results/stage-6-ruff-legacy.txt)。0.12基线100到0是同版本对照；不混算不同版本的发现数量。
- `python -m pytest -q`：**2943 passed，4 warnings，58.79秒**，[完整记录](results/stage-6-pytest.txt)。覆盖现有业务、安全、会话隔离、SQL执行和检索回归；没有为了纯格式改动添加镜像实现的测试。
- `npm run check:frontend`：三页五段脚本全部通过，[记录](results/stage-6-frontend.txt)。前端行为未变，本阶段不重复浏览器负载实验。
- [AST/常量比较](results/stage-6-equivalence.json)：6/20个模块完整AST相同；其余模块的导入/条件/上下文/注解简化按diff逐项审查，不能仅凭AST比较证明所有运行路径等价。四份模板各自AST哈希一致，不执行脚手架。PromptBuilder完整AST相同。
- `git diff --check` 通过；原工作区pyproject.toml SHA256仍为E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3，用户原有改动受保护。

辅助脚本第一次匹配了非目标try块，已撤回两个文件，再只转换八个准确诊断位置；语法检查和全量回归始终保留，错误中间态未提交。格式化模板的范围调整及原因已记入先行计划。

## 已知限制与下一步

本阶段只清理静态lint并建立全仓门禁，不宣称模型质量或运行性能提升。严格mypy上一阶段仍279项（66文件），本阶段未重新测量全部类型错误，**不宣称当前数量不变或已通过**。按模块先补Harness/查询契约与连接资源的类型边界，再处理第三方类型stub和API返回结构，适合后续独立阶段。

真实LLM/embedding/PG/MySQL、人工标注业务评测、生产容量、多租户/跨进程状态等仍按[上一阶段限制](stage-5.md)保留。当前工作为单独阶段提交并普通推送，不合并main；GitHub Actions最终结果以分支实际运行记录为准。
