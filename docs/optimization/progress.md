# 实施进度

- 初始：main@118f9d6；origin可读，GitHub CLI已登录Qorq-byte；独立工作树.verify_tmp_optimization、分支codex/mvp-hardening已创建。
- 原有未提交的pyproject.toml修改未带入分支；上一轮评估文档复制到本分支保存依据。
- 阶段1已提交并推送：24c396fcf0bc4e788604d0991ca76a386e18558b；远程SHA核对一致，全量2893 passed。阶段2开始，阶段3–5待开始。每阶段验证记录放在同目录stage-N.md。
- 安全复核新增两项实际问题：默认development也使用公开JWT secret；MySQL可执行注释可绕过普通AST检查。已将弱secret拒绝扩大到所有环境、注册改为邀请制，并阻断可执行注释；新增回归覆盖，未降低只读约束。
- 第一轮节点/集成回归599通过、1项EXPLAIN合法包装兼容问题；修复并补齐边界后阶段1全量2893项通过，MCP定向184项通过。
- 现存静态债务基线192项，当前Ruff全仓152项；新增安全模块/客户端/安全回归和MCP文件Ruff通过。mypy阶段快照285项；最终复查超180秒无输出，记录未通过并保留日志，后续定位。存量问题不关闭规则。
