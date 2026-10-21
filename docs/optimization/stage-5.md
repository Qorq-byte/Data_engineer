# 阶段5：可信工作台、基础可访问性与交付入口

本阶段按 [阶段开始前制定的六步计划](stage-5-plan.md) 执行，保持既有 React 单页和 Harness 架构。范围是可验证的 MVP 修复，不整体重做 UI。

## 已完成与代码依据

- `frontend_design/workspace.html`：公共 `api._fetch` 自动附加认证头，支持对象/字符串/422 detail，清理取消监听。真实浏览器确认此前数据库与模型 GET 未带 JWT，返回401；修复后返回200。查询 SSE 错误不补发 JSON。
- `ChatPanel/runPipeline`：移除380ms假进度和残余演示 SQL，处理 `workflow_start`、五个真实 stage；序号与 AbortController 隔离取消、历史切换、新对话和组件卸载后的旧响应。取消同时传入领域检测。130秒等待上限配合默认120秒后端截止，不做自动重试。
- 执行、未执行、0行、失败与 MOCK 区分；未连接数据库的 SQL 不能显示为数据库答案。`SchemaPanel` 删除网络失败时注入的假连接、六张表和估算行数；失败保留真实错误。在线与生产就绪分开显示。
- `ModalForm/ModalField`：dialog/标题关联、输入 label、初始/恢复焦点、Tab循环/Escape关闭；回调变化不重复抢焦点。查询输入、发送、历史、错误/空状态有基本语义。移动端修复 flex 最小高度、动态视口和输入最小宽度，发送按钮保持在视口内。
- `frontend_design/form.html`：邀请码输入并发送 `X-Registration-Token`；错误 alert、输入名称、翻转背面 inert，隐藏表单不进入键盘路径。
- `app/workflow/query_service.py:on_node_event`：只增加 `skipped` 事件字段，防止把禁用节点的 completed 误当已执行。新增真实 API 回归。计划顺序、Runner生命周期与节点独立性保持。
- `package.json/package-lock.json` 与 `_check_jsx.js`：锁定浏览器相同 Babel 7.29.0，`npm ci` + `npm run check:frontend` 可重跑全部三页内联脚本，去除临时目录依赖和重复一次性检查器。499个误跟踪 node_modules 文件从 Git 索引移除，添加忽略规则；原主工作区未改。
- `pyproject.toml` 明确 Hatch 的 app 包目标，标准 wheel 构建成功。`.github/workflows/checks.yml` 新增 Python3.12/Node22 安装、前端编译、交付Python文件 Ruff、全量pytest。没有删除旧检查或降低规则；全仓静态债务仍明确未通过。

## 验证命令、环境与实测结果

本机 Windows / Python3.13.9 / Node24.16.0；原 `.venv` 解释器不可用，通过 Anaconda Python 和已有 site-packages 运行。测试设 `LITELLM_LOCAL_MODEL_COST_MAP=True`、`RAG_EMBEDDING_PROVIDER=mock`、忽略目录的 `DE_TRACE_DIR`；conftest 强制 mock LLM 和测试JWT。全量日志 [stage-5-pytest.txt](results/stage-5-pytest.txt)：`python -m pytest -q` **2943 passed，4 warnings，74.29秒**。最终查询契约回归 **8 passed，13.65秒**，见 [定向日志](results/stage-5-target.txt)。

`npm install --ignore-scripts` 安装锁定依赖，无审计漏洞；`npm run check:frontend` 三页共五段脚本通过，见 [编译日志](results/stage-5-frontend.txt)。`pip wheel --no-deps --no-build-isolation --no-cache-dir . --wheel-dir .verify_tmp_scripts/wheels` 成功。Hatch在忽略目录单独安装，不改变主项目环境。wheel包含后端包；网页登录仍按仓库源码部署，前端静态文件和main入口不是独立wheel安装的完整产品包。

交付Python文件 Ruff 通过；**全仓 Ruff仍100项、严格 mypy仍279项（66文件，检查148源文件）**，见 [Ruff全仓](results/stage-5-ruff-full.txt)、[mypy](results/stage-5-mypy.txt)。没有屏蔽规则、删除失败测试或宣称全仓静态检查已通过。CI当前只对明确列出的交付文件 lint；全仓类型债务清理尚未纳入阻断门槛，需另行按模块修复。

### 浏览器复现

1. 从仓库启动 `python -m scripts.browser_qa_server`。只监听127.0.0.1:8018，内存SQLite三行，随机临时JWT和脚本模型；不会写生产数据库，不需要认证MySQL。测试令牌文件只在已忽略目录中。
2. 在仓库另一个PowerShell执行 `./scripts/browser_qa.ps1 -BrowseExe <已安装的gstack浏览器路径>`。先完成 `/browse` 自身安装；Windows下服务偶发启动超时可重启此只读启动步骤，不据此跳过业务断言。
3. [浏览器记录](results/stage-5-browser.txt) 与 [桌面](results/stage-5-desktop.png)、[手机](results/stage-5-mobile.png) 截图。浏览器全部使用gstack `/browse`，截图已人工视觉复核。

实际通过：正常3行、空结果0行、危险SQL失败、延迟生成显示真实进行中、取消后新对话无旧结果、四次操作恰好四次SSE/零次JSON补发、375×812无横溢出且发送按钮完整可见、弹窗初始焦点/Tab循环/打字焦点/Escape、断网Schema无假连接、登录/注册名称与隐藏表单inert、邀请码请求头与错误显示。

查询使用真实API→五节点Runner→校验→SQLite；**仅模型为固定脚本，未初始化语义RAG**。断网与注册表单使用显式浏览器fetch注入；注册头验证不代表真实MySQL开户成功。首次未登录访问产生预期401，mock环境readiness预期503；无React运行异常，CDN Tailwind和浏览器Babel生产构建警告仍存在。

## 范围调整与保留事项

真实浏览器暴露公共GET缺认证头、0行结果被条件隐藏、手机输入区越界，纳入修复后复测；Tab测试曾错误选中每个容器的last-child，修正为实际最后控件，未弱化焦点循环验收。登录页验证先退出合成登录态，避免合法的自动跳转干扰。没有把这些工具/测试脚本问题误归因于产品功能。

五阶段MVP加固已交付；仍需真实LLM/embedding凭据、人工标注业务题及PG/MySQL环境建立语义召回、答案正确率和生产容量基线。现有学习、自动修复等模块没有自动加入请求计划；多租户、跨进程会话、外部数据库长稳态、生产静态资源预编译、全部无障碍合规和全仓静态债务不是本轮已完成能力。优先后续顺序：按模块清静态债务并把检查升级为全仓阻断 → 业务质量标注与真实provider/数据库验收 → 按测量结果优化容量 → 前端离线构建与深入体验。

原主工作区 `pyproject.toml` SHA256 仍为 `E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3`，与开始时一致。阶段代码仅在 codex/mvp-hardening 独立工作树；提交前检查敏感文件与diff，普通推送，不合并主分支。GitHub运行结果另以Actions实际状态为准，不用本机结果冒充远程结果。
