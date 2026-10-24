# 阶段26：LLM输出参数上限与未支持选项

2026-10-23，依据[先行计划](stage-26-plan.md)实施；基线1a80342（Actions39947927462成功），独立codex/llm-token-policy，不合并main。

## 实现与依据

旧factory没有消费max_tokens，RouterConfig无对应字段；routing_strategy/temperature也未消费，SQLGenerator三处显式温度0.3。三个旧回归先[失败](results/stage-26-before.txt)后[3通过](results/stage-26-first.txt)。选择接通输出参数上限，其他两项只读；没有增加未经评测的自适应模型选择。

- app/llm/router.py:RouterConfig.max_output_tokens/_completion_options，参数在预算前校验、在真实_call_provider传输前复核，处理两种别名、较小值/截上限、非法值/extra_body覆盖；流式、重试和fallback复用，同步既有物理预算。独立路由None不自动加上限，mock不裁剪合成输出。
- factory.py:build_router_from_settings严格读取1–32768整数并构建当前路由；settings.py拒绝策略/温度与未知写字段，严格上限输入和坏历史记录，公开配置/运行上限及实际行为/未支持字段。继承阶段25持久化确认后发布，GET不自动应用漂移。
- workspace.html与实际函数契约：移除策略/温度编辑和payload，显示只读来源；编辑上限、已配置快照和运行上限分开，非整数/范围错误不写，成功必须核对实际运行上限，未知必须重读。没有更改计划/节点调用、模型fallback或SQL只读。

接口/别名/独立路由和provider兼容边界见[上限契约](llm-token-policy.md)。这是输出请求参数限制，不是总token/费用或真实模型行为保证。

## 验证与环境

Windows Python3.13.9/Node24/Ruff0.17.1/mypy2.4.0；沿用原site-packages/type-deps/driver-types，LLM_MOCK_MODE=1、RAG_EMBEDDING_PROVIDER=mock、LITELLM_LOCAL_MODEL_COST_MAP=True，追踪在忽略目录。新增传输测试明确实例mock_mode=false并截获真实litellm.acompletion参数，无网络/真实凭据。

- 新增40项Python用例：真实Router→_limited_call→_call_provider→LiteLLM spy覆盖较小/较大/缺省/None/别名、n/温度保持、流式文本、主模型两次失败后fallback、3次真实预算计数、非法参数预算0；独立无上限、坏配置/严格API无部分修改、真实SQLite1/32768持久化冷恢复/历史字段忽略/GET漂移。python -m pytest tests/test_llm tests/test_api/test_llm_persistence.py tests/test_api/test_settings_llm.py -q：**179通过，16.96秒**，[日志](results/stage-26-targeted.txt)。Spy证明实际传输调用参数，不代表外部provider执行或费用。
- python -m pytest -q：**3456通过、1跳过、4警告，104.14秒**，[日志](results/stage-26-pytest.txt)，Windows uvloop现已由下述Linux实际执行补验。Ruff通过，[日志](results/stage-26-ruff.txt)，原CI核心mypy命令**35文件零错误**，[日志](results/stage-26-core.txt)。全仓mypy app --output json仍退出1，**181项/42文件**，相对181项新增0/移除0，[诊断](results/stage-26-after.jsonl)/[摘要](results/stage-26-summary.json)。不以测试耗时推断性能。
- npm run check:frontend：源码5脚本编译，LLM契约33→**41**，全部前端契约174→**182**，[日志](results/stage-26-frontend.txt)。原场景保留，调整fixture为新接口；新增未支持字段元数据、配置/运行漂移、未就绪/独立未设上限、非法值不写/假称已应用拒绝。完整本地资源/字体/许可证/清单、重复构建和编译失败保留通过，[日志](results/stage-26-build.txt)，Expected identifier为故意负例。
- gstack /browse **12场景**：[证据](results/stage-26-browser.json)，正常、编辑64但配置/运行仍4096、保存后64、外部改29/运行仍64、历史策略/温度忽略、混合写max_tokens+temperature整体422、仅内存13、伪成功无实际上限拒绝、原生SQLITE_READ拒绝、重读恢复、冷重建后29、375px布局。临时FastAPI127.0.0.1:8026、真实SQLite设置协议/mock路由，合成认证/QA模式/CSP只在忽略目录，无真实MySQL/LLM。[桌面](results/stage-26-desktop.png)/[手机](results/stage-26-mobile.png)均查看；0外部资源、375px无溢出/控件在视口。记录的UI写payload无温度/策略，唯1次人工负例带temperature。[控制台](results/stage-26-console.txt)保留未认证401/mock就绪503/人工422/注入读拒绝503，无React异常。不声称完整无障碍审计。

## 兼容调整与限制

原策略错误测试400改422，与移除字段后的严格业务契约一致；部分修改回归仍断言不变。原并发测试用未支持temperature补丁改为已支持max_tokens=777，仍验证两写+启动串行、前写default_model保留，并新增运行上限777断言，未关闭检查或削弱目的。现有LLM页面契约同步新字段并保留全部旧场景；Ruff行宽修复，跨文件复用真实SQLite fixture改模块赋值以避免F811，不加规则忽略。初期猜测测试路径已按rg --files核对。

上限应用仅设置生成路由；原环境/独立路由默认None，传输兼容未实测。没有总token/费用预算、provider实际输出截断或质量提升结论，mock不证明限制，n/重试/多库合计量另评估。依赖/类型存量（7项构建期告警/181项）保留，真实MySQL/生产provider、多进程/压测/RAG语义质量未测。原工作区pyproject.toml/docs/audits与旧未跟踪计划保留。

## Git与下一步

代码[1ac0660](https://github.com/Qorq-byte/Data_engineer/commit/1ac0660a658266aab475ad6aec0011da76f0075c)已普通推送codex/llm-token-policy，远程SHA一致；[Actions39950303020](https://github.com/Qorq-byte/Data_engineer/actions/runs/39950303020)成功且headSha精确匹配。Linux干净安装、182前端契约/构建完整性与重复性/失败保留/Ruff/strict35均通过，全量**3457 passed、1 warning，57.41秒**，uvloop实际执行；[结构化证据](results/stage-26-ci.json)。阶段26代码验收完成，远程补记文档另行提交推送并核对其CI；不合并main。下一候选核对模型响应截断/finish_reason是否在SQL生成与错误反馈中可见，先规划最小范围再实施。
