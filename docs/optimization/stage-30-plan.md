# 阶段30计划：SDK响应兼容与用量来源标识

2026-10-24，用户授权“规划好下一步，然后开始实施”。基线为阶段29最终提交e82406a，独立codex/llm-response-boundary；阶段29未合并main，因此堆叠于其远程分支，不隐式合并main。原主工作区及阶段29工作区保留。

## 依据、方案与取舍

本地安装的litellm1.106.2真实ModelResponse并非dict，具有model_dump；_call_provider原样返回，_observed_call/complete的dict断言拒绝它。SQLGenerator虽兼容属性对象，也接收不到被路由拒绝的正常响应。只用dict合成传输不能覆盖该SDK边界。

另有SDK异常经ProviderServerError等映射后丢失原异常usage的风险。Usage构造器会把缺失prompt/completion计数变为0，model_fields_set也无法还原原始字段是否存在。这是SDK观察值与原始provider字段不同的证据，不能把SDK对象合成测试作为真实账单验证。

比较三条路线：先做持久化会固化现有缺口；先做预测导出会把不稳定来源带到离线评测；本阶段先补SDK响应边界，范围最小且前置依赖明确。推荐并采用第三条。

只在原生SDK边界将结构化非流式响应转换为现有dict协议，保留choices的finish_reason/refusal/tools等，不把任意文本当SQL。dict兼容与注入适配器既有严格契约不变。新增轻量归一化函数，不依赖SDK导入；SDK实际类型只用于离线边界回归。失败先观察usage再沿既有异常分类/重试传播，不增加调用或改变预算。

逐次用量新增usage_basis，区分response_fields和sdk_normalized；SDK无usage仍为未知。SDK归一化值（含0）明确只是SDK观察值，不能据此证明原始provider完整报告；不猜测被SDK抹掉的字段存在性，不读取_hidden_params/原始响应正文。现有complete仍仅表示所观察计数完整且收尾，不表示供应商账单完整。流式SDKchunk在既有消费处标记来源，不新增include_usage请求参数或供应商能力探测。

## 实施与验收

1. 新增真实SDK ModelResponse/Usage/流式chunk与合成acompletion边界回归：正常、缺usage、length/refusal/tools、SDK异常usage、多候选以及字典兼容。先运行目标回归保存失败证据，不发生产模型请求。
2. 增加SDK边界归一化、来源标识和映射前失败用量采集。SDK序列化坏形状用固定安全错误，不能泄露正文/地址。拒绝标量/列表和坏serializer。采集仍只保存脱敏元数据。
3. JSON/SSE/MCP经真实Router+真实SDK响应对象+真实SQLite验证：SQL结果和用量一致；全部失败仍能读到摘要；llm_calls与attempt_count保持可解释。保留第29阶段并发/取消/预算回归。
4. 定向/全量pytest、Ruff、原strict36/评测strict1/usage strict1及新增模块门禁；全仓mypy相对178无新增；182前端契约/完整构建。复用已验证的本机依赖，无页面改动则不重复视觉检查。
5. 更新响应/用量契约、运行说明、阶段报告和原始证据；凭据/范围检查后提交普通推送独立分支。核验代码精确SHA CI成功，再补记文档推送并核验最终CI。不强推、不自动合并main。

不新增费用估算、价格表、持久账本、预测导出、模型调用或供应商特定原始usage解析。真实provider usage覆盖率/SDK补零之前的原始数据、取消后的远端消耗和账单仍待以后具备条件单独验证。

实施已完成，本地及代码精确SHA CI通过；设计取舍、回归和证据见[阶段30报告](stage-30.md)。
