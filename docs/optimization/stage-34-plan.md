# 阶段34计划：流式候选完成证据

2026-10-24，用户授权规划后实施。从阶段33最终339ede8建立codex/stream-completion-evidence/.stage34；不改原工作区、不合并main。使用brainstorming及planning-with-files，将既有逐阶段设计存放约定保留在本目录。

## 设计与取舍

对比请求旁路状态、全面替换文本接口、增加可选事件能力：采用第三条。纯dataclass StreamEvent传递delta/end，模块不导入SDK。end只在完整消费及尾部检查后交付，source为model_stream/stream_text，finish为stop/null；不携带usage、密钥、工具参数或价格。文本调用方继续收到原str。旧文本路由/Mock适配成stream_text/null，不因EOF升级完成。

原生SDK流沿用现有终止/拒绝/工具/非文本/stop后继续内容检查，正常消费后输出model_stream/stop。限流、重试、超时、usage观察仍只包原物理调用一次，不另开请求。预算适配器转发可选事件能力；覆盖文本方法却只继承父类事件方法的旧适配器必须保持旧文本路径，防止绕过注入实现。显式实现事件方法的适配器可声明结构化能力，但不是provider真实性认证。

SQLGenerator新增事件接口，保留generate_stream文本兼容；节点完整消费并校验事件后才构造候选，同响应alternatives共享model_stream/stop。缺end、重复end、end后内容、错误类型/来源/标记、异常/取消均不产出成功候选。明确的文本兼容end为stream_text/null。各层finally关闭上游，异常不重试、不续接新响应；usage和预算不从事件额外累加。

SQLCandidate/DTO允许新增model_stream来源，导出允许所选候选自身的stop；旧stream_text/unknown不升级，prepared仍拒绝。JSON/SSE/MCP沿既有DTO保留字段，公开SSE仍为工作流事件流，不切换生产调用方式。不改审计schema、不保存SQL、不估价、不运行真实provider。

## 实施顺序与验收

1. 失败回归先行：真实SDK合成chunk与尾usage、两接口文本一致、候选与alternatives、旧适配器/覆盖继承保护、事件畸形/非法尾部、交错请求隔离。
2. 实现纯事件验证、原生流转换、预算转发、生成与节点接线；断言取消/超时/提前关闭释放资源、1物理调用=1预算=1attempt，失败不重试，无尾usage不补token零。
3. 显式导出及三协议DTO验证；关联/全量pytest、Ruff、原strict36+7与新事件模块strict，全仓相对178项无新增；182前端契约/完整构建。
4. 更新契约/README/总计划/进度/报告及证据，提交普通推送；精确代码SHA CI通过后补记文档并核验最终CI，不强推、不合并main。

范围自检：不扩展SQL审计认证或费用功能，不改变Harness节点顺序、权限、只读政策、预算上限和现有连接建立失败重试。流事件来源是代码路径证据，无法认证操作者提供文件或真实账单。

实施完成：本地全量及代码精确SHA CI均通过，结果与边界见[阶段34报告](stage-34.md)。补记验收文档普通推送后核验最终CI。
