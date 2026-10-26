# 阶段33：候选级生成来源与完成信息

2026-10-24，基线阶段32最终 f9954a2，独立分支 codex/candidate-completion-provenance、工作区 .stage33。先行设计见[计划](stage-33-plan.md)。承接尚未合并 main 的阶段29—32，保留主工作区用户改动，不强推、不自动合并。

## 结果与边界

SQLCandidate及公开CandidateResult新增generation_source和finish_reason。非流式两种策略通过既有拒绝/工具/结束状态检查后保存该响应首个choice的stop/null；同响应alternatives共享字段，缺失不借其他候选或请求attempt补齐。每次独立读取响应model，缺失仍使用本次请求/配置模型名，不沿用前响应；该回退名不能证明实际provider模型身份。

系统元数据SQL及prepared分支标记prepared/null，通过dataclass复制避免修改输入；纯文本模型流为stream_text/null，不从EOF猜测完成。自愈复制实际修正候选，保留其来源、完成信息及原有元数据。旧构造默认unknown/null，不影响现有调用方式。

JSON/SSE done保留字段；SSE仍为工作流事件流，正常生成沿用非流式调用。MCP新增只读candidates，包含相同来源/完成信息，排除execution防止绕过顶层100行限制。原sql/rows/错误语义保留。预制sqlite_master查询可能被既有业务Schema校验拒绝；本阶段只保证这种失败响应仍保留来源，不改变校验规则。

导出只读取显式选中的候选：prepared和无效来源拒绝；stream_text/unknown即使填写stop也不升级，但显式length/refusal/tools仍标记拒绝。缺generation_source的旧文件兼容。request-prediction-v1行结构及哈希验证不变，源响应哈希覆盖新增字段；审计仍v1元数据，无SQL/摘要认证，费用及真实生成耗时仍null。来源仅代表代码路径，不是签名、真实模型调用或业务质量证明。

Harness固定节点顺序、权限、预算、重试和取消释放机制均未修改；默认关闭审计未改。未进行真实模型、多方言业务质量、账单或生产负载验收。流式结构化终止协议、SQL摘要审计绑定、真实模型评测和费用估算留待后续规划。

## 验证证据

新增33项候选回归覆盖两策略/字典及对象响应/alternatives/缺完成标记/交错请求、模型名隔离、预制复制、文本流关闭、自愈、三协议以及导出来源隔离。首轮[30项失败回归](results/stage-33-initial.txt)为23失败/7通过，其中3项预制协议测试误假定sqlite_master通过现有Schema校验，已调整为验证失败时仍保留候选证据。初次关联[489项回归](results/stage-33-target.txt)通过后，补充3项缺结束原因覆盖及MCP排除候选execution断言。

真实Router+合成LiteLLM SDK ModelResponse→真实SQLite查询→本机审计→显式导出→SQLite评测，新增断言确认候选/导出stop、completion_source及完成计数；其余案例仍保留缺预测分母，不从请求用量推测。没有外部模型请求。

最终本地[全量pytest](results/stage-33-pytest.txt)为 **3767通过、1平台跳过、2警告，153.25秒**。新增SDK导出断言另行[单独通过](results/stage-33-sdk-export.txt)，1通过/53未选择；全量后没有实现代码变更。[Ruff](results/stage-33-ruff.txt)、[核心strict36](results/stage-33-core.txt)、[LLM/审计/导出/评测strict7](results/stage-33-strict.txt)通过；全仓mypy相对阶段32仍178项，按file/code/message多重集新增0，[摘要](results/stage-33-types-summary.json)。自愈改用replace后一度产生2项Any返回类型错误，已为实际修正候选补类型标注并复核清零新增。

[前端182函数契约](results/stage-33-frontend.txt)、[三页完整构建](results/stage-33-build.txt)、重复构建/故意坏JS保留旧产物通过。前端安装使用锁文件及本地缓存；沙箱首次拒绝npm目录创建，授权范围内重跑成功。复用已验证Python3.12环境，Windows异步测试在沙箱外使用socketpair。无页面修改，不重复视觉检查。

## 交付

代码[908ebce](https://github.com/Qorq-byte/Data_engineer/commit/908ebce15875a0fa289de2cb94f3457a6057bb18)已普通推送codex/candidate-completion-provenance。[Actions40090996783](https://github.com/Qorq-byte/Data_engineer/actions/runs/40090996783)成功，headSha精确匹配，见[结构化证据](results/stage-33-ci.json)和[Linux测试摘要](results/stage-33-ci-tests.txt)。干净环境 **3768通过、2警告，63.51秒**；本地跳过的平台项在Linux实际执行。182前端契约、完整构建、Ruff及所有严格类型门禁均通过。

本次补记验收文档另行提交推送并核验最终CI；不以代码CI代替最终提交验收。主工作区原有改动及main保持原状，不强推、不自动合并。
