# 阶段45：大Schema规模与并发负载测量

2026-10-31。本阶段只测量，不改业务代码，不承诺吞吐或 SLO。所有数字来自合成输入、单个进程、本机（Windows 11，24 逻辑核），**不含真实模型或真实 embedding**，因此是编排容量，不是端到端线上容量。

## 口径

规模维度分开声明，不把文档数当表数：

| 维度 | 1k 轮 | 10k 轮 |
|---|---|---|
| 表 | 1000 | 10000 |
| 列对象（12 列/表） | 12000 | 120000 |
| 元数据文档 | 1000 | 10000 |
| 合成库字节 | 4.39 MB | 43.84 MB |

场景分开测：冷提取（`schema_extract`）、已建索引的 keyword 检索、RAG 检索（mock 向量）、一次性索引构建（`rag_index`）、真实 Harness 编排（`pipeline`，scripted router 固定 SQL）。每层声明请求数，失败/超时/取消留在分母，`not_attempted` 单独计数。

停止策略：单场景 60 秒（10k 放宽 120 秒）、整批 20 分钟（10k 25 分钟）、RSS 2 GiB、产物 1 GiB。**两轮均未触发任何停止线**。

## 实现

- [app/evaluation/scale_probe.py](../../app/evaluation/scale_probe.py)：合成 Schema/文档、最近秩分位、停止检查、并发层级执行器、事件循环滞后与 RSS 采样。RSS 在 Windows 走 `GetProcessMemoryInfo`，Linux 走 `/proc/self/status`；读不到时返回 `None` 并显式标注，不补 0。
- [scripts/scale_load.py](../../scripts/scale_load.py)：CLI。真实 SQLite 库 + mock embedding + 私有临时索引目录；`pipeline` 走生产 `run_query`，不 monkeypatch 全局路由。
- 13 项单测覆盖规模拆分、分位口径、失败分母、场景截止、RSS 越线、RSS 可读性、产物字节。

## 结果：1k 表（1000 表 / 12000 列 / 1000 文档）

冷提取 359 ms；索引构建 463 ms（2.06 MB）。每层 20 请求。

| 场景 | 并发 | rps | P50 | P95 | 事件循环滞后 P95 |
|---|---|---|---|---|---|
| schema_extract | 1 | 2.90 | 310 ms | 521 ms | 47 ms |
| schema_extract | 2 | 2.58 | 735 ms | 907 ms | 47 ms |
| schema_extract | 5 | 2.69 | 1743 ms | 2199 ms | 39 ms |
| keyword_retrieve | 1 | 326 | 0.9 ms | 1.1 ms | 56 ms |
| keyword_retrieve | 5 | 331 | 0.9 ms | 1.0 ms | 55 ms |
| rag_retrieve | 1 | 85 | 9.4 ms | 11.4 ms | 231 ms |
| rag_retrieve | 5 | 75 | 10.7 ms | 13.3 ms | 263 ms |
| pipeline | 1 | 1.22 | 779 ms | 929 ms | 517 ms |
| pipeline | 2 | 1.21 | 1659 ms | 2041 ms | 543 ms |
| pipeline | 5 | 1.21 | 4077 ms | 4260 ms | 560 ms |

整批 109 秒，产物 6.16 MB，RSS 峰值 433 MB。

## 结果：10k 表（10000 表 / 120000 列 / 10000 文档）

冷提取 2058 ms；索引构建 2118 ms（27.54 MB）。每层 8 请求。

| 场景 | 并发 | rps | P50 | P95 | 事件循环滞后 P95 |
|---|---|---|---|---|---|
| schema_extract | 1 | 0.64 | 1523 ms | 1795 ms | 12 ms |
| schema_extract | 2 | 0.65 | 3030 ms | 3152 ms | 12 ms |
| keyword_retrieve | 1 | 21.5 | 11.9 ms | 266 ms | 367 ms |
| keyword_retrieve | 2 | 22.7 | 12.3 ms | 243 ms | 348 ms |
| rag_retrieve | 1 | 13.9 | 40.5 ms | 280 ms | 572 ms |
| rag_retrieve | 2 | 13.4 | 40.8 ms | 293 ms | 593 ms |
| pipeline | 1 | 0.10 | 9781 ms | 11650 ms | **4384 ms** |
| pipeline | 2 | 0.09 | 22307 ms | 25730 ms | **4936 ms** |

