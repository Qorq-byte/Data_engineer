# 阶段53：多开进程到底有没有用——测出来，以及它撞出的一个真缺陷

2026-11-01。阶段51 收尾时留下一句结论：单请求 CPU 砍到 1/6 后吞吐仍不随 asyncio 并发增长，所以瓶颈是"受 GIL 约束的单进程 Python CPU"，要涨吞吐得把工作移出主进程。这句话的方向对，但**当时没有测量支撑**。本阶段补上测量，并把"移出主进程"的代价列清楚。

结论：**4 个独立进程在 1k 表上给出 3.2×、在 10k 表上给出 3.1× 的吞吐**，同一进程内的 asyncio 并发是 1.0×。代价是每进程一份内存、N 倍的数据库连接、以及若干"假定只有一个写者"的模块。

顺带撞出一个真缺陷：**两个进程在同一微秒启动工作流时，trace 文件名相同，后者在 Windows 上直接 `PermissionError`**。已修。

证据见 [stage-53-workers.json](results/stage-53-workers.json)。

## 1. 怎么测的

新增 `scripts/scale_workers.py`：起 N 个**独立进程**，每个进程自己建数据库连接、自己抽 Schema、自己跑和 `scripts.scale_load` 同一条 in-process pipeline（scripted router，无模型调用），彼此只共享磁盘上的 SQLite 文件。

两处刻意的设计：

- **同步起跑**：`multiprocessing.Barrier`，否则第一个进程跑完了第 N 个还没启动，量出来的是启动时间不是吞吐。
- **吞吐用共同墙钟窗口**：总请求数 / （最早开始的进程到最晚结束的进程），不是各进程耗时相加。窗口从 barrier 放行之后才计时，避免进程启动抖动被算进分母。

夹具里额外建了一张 `facts` 表：pipeline 的固定 SQL 查它，`synthetic_schema` 不生成它，`scale_load` 是靠在已注册连接上 `CREATE TABLE IF NOT EXISTS` 补的。在父进程用 `sqlite3` 建一次、worker 只读，避免 4 个进程同时 `CREATE TABLE` 撞锁。

## 2. 结果

**1k 表（每个 worker 8 个请求）**

| 进程数 | 请求数 | 共同窗口 | 吞吐 | P50 | P95 |
|---|---|---|---|---|---|
| 1 | 8 | 1.09 s | 7.34 rps | 110 ms | 220 ms |
| 2 | 16 | 1.12 s | 14.25 rps（1.94×） | 111 ms | 235 ms |
| 4 | 32 | 1.36 s | **23.60 rps（3.21×）** | 131 ms | 308 ms |

**10k 表（每个 worker 4 个请求）**

| 进程数 | 请求数 | 共同窗口 | 吞吐 | P50 | P95 |
|---|---|---|---|---|---|
| 1 | 4 | 4.61 s | 0.87 rps | 1109 ms | 1284 ms |
| 2 | 8 | 5.04 s | 1.59 rps（1.83×） | 1222 ms | 1380 ms |
| 4 | 16 | 5.99 s | **2.67 rps（3.07×）** | 1467 ms | 1685 ms |

所有 56 次尝试状态都是 `ok`（真的跑完并返回了行），不是提前失败拉高的数字。

对比阶段51 在同一台机器上的 asyncio 并发结论（10k：并发 1/2/4 的 rps 0.31/0.26/0.32，P50 却近似线性变长）——**换进程就涨，换协程就不涨**。这正是 GIL 假设的预测，现在它有数据了。

代价也能量出来：4 进程时单请求 P50 从 1109 涨到 1467 ms（+32%），因为四个进程在争同一块 CPU 和同一个 SQLite 文件。这是并行度的正常代价，不是回归。

## 3. 撞出的缺陷：trace 文件名跨进程撞车

第一次跑 1k 时，4 进程那一级在 stderr 里刷出 `Workflow trace persistence failed ... PermissionError: [Errno 13] ... wf_20261101_201639_696830.jsonl`。

原因很直白：`app/harness/runner.py` 里

```python
workflow_id = f"wf_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
```

文件名就是 `{workflow_id}.jsonl`。同一微秒启动的两个进程会产生同一个 id、同一个路径，Windows 上第二个写者拿到 `PermissionError`。单进程下这条路永远走不到，多进程第一次跑就撞上了。

修法是给 id 追加 pid：

```python
stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
return f"wf_{stamp}_{os.getpid()}"
```

对外可见的 id 只是多了后缀，UI 与审计读的仍是同一个字段。3 项测试固定住这个行为，其中一项**冻结时钟**并切换 pid，断言两条 id 字符串完全不同——不是"看起来不一样"，是具体字符串相等。

## 4. 多开进程为什么还不能直接上线

这一节是设计说明，不是已完成的工作。上面 3× 的吞吐是在一个刻意简化的环境里拿到的：每个进程独立、只读一个 SQLite 文件、不建检索索引、不发 HTTP。

| 会被 N 进程放大的东西 | 现在是什么状态 |
|---|---|
| Schema 缓存 | 每进程一份。10k 表时单进程峰值 RSS 约 470 MB（阶段51 记录），4 进程就是 4 份 |
| 检索索引目录（lancedb + bm25 sqlite） | 本阶段**刻意没建**。多进程并发读 + 单写者刷新没有验收过 |
| 数据库连接 | 单连接 lease 是**进程内**对象，N 进程 = N 倍连接数，目标库的连接预算要重算 |
| 准入闸门 | `max_concurrent_queries` 是进程内计数，多进程后实际并发上限变成 N × 配置值 |
| 状态备份 / 恢复 | 假定单一持有者：`refresh_required` 标记与 `VACUUM INTO` 快照都需要单写者 |
| trace 目录 | 命名冲突已修，但没有配额与清理策略，N 个进程往同一棵树里写 |
| 用量审计 | 有并发线程的测试，**没有并发进程的验收** |

换句话说：进程数是一个真实的杠杆，但"把副本数调大"这句话背后是上面七件事要各自有答案。本阶段只把杠杆量出来，没有假装这些答案已经有了。

## 5. 没有覆盖的

- **没有 HTTP 传输**：worker 直接在进程内调 `run_query`，序列化、连接接入、keep-alive 的开销都不在里面。
- **没有真实模型、没有 embedding**，延迟只是编排时间。
- **只有 SQLite 单文件读**；PostgreSQL/MySQL 在 N 条连接下的行为不一样。
- **只测到 4 进程**（24 核机器），没有做超订（进程数 > 核数）实验。
- 本阶段**没有跑服务端**，所以这里没有任何部署拓扑或 SLO 结论。

## 6. 复现

```bash
python -m scripts.scale_workers --output workers.json \
  --workdir .scale_probe/workers --tables 10000 --workers 1,2,4 --requests 4
```

单测：`pytest tests/test_evaluation/test_scale_workers.py tests/test_harness/test_workflow_id.py`
