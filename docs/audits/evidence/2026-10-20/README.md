# 评估验证证据

本目录保留 2026-10-20 的只读评估与隔离验证结果。测试采用临时副本、Mock LLM/embedding；不代表真实模型质量或生产性能。页面截图来自仅提供静态文件的临时服务，不代表前后端已联调成功。

- [pytest.txt](pytest.txt)：2842项现有测试，2833通过、9项MCP导入失败；含失败原因。
- [ruff.txt](ruff.txt)：全仓192项静态检查结果，未自动修复。
- [mypy.txt](mypy.txt)：140个源文件，62个文件285项错误。
- [probes.txt](probes.txt)：仅操作内存/临时SQLite、DuckDB、LanceDB/FTS5与无lifespan的ASGI应用；记录只读、超时、参数、会话、缺节点和索引替换的行为。PowerShell采集使少数提示字符串的长破折号出现替代字符，关键结果字段未受影响，未手工改写原始日志。
- [workspace-desktop.png](workspace-desktop.png)、[workspace-mobile.png](workspace-mobile.png)：1280×720／390×844工作台初始页。
- [login-desktop.png](login-desktop.png)、[landing-desktop.png](landing-desktop.png)：登录与落地页。

解释器替代方式、已安装版本、命令、页面解析结果的验证矛盾及尚未执行的项目，见[验证方案](../../2026-10-20-validation-plan.md)。未保存任何真实API key、密码、业务数据或登录会话。
