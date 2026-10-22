# 阶段20：工作台 Schema 索引状态与按库维护

2026-10-22，按用户要求先合并此前优化，再依据[先行计划](stage-20-plan.md)实施。阶段20从合并后的main新建codex/rag-maintenance-ui独立分支，不覆盖原工作区、不自动再次合并main。

## 阶段1–19合并

origin优化分支比main多34个提交，main无额外提交，GitHub没有其他待合并PR。通过[PR#1](https://github.com/Qorq-byte/Data_engineer/pull/1)使用merge commit保留各阶段历史，合并提交[30bff50a6d876938bc184688a9fcdd92ccaf8fad](https://github.com/Qorq-byte/Data_engineer/commit/30bff50a6d876938bc184688a9fcdd92ccaf8fad)，2026-10-22T11:41:51Z已合并。880文件差异含此前移除误跟踪node_modules，未将其当新阶段重写。

合并前PR检查[39772123196](https://github.com/Qorq-byte/Data_engineer/actions/runs/39772123196)及源分支最新检查通过；合并后[main Actions39772535204](https://github.com/Qorq-byte/Data_engineer/actions/runs/39772535204) success，核对headSha=30bff50。合并树与阶段19末f7b6f32无内容差异；主分支CI通过后开始阶段20实现。[PR合并证据](results/stage-20-merge.json)/[main CI证据](results/stage-20-main-ci.json)。原主工作区仍保留既有main检出及未提交pyproject.toml/docs/audits，没有强行同步覆盖。

## 问题与实现

原工作台仅取stats，没有当前隔离状态/按库清理入口；无连接时推荐清空全部索引，disconnect忽略rag_index_cleared/warnings。索引统计可能将读取失败或缺字段当0，全量按钮只检查向量数，会漏掉关键词侧记录。

- `workspace.html:schemaIndexRows` 合并连接库/待刷新/恢复失败ID，恢复失败优先，遗留断开库可显示；历史startup结果不作为当前状态。标签“未隔离”不承诺索引非空或模型健康。RAG页读取stats/status/databases，检查业务状态/字段结构，失败显示未知计数和明确错误，不制造假空库/假健康；请求序号防旧GET覆盖新结果。
- 新“Schema按库维护”保留现有Barlow/中文系统字体、中性卡片、monospace ID与状态色，长ID换行、窄屏纵排、动作有目标aria-label、反馈role/status/alert。指定库清理只调用阶段19URL并编码目标，原生确认明确仅Schema及保留其他数据；取消不发请求。重建仅连接库可用，API客户端schemaRefresh也对db_id编码。
- 清理严格验证status/scope/db_id/非负整数removed，重建严格要求rag_indexed=true；503按restored=true/false/null给不同固定提示，权限/源冲突/超时结果未确认不自动重试POST。操作后重新取状态，读取失败独立显示，不抹掉已确认操作结果。
- 维护共用同步busy ref防快速重复，现有三种批量维护与单库互斥，finally释放；无连接文案改为按库处理遗留项。全量清理移入“仅维护窗口”折叠区，保留原确认流程，数据未读/上传中禁用，判断为空同时考虑关键词侧。不是将全量操作升级为在线事务。
- `handleDisconnect` 对rag_index_cleared非true/有warnings显示“连接已断开、索引未完成”和具体后续位置，保留成功关闭事实，不用全成功通知掩盖部分失败。没有改后端节点/计划/权限/SQL安全边界。

## 验证与环境

Windows/Python3.13.9、Node24、Ruff0.17.1、mypy2.4.0、SQLite3.51.0、DuckDB1.5.5、SQLGlot30.18.0。测试继续使用D:/Users/anaconda/python.exe及原site-packages，严格类型检查添加既有type-deps/driver-types；环境LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock、DE_TRACE_DIR为忽略目录。浏览器临时服务另将当前工作树加入PYTHONPATH，确保导入本次代码。

- 先加实际页面函数回归，旧页面 `node frontend_design/_check_rag_maintenance.js` 退出1：缺少实际schemaIndexRows，[记录](results/stage-20-before.txt)。这是缺失能力的固定回归，不是虚构旧浏览器测量。
- `npm run check:frontend` 通过：5段页面脚本编译、原12条刷新契约及新37条实际函数场景，共49条契约，[记录](results/stage-20-frontend.txt)。涵盖隔离优先级/历史不冒充当前、读取失败/旧GET、目标编码/原生确认取消、严格200业务状态、503三态、权限/源冲突/超时、busy重复提交/finally、关键词单侧计数、disconnect部分结果。原测试上下文补充新ref/状态，原成功/失败断言不弱化。
- gstack `/browse` 在127.0.0.1:8020临时服务执行**11项场景**，[结构化证据](results/stage-20-browser.json)：真实持久LanceDB/FTS5重开后的遗留库、确认取消0次POST、一次定向清理且保留健康源3行/文档1条/指标10条、原生SQLite authorizer拒绝FTS DELETE导致恢复失败、显式恢复清理/重建、HTTP200但embedding失败、读取失败、手机长ID/键盘、断开部分结果和断开后重试。没有调用全量clear端点。
- embedding失败为显式provider注入，读取失败为临时服务middleware注入；其余写入/恢复/重开走实际后端与正式API。全部是合成管理员/临时SQLite/持久临时索引，令牌与控制脚本仅忽略目录；不代表生产坏盘恢复、真实模型语义检索或MySQL开户成功。
- [桌面截图](results/stage-20-desktop.png)/[手机截图](results/stage-20-mobile.png)已逐张查看。375×812时viewport/document/body宽均375，长ID折行、按钮在视口内，Tab从重建到目标清理；不宣称完整WCAG审计。控制台保留预期未登录401、mock readiness503和注入503；无React运行异常，CDN Tailwind/浏览器Babel生产构建警告仍在，[控制台](results/stage-20-console.txt)。
- `python -m pytest -q`：**3298 passed、1 skipped、4 warnings，92.58秒**，[日志](results/stage-20-pytest.txt)。Python测试数与阶段19相同，新增覆盖在Node契约；Windows uvloop跳过由Linux补验。
- 核心mypy命令沿用`.github/workflows/checks.yml`的34源文件范围，**零错误**，[记录](results/stage-20-core.txt)。全仓`mypy app --output json`仍退出1，**197项/42文件**，对阶段19按文件/错误码/消息多重集新增0，[诊断](results/stage-20-after.jsonl)/[摘要](results/stage-20-summary.json)。`ruff check .`通过，[记录](results/stage-20-ruff.txt)。不称全仓类型通过，不将测试耗时当性能提升。

## 调整与限制

浏览器工具初期落入空白页的结果不计验收；已检查截图并重做。诊断发现gstack后台进程的可选helper通过Node spawn找不到原生bun（PATH只有bun.cmd），出现FATAL spawn bun ENOENT；测试进程临时PATH加入已安装bun.exe目录，并显式保持gstack server生命周期后11项完成，没有修改产品或关闭断言。preview首轮缺PYTHONPATH和Node契约提取缺activeTab是测试搭建问题，修正后复跑。

仍使用现有CDN资源/浏览器JSX，未离线打包；当前Schema状态不是模型或存储实时探活。全量清理仍为维护窗口、按库清理也非跨进程/掉电事务，后续新刷新可建索引。真实模型/embedding、PG/MySQL生产环境、语义召回/SQL答案准确率和生产容量未验收。读取失败禁用维护是保守边界，重试读取不会自动重复写操作。下一候选先核对前端构建与静态资源能否脱离CDN、形成可复现部署，再单独规划。

## Git 与远程验收

待暂存审查后普通提交/推送codex/rag-maintenance-ui，核对SHA与Linux CI，再补记远程证据。本阶段不自动合并main；原主工作区保护检查继续保留。