整批 370 秒，产物 68.1 MB，RSS 峰值 703 MB。

## 三个已证实的结论

**1. 吞吐不随并发增长，延迟随并发线性恶化。**

所有场景在并发 1→2→5 时 rps 基本不变，P50 近似按并发倍数增长：

- `pipeline` 1k：1.22 → 1.21 → 1.21 rps，P50 779 → 1659 → 4077 ms
- `pipeline` 10k：0.10 → 0.09 rps，P50 9781 → 22307 ms
- `schema_extract` 1k：2.90 → 2.58 → 2.69 rps，P50 310 → 735 → 1743 ms

这是**严格串行化**的形态，不是资源饱和。原因在代码里可核对：`ConnectionFactory` 的每个已注册连接由 `lease()` 单槽门控（`gate(("database", id(connection)))`，capacity 1），所有查询共用同一连接；`max_concurrent_queries=8` 的准入门控因此不构成实际并发度。多请求只是排队，等待时间直接加到 P50 上。

**2. 事件循环被同步工作阻塞。**

10k 表时 `pipeline` 的事件循环滞后 P95 达 4.4 秒（并发 2 时 4.9 秒），`rag_retrieve` 与 `keyword_retrieve` 也达到 0.35–0.59 秒。这意味着同一进程内的健康检查、取消传播、SSE 心跳、其他会话请求都会被这段同步计算拖住。1k 表时该滞后仍有 0.5 秒量级，说明这不是极端规模才出现的问题。

**3. Schema 规模对编排延迟是超线性的。**

表数、列数、文档数各放大 10 倍时，P50 的增长：

| 场景 | 1k P50 | 10k P50 | 倍率 |
|---|---|---|---|
| schema_extract | 310 ms | 1523 ms | 4.9× |
| keyword_retrieve | 0.9 ms | 11.9 ms | 13.2× |
| rag_retrieve | 9.4 ms | 40.5 ms | 4.3× |
| pipeline | 779 ms | 9781 ms | **12.6×** |

编排延迟放大 12.6 倍，明显高于输入放大倍数，指向每次请求都要对全量 Schema 做重复处理（内省/序列化/关键词匹配），而不是按需取子集。

## 容量边界（本机、单进程、单连接）

- **1k 表**：编排 P50 0.78 秒、有效吞吐 1.2 rps、RSS 433 MB。可用于低频交互，不能承受并发。
- **10k 表**：编排 P50 9.8 秒、有效吞吐 0.1 rps、P95 11.6 秒、事件循环滞后 4.4 秒。**当前实现不适合在此规模上提供在线查询**。
- 资源停止线（2 GiB RSS / 1 GiB 产物）在这些规模下远未触及，瓶颈不在内存或磁盘。

## 未测范围

- 无真实模型、无真实 embedding 服务：端到端语义延迟与 token 成本未测。
- 无混合维护负载（索引重建与查询并发），本轮不做，避免把写入与读混为一个数字。
- 单进程单 worker；多 worker 的共享状态与失效协议未设计、未验收。
- 100k 对象规模未测，按计划不在首批。
- 本机为 Windows 11 / 24 逻辑核，Linux 服务器上绝对值会不同；结论的**形态**（串行、阻塞、超线性）比绝对值更可迁移。

## 下一步（不在本阶段实施）

按"只针对已证实瓶颈改代码"的约定，后续若要提升容量，候选顺序是：

1. 解除单连接 lease 造成的查询串行（连接池或按连接分片），这是唯一被直接证明的吞吐上限。
2. 把 Schema 内省与关键词匹配移出事件循环（线程池或预计算子集），解决 4 秒级滞后。
3. 对大规模 Schema 按需取子集，而不是每请求全量处理。

以上都会改变现有并发语义与只读保证，必须独立设计、独立验收，不能作为调参混入本阶段。

证据见 [results/stage-45-scale.json](results/stage-45-scale.json)；原始逐层数据与合成库保留在被忽略的 `.scale_probe/` 目录。
