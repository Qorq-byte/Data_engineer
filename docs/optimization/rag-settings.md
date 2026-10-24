# RAG配置生效契约与操作

阶段22起，系统设置→RAG检索只开放向量/BM25融合权重；不再把未接通字段当成可保存功能。接口仍要求受信管理员（401/403边界不变），不改变SQL权限、固定工作流或现有索引隔离。

## 读取与保存

`GET /api/v1/settings/rag`返回status=ok，顶层vector_weight/bm25_weight为已配置权重，两者和为1；configuration_source为database/memory/default。历史配置的vector_weight是已有实际读取入口，读取时据它推导互补BM25值；无效历史向量权重显示默认修复值并标configuration_valid=false，不自动保存。legacy_ignored_fields只列旧字段名，不冒充已生效值。

runtime.available表示是否存在已初始化引擎，**不是模型/存储健康探测**。可用时runtime.weights分别提供schema/metrics/documents的实际融合权重，runtime.embedding给实际provider类名、model与dimension；未初始化时weights/embedding为null。runtime.matches_configured明确三个实例是否均与配置一致。GET不会应用漂移配置或重建索引；聚合`GET /api/v1/settings`中的rag段使用同一生效视图。

`PUT /api/v1/settings/rag`仅接受bm25_weight、vector_weight。可只给一项，另一项互补；双项总和须为1（容差1e-9），范围[0,1]，拒绝字符串/布尔/非有限数值、空对象、显式null、未知字段。**兼容变化**：旧客户端提交top_k、similarity_threshold、embedding_model会得到422，不能再传整份GET响应。请求示例：`{"vector_weight":0.7}`。

成功返回status=updated、规范化权重、生效视图及两个必须核对的字段：

- persistence=persisted：存储适配器报告写入成功；memory_only：无可用存储对象，仅当前进程配置，重启不保留，不表示已持久化。
- runtime_applied=true：当前三个融合器已发布新配置；false：引擎未初始化，配置已记录但尚未应用，初始化后需重新读取确认。

只有持久化成功且运行应用为true，才可以显示完整持久生效成功。页面分别显示“已持久化”“仅本进程临时配置”“引擎未就绪”，保存前后严格核对业务字段，不将HTTP200本身当作成功。

## 失败、取消与恢复

存储读写分别使用10秒await截止；此截止不含等待配置gate的排队时间，也不保证同步驱动能及时被打断，不是响应SLO。读取失败503/detail.code=RAG_SETTINGS_READ_FAILED，不用默认值掩盖。写失败/超时503/RAG_SETTINGS_PERSIST_FAILED，persistence=unconfirmed、runtime_applied=false；不返回原始异常/凭据。前端显示未知、禁用保存，保留“重新读取配置”。不自动重试PUT。

失败或取消时，内存和运行配置不会由该请求发布；**数据库可能已经提交**，不能称已回滚。先读取配置及运行值，若存在漂移，确认后显式保存所需权重；读取不会偷偷应用。无存储时本进程临时配置与未来恢复的持久记录也可能漂移，处理方式相同。没有跨进程事务、广播、持久恢复日志或乐观版本锁；多个管理员最后完成的写入为准，其他worker需独立配置/重启协调。

引擎初始化与设置GET/PUT共享当前事件循环的rag-settings gate，持久化成功后无await发布三个融合器与内存配置。每次Schema/指标/文档检索在第一次embedding await前捕获融合器引用；在途检索继续使用原配置，新检索使用新引用。一次完整查询可能分多次调用不同检索器，不承诺整个查询跨所有调用拥有同一配置版本。只改变排序权重，不动源数据、索引内容、向量维度、已开始的embedding调用或检索隔离。

## 尚不支持的选项

Top-K仍由各调用方参数/节点配置决定；相似度没有全局统一阈值过滤器。embedding provider由RAG_EMBEDDING_PROVIDER及相应部署配置选择，运行实例显示实际结果（包括mock回退），不能从旧embedding_model字段推断。模型/维度变化必须用兼容索引或版本目录重建；本接口不切换provider。Harness/database设置的类似未接通问题单独跟进，不以本阶段RAG闭环推断全系统设置均已完成。

部署继续先执行`npm ci --ignore-scripts`、`npm run build:frontend`并与对应源码一起发布，旧标签页需刷新。全局类型债务、构建期依赖告警及真实MySQL/模型/跨进程验证限制见[阶段22报告](stage-22.md)。
