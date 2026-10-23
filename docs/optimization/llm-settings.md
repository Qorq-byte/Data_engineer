# LLM设置：持久化、运行时与恢复

阶段25起，GET /api/v1/settings/llm返回公开配置、configuration_source=database/memory及runtime={available,active_model,mock_mode}；runtime来自当前路由实例，不从default_model猜测，不由GET创建实例。聚合GET /settings的llm段使用相同视图，available-models与启动恢复也用专用加载器；读取失败503 LLM_SETTINGS_READ_FAILED，不偷偷回退旧值或默认值。没有数据库存储实例时允许明确仅内存配置。

PUT /settings/llm及DELETE /settings/llm/providers/{name}都在当前loop的llm-settings gate内依次读取候选、构建路由、持久化确认、无await同步发布路由/内存。候选是深拷贝，非法策略不留下部分修改；删除也同步更新路由，原内置提供商保护/404保持。构建或加密失败固定400 LLM_SETTINGS_PREPARE_FAILED，不先写设置；旧策略错误仍400。LLM请求参数校验固定422 LLM_SETTINGS_INVALID，不回显input/ctx或明密文key；其他设置段仍用原校验行为。

写入成功响应status=updated/deleted、persistence=persisted/memory_only、runtime_applied=true、active_model/mock_mode，以及完整公开配置/runtime。persisted表示本次存储接口确认完成；memory_only仅当前进程、重启不保留。生产路由可能按既有模型fallback选择其他可用模型或进入mock，前端展示实际active_model/mock_mode，不把配置default_model当运行结果。没有外部provider探测，因此“应用”不是模型服务健康或真实回答正确性保证。

存储读写各有10秒await截止，不包含gate排队，也不保证阻塞底层驱动及时取消。写失败503 LLM_SETTINGS_PERSIST_FAILED、persistence=unconfirmed、runtime_applied=false；本进程未发布新路由/配置。取消向上传播，不自动重试；数据库可能已经提交，不能称事务回滚。先GET核对配置和实际runtime，再显式保存决定重新应用；GET只重读配置而不发布路由。单进程内写/启动序列化，跨系统事务、跨worker广播/配置版本不在本阶段。

## 密钥与页面

API公开视图只选择已知字段，不回显api_key/api_key_enc及其他旧私有字段；前端也只接收白名单。密钥仍用JWT_SECRET派生AES-GCM加密，保持JWT_SECRET稳定才能恢复；数据库和该secret同时泄漏时不能保护密钥。base_url仍是可编辑公开配置，勿把凭据嵌入URL；本阶段未新增URL访问策略或SSRF防护。

页面保存/开关/新增/删除统一同步busy ref、防重复与严格业务响应检查，只有确认状态、持久化/运行字段及配置相符才提示成功；memory_only黄色提示重启不保留。写失败或畸形200均显示未知、隐藏编辑区域，须显式重读才能再写，不自动重试；成功/失败后清除密钥输入和新增表单敏感值，providers状态不保存明文key。读取中隐藏旧编辑区域、序号丢弃迟到响应，重读清旧成功反馈。未完成请求不会因发布新路由而被关闭；新请求使用新共享实例，旧路由的物理资源清理仍沿用现状。

启动使用同一gate从存储构建并发布，失败交由既有启动就绪逻辑记录，异常链隐藏底层敏感信息。聚合设置仍可能因其他段依赖失败，不是原子跨段快照。直接调用factory.apply_llm_settings仍是同步工厂入口，不代表绕过API后获得持久化或跨loop保护。

## 保留边界

当前工厂只消费提供商/密钥/base_url/models、default/fallback及既有环境限流配置；保存routing_strategy、temperature、max_tokens不意味着这些值已成为全查询路由/生成参数。本阶段保留既有行为，在页面/文档明确提示，后续需单独核对消费者。真实MySQL/LLM服务、多进程、性能和RAG语义质量未实测。流程与证据见[阶段25报告](stage-25.md)。
