# 阶段8执行计划：生成与执行节点的接口契约

日期：2026-10-21；基准40b809e，阶段7代码与补记CI均成功。继续codex/mvp-hardening独立工作树，保护原工作区改动。

## 目标与边界

将既有strict门禁扩展到生成、执行节点及预算适配器，默认追踪导入，不设置silent/skip/ignore_errors，不新增宽泛ignore或扩大Any规避诊断。标准目标为`mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py`。当前前四个目标复测24项诊断，涉及AgenticNode、执行节点及SQL提取；全仓上一阶段230项仍未通过。

采用最小结构协议兼容LiteLLMRouter和BudgetedRouter，避免具体类耦合；SQL结果保持现有字典线格式，用TypedDict表达。原生多驱动分发入口和SQL单元格保留已有动态边界，驱动辅助函数使用可核对的具体类型或窄协议。API层其余存量问题不纳入本次通过声明，查询服务仅修复可空Schema目标与Router注入契约。

## 顺序步骤与验收

1. 保存基线；核对Router真实签名、驱动实际返回值、节点基类依赖。计划先于代码修改。
2. Router：为SQL生成所需config/complete/complete_stream定义最小协议；预算包装显式转发，保持物理调用计数、流消费、错误与取消传播，不引入节点调用或动态流转。
3. 节点：明确AgenticNode延迟依赖的实际类型、工具名称边界；执行结果结构与驱动辅助接口有类型依据。Schema缺省目标显式可空；SQL提取返回字符串，不以cast掩盖。发现行为问题需记录范围调整和回归。
4. 验证：新增适配器正常/失败/流/预算耗尽测试和必要异常边界测试；运行标准目标mypy至退出0，复测全仓并比较诊断指纹；全仓Ruff、前端编译、定向和全量pytest通过。CI扩展同一类型门禁，保留全部原检查。
5. 交付：stage-8报告记录命令、版本、实测结果和剩余限制；同步开发/检查说明与进度；审查diff与敏感内容，单独commit、普通push、核对SHA和远程CI，失败先修复本阶段。

## 交付物和限制

最小实现、边界回归、CI和文档及结果文件。Harness计划/Runner和SQL只读/验证/权限/超时逻辑保持；全仓类型不宣称通过。真实模型质量、真实PG/MySQL在线行为及生产容量无环境时继续明确待验证。全量测试基线Windows2964通过、uvloop1跳过；Linux2965通过。

## 进度

- 步骤1–4本地完成：扩展目标0错误，全仓230→201且无新增诊断；645定向/2982全量通过，Windows跳过uvloop1项；Ruff/前端通过。EXPLAIN消费参数同步为只读Mapping，开发指南用真实SQLite字典结果验证。
- 步骤5完成：代码a224df6已普通推送，远程SHA一致，Actions39548676833成功；Linux扩展mypy/Ruff/前端通过，全量2983通过，uvloop已执行。远程证据与报告同步补记。
