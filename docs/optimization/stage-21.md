# 阶段21：可复现的本地前端构建

2026-10-23，按[先行计划](stage-21-plan.md)实施。基线708c8dc，阶段20补记Actions39884557824成功；新分支codex/frontend-build从阶段20末派生，main仍为已合并阶段1–19的30bff50，不自动合并本阶段或覆盖原工作区。

## 问题与范围

`app/api/__init__.py:create_app`原直接返回frontend_design三页，页面从CDN加载Tailwind、React开发版、Babel、Framer Motion、Three.js、Google Fonts及默认Unsplash头像。断网或CDN不可达时不能保证页面启动；仅运行已有JSX编译检查不会产生可部署资源。源码行数和功能实现度不等同于可复现交付。

本阶段保留三页、React18.3.1/Framer Motion11.11.17/Three0.159.0及API契约，不迁移组件框架、不改SQL/RAG/Harness行为。构建依赖锁定esbuild0.28.2、Tailwind3.4.19、PostCSS8.5.31、Fontsource5.3.0，保留Babel7.29.0用于现有源码契约检查。

## 实现

- `scripts/build-frontend.cjs:buildFrontend`将已有内联脚本按原顺序提取，esbuild在构建期生产编译JSX并打包库，Tailwind扫描三页raw源码生成CSS，字体改本地latin子集（中文系统回退不变）。保留原页面专用CSS顺序；首页Three全局接线及工作台Motion接线保留。默认头像改`frontend_design/default-avatar.svg`，自定义URL行为不改。移除workspace对console.error中Warning的屏蔽。
- 产物在忽略的frontend_dist，JS/CSS/字体使用内容哈希；清单记录SHA-256/字节数、无时间戳与本机路径，生成完整第三方许可证和linked法律说明。所有编译/许可证读取成功后才更换固定输出目录，拒绝输出根符号链接；清单最后通过临时文件改名发布。编译失败保留旧产物，但写盘故障/掉电/在线切换不提供事务保证。
- `app/api/frontend.py:register_frontend`独立注册固定三页及/assets，create_app接入；页面缺失、assets缺失或完成清单缺失返回503 FRONTEND_NOT_BUILT和准确构建命令。没有assets时静态路径404，API仍可启动；随后构建须重启。HTML no-cache，资产沿用Starlette重验证；不公开源码/node_modules，不新增生产CSP。
- `scripts/check-frontend-build.cjs`在CI验证三页无外部运行时资源、无text/babel、所有静态/字体引用存在、摘要/字节一致、两次构建相同；使用临时副本注入真实语法错误，并核对旧产物所有字节未被覆盖。不是mock编译器或降低编译约束；日志中的Expected identifier是刻意负例，脚本成功取决于拒绝并保留旧产物。
- `.github/workflows/checks.yml`在原49项前端契约后加入构建验收，Python检查范围不缩小。README/AGENTS加入必要构建命令，[部署说明](frontend-build.md)规定整目录交付、停服或版本目录切换及配套回退。

## 验证、环境与证据

Windows/Python3.13.9、Node24、npm12.0.2、Ruff0.17.1、mypy2.4.0、SQLite3.51.0、DuckDB1.5.5。沿用原site-packages和type-deps/driver-types，LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock，追踪写入忽略目录。没有依赖真实生产数据/模型。

