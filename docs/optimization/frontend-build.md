# 前端构建与部署

阶段21起，`frontend_design/{landing_page,form,workspace}.html` 是源码，正式路由不再直接返回这些文件。保留既有页面与API，使用esbuild生产编译React/JSX、Framer Motion和Three.js；Tailwind在构建期生成CSS，本地字体来自锁定的Fontsource包，中文仍使用系统字体。默认头像为本地SVG。用户自定义远程头像/API与模型服务仍可需要网络。

## 构建与验证

```bash
# Node.js 22+，首次安装需访问npm注册表或已有缓存
npm ci --ignore-scripts
npm run check:frontend
npm run build:frontend

# CI额外检查：连续构建字节相同、引用/字体存在、编译失败不覆盖旧产物
npm run check:frontend:build
```

`package.json`及`package-lock.json`锁定版本；不用全局Tailwind/Babel，不运行安装生命周期脚本。esbuild使用平台对应的可选二进制包，安装时不要使用`--omit=optional`或`--omit=dev`。Node用于构建，运行服务无需Node或npm。

构建将页面脚本按原顺序提取到生产IIFE，浏览器不再编译JSX；`NODE_ENV=production`在编译期定义。样式按Tailwind→字体→既有页面规则加载，完整字符串类名由Tailwind扫描源码保留。不是通用HTML打包器，新页面、动态拼接类名或额外运行时库须先扩展构建与检查，不能直接添加CDN标签。

产物目录`frontend_dist/`已忽略，不提交`node_modules`、字体二进制或生成JS。三个HTML引用`/assets/`中的哈希JS/CSS及本地字体；默认头像保留稳定文件名。`manifest.json`记录产物SHA-256与字节数（不包含清单自身），供部署完整性检查；`assets/THIRD-PARTY-LICENSES.txt`及JS的linked法律说明必须随整个目录交付。构建中相同字体资源共用文件。清单无时间戳/本机路径，重复构建检查只证明同环境相同输入可重复，不承诺跨所有操作系统字节恒等。

## 部署与回退

1. 在可信构建环境安装并验证，保留源码提交SHA和整个产物目录，按清单核对文件摘要。
2. 停止服务或准备独立版本目录，将该版本源码/Python环境及完整`frontend_dist/`一起发布；然后启动`python main.py`（默认8001，可用`APP_PORT`覆盖）。验证`/`、`/login`、`/workspace`及资源均200。
3. 首次请求若为503且code为`FRONTEND_NOT_BUILT`，先核对页面文件、assets目录和最终manifest，再在部署前重建。不要复制源码HTML充当回退。仅API部署可不构建UI；`/health`、API权限和工作流不依赖静态目录。
4. 回退时一起恢复上一版本源码和对应静态目录，重启并检查三页；不要只回退HTML或只回退带哈希脚本。

构建先在内存完成所有编译和许可证收集，成功后才更换输出目录，清单通过临时文件改名最后发布；编译错误保留原产物。输出目录固定并检查符号链接，脚本不会清理其他目录。该过程**不是在线原子切换**：写盘故障、进程退出或在线重建仍可能导致静态请求中断，必须采用上面的停服/版本目录方式。应用启动时没有assets目录会注册404回退；随后构建需重启应用。页面每次检查完成标记，缺少标记返回503，但不逐请求重新计算全部文件摘要。

HTML使用`Cache-Control: no-cache`避免长期缓存旧页面；静态资源沿用Starlette ETag/Last-Modified重验证，不承诺immutable。构建会移除旧哈希资源，旧标签页应在部署后刷新，当前没有滚动部署的多版本资源保留策略。该阶段不新增CSP生产策略或Service Worker。

## 依赖安全边界

2026-10-23安装审计发现初选PostCSS8.5.6的安全问题，已升级8.5.31；Tailwind采用兼容3.4.19。完整`npm audit`仍报告**7项（5 high、2 moderate）**，包括braces及其chokidar/fast-glob/micromatch/Tailwind传递链、postcss-selector-parser/postcss-nested链。braces注册表最新3.0.3仍在受影响范围；其他建议修复涉及Tailwind4大版本迁移。没有关闭审计、添加忽略或运行`audit fix --force`。

这些依赖在构建工具链中；它们不打入浏览器包、不被Python服务执行。当前Tailwind内容为仓库内固定HTML的raw文本，不接收API请求、上传文件或用户提供的glob/选择器。但构建不可信提交或引入第三方插件仍有构建期拒绝服务风险，**不能称全依赖安全审计通过**。只在隔离且有限资源的可信构建环境执行；后续兼容补丁可用时更新锁文件，或单独规划Tailwind4迁移并验证样式差异。[阶段21报告](stage-21.md)保留完整审计证据与实测限制。
