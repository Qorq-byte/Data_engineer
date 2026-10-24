# 阶段30：SDK响应兼容与用量来源标识

2026-10-24，用户授权规划并实施下一步。本阶段按 [计划](stage-30-plan.md) 从已验收阶段29最终e82406a创建独立codex/llm-response-boundary（.stage30），阶段29尚未合并main，因此分支承接阶段29，不自动合并main。原主工作区pyproject.toml/docs/audits及既有工作区均保留。

## 选择理由与实现

在已安装litellm1.106.2中直接构造真实ModelResponse，确认isinstance(response,dict)为False；原_call_provider原样返回后被_observed_call拒绝。直接推进持久化或预测导出会继承该缺口，故先补最小SDK边界。

- app/llm/responses.py：独立无SDK导入的归一化函数；dict兼容返回，结构化SDK对象调用model_dump并检查仍为dict。序列化属性/调用异常、坏返回形状使用固定安全TypeError，不回显异常正文。只在原生SDK传输边界使用，注入适配器的既有dict契约不放宽。finish_reason/refusal/tool_calls与usage均保留给既有SQL守卫，不能把任意字符串当SQL。
- app/llm/router.py：正常SDK结果先观察，再归一化；SDK异常在原异常分类/重试映射前观察直接usage。流式仍在原消费点采集，正常/异常/取消/关闭不额外重试、不增加预算，不改变上游关闭路径。流异常再次观察usage，不覆盖此前来源；直接dict流的length不错误标为SDK来源。
- app/llm/usage.py：逐次新增usage_basis=response_fields/sdk_normalized，保留第29阶段请求隔离、有界、部分和、缺测与完成状态。可选SDK元数据属性读取失败按未知处理，不替换实际响应或异常。CI新增responses模块strict门禁。

Harness计划、节点顺序/生命周期、模型选择、调用预算、重试、只读约束、三协议错误码和原有响应字段保持原语义。无需新增数据库/后台任务/费用逻辑。三协议直接消费同一摘要，无各自推算。

## SDK来源的实际限制

检查真实Usage构造器发现prompt_tokens/completion_tokens/total_tokens缺失会补0，fields_set也包含被补字段，无法还原原始存在性。因此sdk_normalized只承诺SDK观察值，合法0保留，完全缺usage仍未知；complete/usage_coverage仅是观察字段完整性，不是供应商原始usage或计费覆盖率。没有读取_hidden_params/原始响应正文、猜测字段存在性、修改价格表或新增include_usage请求参数。

字段、来源与运行说明已更新 [用量契约](request-llm-usage.md)、[查询契约](query-contract.md)及README。费用估算、持久审计、预测导出继续独立后续；生产SDK版本变化、不同provider原始usage、取消后的远端消耗与账单仍未测。

## 验证与证据

使用阶段29已验收Python3.12.13隔离环境，复用其本机依赖（不修改项目依赖）。设LITELLM_LOCAL_MODEL_COST_MAP=True和RAG_EMBEDDING_PROVIDER=mock，conftest固定测试密钥/mock；仅替换litellm.acompletion为合成传输，Router、真实SDK模型类与SQLite均实际执行，不请求生产模型。Windows asyncio本机socketpair仍需沙箱外测试。前端从已缓存锁文件离线npm ci，不新增依赖或联网供应商请求。

实现前新增21项回归全部失败，[原始证据](results/stage-30-before.txt)：真实SDK对象被拒绝、异常usage在映射后丢失、缺少来源等；其中归一化函数/来源字段的新契约测试因功能未实现而失败，不把所有失败说成同一种旧产品故障。实现后21项通过，[日志](results/stage-30-after.txt)。后续扩展到34项新增测试，包括首块前/中途流异常、取消关闭、三协议正常与length、SQLite执行、SDK补零限制、坏serializer/元数据属性、无SDK副作用导入和多候选不双计。

定向LLM与三协议回归314通过（25.84秒），[日志](results/stage-30-targeted.txt)；该轮之后又补2项流异常回归，纳入最终全量。首轮全量3637通过/1跳过（119.41秒），之后为最终来源修正和补充用例再次核验；最终全量 **3640通过、1跳过、2警告，116.55秒**，见[日志](results/stage-30-pytest.txt)，包含全部34项新增回归。

Ruff通过；原strict36、评测strict1及边界strict2通过；全仓mypy仍178项，按file/code/message多重集新增0，[摘要](results/stage-30-types-summary.json)。182前端函数契约及三页46文件完整构建/重复构建/故意坏JS保留旧产物通过，[前端](results/stage-30-frontend.txt)/[构建](results/stage-30-build.txt)。无页面变更，不重复视觉测试；存量类型债务与构建依赖告警未作为已修复事项。

## 交付

阶段计划、响应归一化、来源标识、映射前usage观察、SDK/协议回归和类型门禁已实现。本地全量最终检查已通过；提交推送和精确SHA CI待核验后补记。不强推、不自动合并main。