- `npm ci --ignore-scripts --cache .verify_tmp_scripts/npm-cache-stage21`干净安装成功，[记录](results/stage-21-npm-ci.txt)。首轮共享npm缓存EEXIST，改隔离缓存解决，未删除共享文件或使用force。
- `npm run check:frontend`通过：5段页面脚本编译、原12+37实际函数契约，共49项，[记录](results/stage-21-frontend.txt)。`npm run check:frontend:build`全部通过，[记录](results/stage-21-build.txt)。三页共46个文件（含manifest），**2,030,157字节**，仅是未压缩交付大小，不是性能提升证据；[产物摘要](results/stage-21-build-manifest.json)。同环境连续构建一致，未声称跨平台恒等或首次安装离线。
- 新增`tests/test_api/test_frontend_delivery.py`共6项：三条缺失产物路由、完整页面/资源类型与路径边界、不完整构建503、真实create_app在缺UI时health仍200且未认证API401。完整`python -m pytest -q`最终**3304 passed、1 skipped、4 warnings，99.72秒**，[日志](results/stage-21-pytest.txt)。首轮3303通过后补齐清单边界再全量复跑；Windows uvloop跳过由Linux补验，不把测试耗时当容量结论。
- 核心strict34源文件零错误，[记录](results/stage-21-core.txt)；全仓Ruff通过，[记录](results/stage-21-ruff.txt)。全仓`mypy app --output json`仍退出1，**194项/42文件**，相对197项新增0、移除3（被迁出页面函数已有返回类型）；[诊断](results/stage-21-after.jsonl)/[摘要](results/stage-21-summary.json)。单独`mypy app/api/frontend.py`仍因API包初始化连带39文件的174项既有错误退出1，新增模块无诊断，[记录](results/stage-21-frontend-types.txt)；不设置follow-imports/ignore绕过既有问题，不宣称全仓或这条命令通过。
- gstack `/browse`执行**8项浏览器场景**，[结构化证据](results/stage-21-browser.json)：首页本地字体/Three画布，登录空值验证，注册邀请字段切换，合成身份工作台，真实RAG当前状态，本地默认头像实际加载，375px/键盘，从重建按钮调用真实POST且最终提示已重建。重建后读取stats/status/databases；HTTP200本身不当业务成功。
- 临时FastAPI在127.0.0.1:8021，lifespan关闭并显式初始化临时SQLite/LanceDB/FTS5、mock embedding和合成管理员。**仅QA服务注入CSP禁止外部脚本/样式/字体/连接**，三页performance资源清单全部同源，观察外部资源请求0；不是模拟页面内容，不作为生产CSP配置。MySQL注册/登录未实测（身份注入仅用于工作台验收），模型语义/生产负载未评测。
- [桌面](results/stage-21-desktop.png)、[手机](results/stage-21-mobile.png)、[手机按库维护](results/stage-21-mobile-maintenance.png)已逐张查看。viewport/document/body宽均375，目标按钮在视口内，Tab从重建进入清理；不宣称完整无障碍审计。[控制台](results/stage-21-console.txt)没有React运行异常/CDN或Babel生产警告；仍有既有Three采样/软件GPU提示、mock就绪503，不当性能结论。合成昵称的临时脚本终端编码问题已改ASCII，动画中重复选择器改用最新snapshot引用，均未改产品逻辑。

## 依赖风险与已知限制

`npm audit`初选报告8项，PostCSS兼容升级后仍**7项（5 high、2 moderate）**：[升级前](results/stage-21-audit-before.json)/[当前](results/stage-21-audit-after.json)。Tailwind3的braces传递链仍受影响，注册表最新braces3.0.3无同系列修复；postcss-selector-parser链建议涉及Tailwind4迁移。保留完整告警、无忽略/强制修复，不称安全审计通过。仅构建工具受影响，不进入浏览器或Python运行服务；当前输入为可信仓库固定raw页面，不接收上传内容/外部glob。构建不可信提交仍有拒绝服务风险，需隔离与资源限制；后续跟进补丁或独立迁移验证，详见部署说明。

仍有旧页面长文件、完整UI测试覆盖不足及系统设置展示值与真实运行配置的既有一致性问题，本阶段只保持行为，不将打包当功能实现或真实模型健康验证。没有压缩/CDN缓存/滚动多版本资产策略；更新后旧标签页须刷新。构建期依赖注册表/缓存，运行静态UI不再依赖CDN；用户配置的外部服务/头像不在离线承诺内。不在在线目录重建，不将清单当跨文件系统事务或逐请求文件校验。

## Git与下一步

待暂存审查/凭据检查后普通提交推送codex/frontend-build，核对SHA与Linux CI，再补记远程证据；不合并main。原工作区未提交文件及本工作树旧未跟踪计划文件保留。下一候选先核对工作台配置展示与运行时真实生效状态，基于代码独立规划最小闭环，避免在本阶段附加行为改造。
