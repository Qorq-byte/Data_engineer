# NL2SQL 資料工程 Agent

**用自然語言向關聯式資料庫提問，取得可審查、可驗證、可辯護的 SQL —— 或是一句明確的「做不到」。**

[English](README.md) · [简体中文](README.zh-CN.md) · **繁體中文** · [日本語](README.ja.md)

[![MVP checks](https://github.com/Qorq-byte/Data_engineer/actions/workflows/checks.yml/badge.svg?branch=main)](https://github.com/Qorq-byte/Data_engineer/actions/workflows/checks.yml)
[![Real database acceptance](https://github.com/Qorq-byte/Data_engineer/actions/workflows/real-databases.yml/badge.svg?branch=main)](https://github.com/Qorq-byte/Data_engineer/actions/workflows/real-databases.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-3DA639.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg?logo=fastapi&logoColor=white)](app/api/)
[![Node.js 22+](https://img.shields.io/badge/Node.js-22%2B-339933.svg?logo=nodedotjs&logoColor=white)](package.json)

[![SQL: read-only](https://img.shields.io/badge/SQL-read--only-0B7285.svg)](app/security/sql_policy.py)
[![Status: pre-1.0 MVP](https://img.shields.io/badge/status-pre--1.0%20MVP-F5C451.svg)](#專案現況)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-4C9A5A.svg)](CONTRIBUTING.md)
[![Security policy](https://img.shields.io/badge/security-policy-4B5563.svg)](SECURITY.md)

<a href="assets/runtime-flow-static.svg"><img src="assets/runtime-flow.svg" width="1000" alt="精簡執行流程圖：啟動列、入口與准入列、固定 request_query 計畫、五個節點、回應卡，以及護欄與分支腳註"></a>

*生成式示意圖，不是執行紀錄。[靜態版](assets/runtime-flow-static.svg) · [圖中畫了什麼](docs/runtime-flow.md)。*

## 專案簡介

這是一個用於關聯式資料庫自然語言分析的 Agent Harness。語言模型負責撰寫 SQL；固定的 YAML 計畫與
宣告式限制決定下一步執行哪個節點、節點能碰哪些工具、最多回傳多少資料列，以及請求何時必須停止。
輸出不是分數，而是一條 SQL 加上背後的證據；某項檢查無法執行時，也會明確說明原因。

設計遵循三條規則：

- **模型不做路由。** YAML 計畫固定節點順序；`plan_id` 或 `auto_plan` 只決定執行哪份計畫，
  `WorkflowRunner` 負責執行。
- **唯讀是被強制的，不是被請求的。** 單一語句 AST 策略、`execute_sql` 內部的寫入檢查，以及驅動端
  提供的防護（SQLite authorizer、ClickHouse 唯讀 profile）各自獨立生效。
- **缺口就寫成缺口。** 跳過的執行、缺少的結構、mock 提供者與未驗證的關聯，都是明確的欄位、
  警告或 `null`，不會是無聲的成功。

這是面向單一管理員群組的 pre-1.0 MVP。[專案現況](#專案現況)列出哪些已接通、哪些只完成一部分、
哪些刻意沒有做。

## 核心特性

- **確定性的控制流程。** 所選計畫固定節點順序——預設 `request_query.yml` 為五個節點。節點之間互不呼叫，只透過
  `WorkflowContext` 溝通；節點失敗就中止，除非計畫明確宣告 `on_failure: skip`。預設計畫的
  `max_retries` 為 `0`。
- **分層唯讀。** 單一語句解析與 AST 檢查；`execute_sql` 在接觸資料庫前阻擋寫入；SQLite 與
  ClickHouse 另有驅動端防護。部署時請使用目標資料庫原生的唯讀帳號。
- **提供證據，不提供信心分數。** 每個候選 SQL 都記錄結構來源、取得時間、方言、假設，以及逐項
  檢查的通過 / 未通過 / 未驗證。`confidence` 依設計恆為 `0`，因為沒有經過校正的機率。
- **結構感知的產生。** 資料表、欄位、鍵、註解、業務詞彙表與領域規則，BM25 與向量檢索再以
  倒數排名融合（RRF）合併。
- **學習只做建議，不改寫 SQL。** 審查通過的編輯規則會成為下一次匹配查詢的提示與驗證警告；
  `applied_rules` 說明本次用了哪些規則，SQL 仍須經過驗證與唯讀執行，不會被悄悄改寫。
- **七個連接器已通過實庫驗收。** SQLite、DuckDB、PostgreSQL、MySQL、ClickHouse、Trino、StarRocks。
  Snowflake 已實作，但獨立驗收仍待完成。
- **一條流水線，三種出口。** Web 工作區、JSON/SSE API 與 MCP 工具都呼叫同一個請求服務。JSON 與
  SSE 回傳 `QueryResponse`；MCP 將相同狀態、識別碼、SQL 與錯誤投影為工具結果字典。
- **具硬性預算的提供者路由。** LiteLLM 可路由至 OpenAI、Anthropic、Google、DeepSeek、Qwen 或
  相容 OpenAI 的自建端點；`max_llm_calls_per_query`、重試、逾時與併發由 Harness 強制執行。
- **MCP 雙向使用。** 四個 FastMCP 服務將 NL2SQL 與資料工具暴露給外部用戶端；同一行程也能使用
  外部 MCP 工具。

## 專案現況

狀態以執行中的程式碼為準，不以設計文件為準。逐項矩陣見
[docs/support-matrix.md](docs/support-matrix.md)。

| 領域 | 狀態 |
|---|---|
| 固定的五節點 `request_query` 流水線 | **已實作** —— JSON、SSE 與 MCP 均已接通 |
| 約束、權限與 SQL 策略執行 | **已實作** |
| Schema 連結、多階段驗證、混合檢索 | **已實作** |
| 唯讀資料庫連接器 | **七個已通過實庫驗收**。Snowflake 已實作並通過本機策略測試，專用雲端環境驗收待完成。[驗收說明](docs/optimization/snowflake-acceptance.md) |
| 其他工作流程計畫 | **預設五個可執行** —— `gensql_agentic`、`ez_query`、`reflection`、`chat_agentic`、`explore`；`metric_query` 需設定指標目錄，`candidate_review` 需明確啟用預覽 |
| 自動計畫選擇 | **已實作** —— `auto_plan=true` 依序走 L1 規則、L2 歷史與能力過濾後的預設；預設仍為 `request_query` |
| 指標工作流程 | **已實作**。目錄 v2 支援日、週、月分組與主鍵查找維度，已在 SQLite、MySQL 和 PostgreSQL 執行測試。[指標目錄 v2](docs/optimization/remaining-implementation-roadmap-2026-11-20.md#m2-日历分组与查找表维度) |
| 候選複核與子代理 | 固定候選複核可明確預覽。子代理依租戶使用 REST、認證 MCP HTTP 和工作台，共用 worker 使用封存目錄。[子代理設定](docs/optimization/immutable-workers.md#封存共享-worker-的子-agent-目录) |
| 回饋驅動的規則學習 | **已實作** —— 審查通過的規則進入生成提示與驗證，並在 `applied_rules` 回顯；規則不會自動改寫 SQL |
| 多租戶隔離 | **部分實作** —— 單 worker 和單機不可變多 worker 支援租戶工作台、REST 和 MCP HTTP。雙 worker 已在 MySQL 和 PostgreSQL 查詢目標驗收資料來源隔離、工作階段復原與拒絕設定漂移。[部署與驗收](docs/optimization/immutable-workers.md#v2-租户发布包) |
| 品質與容量驗收 | 已實作凍結保留集評分、計畫配對擷取、租戶容量門檻及資源與帳單核驗，並通過合成情境測試。正式業務和生產驗收需要目標資料及部署。[驗收狀態](docs/optimization/remaining-implementation-roadmap-2026-11-20.md#集中实施与正式验收状态) |

## 快速開始

**環境需求：** Python 3.12+、Node.js 22+（僅用於建置前端），以及 MySQL 8.0+ 帳號庫。沒有 MySQL
時服務仍可啟動，但登入與註冊會被停用。

```bash
git clone https://github.com/Qorq-byte/Data_engineer.git
cd Data_engineer

python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -e ".[dev]" -c constraints.txt

npm ci --ignore-scripts && npm run build:frontend

cp .env.example .env                      # Windows: copy .env.example .env
# 設定 JWT_SECRET（32 字元以上）、REGISTRATION_TOKEN、AUTH_MYSQL_* 與 ADMIN_USER_IDS
python main.py                            # http://localhost:8001 · API 文件在 /docs
```

1. 開啟 <http://localhost:8001>，用 `REGISTRATION_TOKEN` 的邀請碼註冊。
2. 在工作區連接目標資料庫（或呼叫 `POST /api/v1/databases/connect`）。
3. 提問。工作區依節點串流顯示進度，SQL、驗證報告與結果表並排呈現。

沒有模型金鑰？路由會退回 mock 模式：回應結構完整，SQL 是佔位符，警告會說明這一點。沒有本機
Ollama？在 `.env` 設定 `RAG_EMBEDDING_PROVIDER=mock`，改用決定性嵌入而非語意嵌入。

## 安裝與設定

### 安裝

```bash
pip install -e ".[dev]" -c constraints.txt   # 應用程式 + 測試與 lint 工具
pip install -e ".[snowflake]"               # 可選 Snowflake 驅動（不在 constraints.txt 快照內）
```

執行期不需要 Node.js。Node 22+ 只用於 `npm run build:frontend`，將 `frontend_design/` 編譯到
`frontend_dist/`，再由 FastAPI 提供靜態檔案。

### 環境變數

將 `.env.example` 複製為 `.env`。下表是最常需要調整的變數；其餘開關在範例檔案中都有註解。

| 變數 | 預設值 | 用途 |
|---|---|---|
| `APP_HOST` / `APP_PORT` | `0.0.0.0` / `8001` | 綁定位址與連接埠 |
| `DATABASE_URL` | `sqlite:///./data/app.db` | 本機應用程式儲存（不是帳號庫） |
| `AUTH_MYSQL_HOST`、`AUTH_MYSQL_PORT`、`AUTH_MYSQL_USER`、`AUTH_MYSQL_PASSWORD`、`AUTH_MYSQL_DATABASE` | `127.0.0.1`、`3306`、`root`、空、`nl2sql_auth` | MySQL 帳號庫 |
| `JWT_SECRET` | 無 —— **必填** | 簽章金鑰；少於 32 字元或保留預設值會中止啟動 |
| `REGISTRATION_TOKEN` | 空 | `POST /api/v1/auth/register` 需要的邀請碼；留空即關閉註冊 |
| `ADMIN_USER_IDS` | `[1]` | 操作員允許清單；僅註冊不會自動取得權限 |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` / `DEEPSEEK_API_KEY` | 空 | 啟動時讀取的提供者憑證 |
| `LLM_MOCK_MODE` | `false` | 強制 mock 路由，不呼叫真實提供者 |
| `LLM_MAX_CONCURRENCY` / `LLM_TIMEOUT_SECONDS` / `LLM_RATE_LIMIT_RPM` | `4` / `30` / `60` | 單一行程的 LLM 限制 |
| `QUERY_TIMEOUT_SECONDS` | `120` | 單次查詢的 HTTP/SSE 截止時間 |
| `MAX_CONCURRENT_QUERIES` | `8` | 准入槽位；超出請求會收到 `503` |
| `MAX_RESULT_ROWS` / `STATEMENT_TIMEOUT_MS` | `1000` / `30000` | 資料列上限與單語句逾時 |
| `RAG_EMBEDDING_PROVIDER` | `ollama` | `ollama`、`openai`、`bge` 或 `mock` |
| `OLLAMA_BASE_URL` / `OLLAMA_EMBEDDING_MODEL` / `OLLAMA_EMBEDDING_DIM` | `http://127.0.0.1:11434` / `embeddinggemma` / `768` | 本機嵌入服務 |
| `LLM_AUDIT_PATH` | 空 | 可選用量稽核檔案；留空即不落盤。預設不存 SQL |
| `MCP_SERVER_HOST` / `MCP_SERVER_PORT` | `0.0.0.0` / `8080` | MCP 服務綁定 |

### 宣告式設定

| 檔案 | 作用 |
|---|---|
| `app/config/agent.yml` | 提供者預設值、節點→工具→權限對應、硬性約束 |
| `app/workflow/plans/request_query.yml` | 可執行計畫：`node_order`、`failure_policy`、評估指標 |
| `app/workflow/plans/*.yml` | 其他預設五個計畫、伺服器目錄啟用的 `metric_query` 與可選的 `candidate_review` 預覽 |
| `app/config/domains/*.yml` | 依領域的詞彙表、業務規則與術語對應 |
| `app/config/subagents/*.yaml` | 限定範圍的子代理定義與交付目標 |

`ConfigLoader` 與 `PlanLoader` 依檔案 mtime 熱更新，修改後對下一次請求生效，無需重新啟動。節點
順序仍在計畫載入時固定；單一請求不能插入或跳過節點。

模型金鑰有兩個來源：啟動時的 `.env`，或 `PUT /api/v1/settings/llm`。後者先以 AES-GCM 加密再寫入
MySQL，立即生效並跨重新啟動保留；`.env` 中的金鑰不會被回寫。

## 使用範例

### 註冊與登入

只有設定 `REGISTRATION_TOKEN` 才開放註冊。邀請碼放在請求標頭，不在請求內容中。

```bash
curl -X POST http://localhost:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -H "X-Registration-Token: <來自 .env 的值>" \
  -d '{"username":"admin","email":"admin@example.com","password":"<強密碼>"}'

curl -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"<強密碼>"}'
# → {"access_token":"…","token_type":"bearer","user":{…}}
```

### 連接目標

```bash
curl -X POST http://localhost:8001/api/v1/databases/connect \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"db_type":"sqlite","config":{"path":"data/app.db"},"alias":"demo"}'
# → {"status":"connected","db_id":"sqlite_demo_20261101143012", …}
```

查詢目標請使用資料庫原生的唯讀帳號。回傳的 `db_id` 就是查詢端點需要的目標識別碼。

### 產生與驗證

```bash
curl -X POST http://localhost:8001/api/v1/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"nl_text":"上個月收入最高的 10 個商品","domain":"ecommerce","dialect":"postgresql","database":"<db_id>","num_candidates":1,"execute":false}'
```

```jsonc
// 已精簡：巢狀內容只保留承載決策的欄位。
{
  "query_id": "q_4f1c2a9b7e01",
  "status": "completed",
  "plan_id": "request_query",
  "primary_sql": "SELECT p.name, SUM(o.amount) AS revenue FROM orders o JOIN products p ON p.id = o.product_id WHERE o.created_at >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month') GROUP BY p.name ORDER BY revenue DESC LIMIT 10",
  "linked_tables": ["orders", "products"],
  "validation": {
    "passed": true, "syntax_ok": true, "schema_valid": true, "type_valid": true, "score": 0.95
  },
  "execution": null,
  "confidence": 0.0,
  "errors": [],
  "warnings": []
}
```

把 `"execute"` 設為 `true`，驗證通過後就會執行。執行物件包含 `columns`、`rows`、`row_count` 與
`truncated`；資料列受 `MAX_RESULT_ROWS` 限制。

### 選擇工作流程計畫

`request_query` 是預設計畫。另外五個計畫走同一個介面，並共用准入、唯讀檢查、資料列上限、語句逾時與
LLM 預算：

| `plan_id` | 流水線 | 適用情境 |
|---|---|---|
| `gensql_agentic` | 連結 → 生成 → 驗證 → 執行 | 不需要 NL 解析的標準流水線 |
| `ez_query` | 連結 → 生成 → 執行 | 簡單讀取，驗證步驟可省 |
| `reflection` | 生成 → 執行 → 反射 → 修正 → 執行 | 預期會執行失敗，需要一次自動修正 |
| `chat_agentic` | 解析 → 檢索 → 生成 → 驗證 → 回覆 | 需要自然語言答覆，而不只是 SQL |
| `explore` | 解析 → 檢索 → 格式化 | 只想看表與文件，不生成 SQL |

```bash
curl -X POST http://localhost:8001/api/v1/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"nl_text":"列出訂單","database":"<db_id>","execute":true,"plan_id":"ez_query","num_candidates":1}'
```

`reflection` 必須同時提供 `execute: true` 與 `database`，否則會在建立工作階段前就失敗。
`metric_query` v1 使用固定的解析 → 驗證 → 執行計畫，不呼叫模型。
需設定伺服器 `METRIC_CATALOG_PATH`，明確指定指標名稱、版本、領域、資料庫和 `num_candidates: 1`。
未設定有效目錄時回傳 `400 INVALID_REQUEST`。共用 worker 必須在啟用的發布套件內明確封存目錄，
見[共用目錄設定與驗收](docs/optimization/shared-metric-catalog.md)。
詳見 [設定與驗收](docs/optimization/metric-query-v1.md)。

管理員可透過 `GET /api/v1/metrics?domain=...&database=...` 取得名稱和版本。
MCP 也提供 `list_metrics`、`metric_query`；HTTP Bearer 驗證與本機行程明確信任方式見
[存取與使用](docs/optimization/metric-discovery-mcp.md)。
獨立 stdio 與回環 HTTP 傳輸驗收見[指標 MCP 傳輸驗收](docs/optimization/metric-mcp-transports.md)。

子代理共用固定自然語言或指標工作流程，設定綁定租戶、`db_id` 和允許的 operator 使用者 ID，執行預設關閉。
詳見 [REST 邊界](docs/optimization/subagent-rest-boundary.md)。

把 `"auto_plan"` 設為 `true`，則由能力過濾後的選擇器在 `request_query` 與 `ez_query` 之間選擇。
回應會帶 `plan_selection`：`layer`（`L1` 規則、`L2` 歷史、`default` 或 `filtered`）、`reason`、
`confidence` 與備選計畫。選擇器不會選 `metric_query`、`reflection`、`chat_agentic` 或 `explore`；
若判決落在集合之外，會回退到 `request_query` 並在原因中說明。不需要時保持關閉，請求仍完全確定。

審查通過的學習規則會合併進生成提示，並在驗證時產生非阻斷的規則警告；回應以 `applied_rules`
列出本次實際套用的規則。規則不會改寫 SQL。

### SSE 串流

```bash
curl -N -X POST http://localhost:8001/api/v1/query/stream \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"nl_text":"按月統計收入","database":"<db_id>","execute":false}'
```

每個階段發出一個事件（`parse`、`schema`、`sql`、`validation`、`execution`），以 `done` 或
`error` 結束。用戶端取消會關閉串流。

### Python 用戶端

```python
import httpx

response = httpx.post(
    "http://localhost:8001/api/v1/query",
    headers={"Authorization": f"Bearer {token}"},
    json={"nl_text": "按月統計收入", "database": db_id, "execute": False},
    timeout=120,
)
payload = response.json()
print(payload["primary_sql"], payload["validation"]["passed"])
```

### 直接 SQL 與其他端點

| 端點 | 方法 | 用途 |
|---|---|---|
| `/api/v1/execute` | POST | 繞過 NL 流水線直接執行 SQL；仍然唯讀 |
| `/api/v1/query/generate` | POST | 只產生；不連結結構、不驗證、不執行 |
| `/api/v1/schema/{db_id}` | GET | 結構快照；`/refresh` 重新內省 |
| `/api/v1/settings/llm` | GET/PUT | 提供者與預設模型；金鑰永不回顯 |
| `/health`、`/health/ready` | GET | 存活與就緒（初始化、帳號庫、非 mock 模型、至少一個已連線資料庫） |

### 回應語意

- **`status`** 為 `completed` 或 `failed`。業務失敗仍回傳 HTTP 200；結論由狀態與 `errors[]` 承載。
- **基礎設施失敗**回傳 4xx/5xx，並帶機器可讀的 `detail.code`（`QUERY_TIMEOUT`、`QUERY_CAPACITY`、
  `SESSION_CONFLICT`、`SHARED_STATE_UNAVAILABLE`、`INTERNAL_ERROR`），能重試時附 `Retry-After`。
- **`confidence` 恆為 `0`。** 請讀 `evidence`：結構來源、假設、澄清請求與逐項檢查狀態。
- **`execution: null` 表示什麼都沒跑。** 可能是 `execute=false`、沒有連線目標，或檢查被阻擋。
  警告與節點軌跡會說明是哪一種。
- **`validation.score` 是診斷值。** 阻擋性失敗時為 `0`；其餘情況反映診斷數量，不代表正確性。

請求欄位與完整錯誤契約見 [docs/usage.md](docs/usage.md) 和
[docs/optimization/query-contract.md](docs/optimization/query-contract.md)。

## 執行流程

頂部示意圖由 `scripts/render_runtime_flow.py` 產生，描述的是實際發布的鏈路，不是錄製的執行軌跡。
圖分四步：

- **啟動** —— `main.py` → `create_app()` → lifespan 探測 → `:8001`。探測失敗只降級命名元件；
  關閉時依固定順序等待釋放。
- **進入與准入** —— 四種入口、操作員檢查、請求驗證、准入槽位、工作階段綁定與 120 秒截止時間。
- **計畫與執行** —— `request_query.yml` 固定 `node_order`，`WorkflowRunner` 依序走五個節點。
  節點只寫入 `WorkflowContext`。
- **回應** —— JSON 與 SSE 回傳 `QueryResponse`，MCP 做投影；軌跡在回覆前落盤。

護欄與失敗分支收斂成圖底兩行腳註，不再單獨佔一段：

```text
入   Web 工作區 · JSON · SSE · MCP  →  認證 · 驗證 · 綁定工作階段與目標庫
計畫 PlanLoader 讀取 request_query.yml  →  WorkflowRunner 依 node_order 推進
執行 parse_nl → schema_linking → generate_sql → validate_sql → execute_sql
出   執行：唯讀結果  ·  跳過/阻擋：明確狀態  →  QueryResponse
閘門 PermissionManager · ConstraintEnforcer · SQL 策略 · LLM 預算（每步生效）
```

繪圖說明、圖中聲明與重新產生命令見 [docs/runtime-flow.md](docs/runtime-flow.md)。

## 文件

- [使用說明](docs/usage.md) —— 帳號、目標、請求欄位、回應證據、端點。
- [架構](docs/architecture.md) —— Harness 不變量、分層、目錄結構、設計取捨。
- [支援矩陣](docs/support-matrix.md) —— 功能狀態、資料庫、模型。
- [缺口收口計畫](docs/optimization/gap-closure-plan-2026-11-16.md) —— 哪些已接線、哪些仍未做，以及多租戶與基線草案。
- [完成路徑與驗收](docs/optimization/completion-path-2026-11-17.md) —— 階段、目前證據與外部阻塞。
- [執行流程](docs/runtime-flow.md) —— 圖中聲明了什麼，以及如何重新產生。
- [查詢契約](docs/optimization/query-contract.md) —— 狀態碼與錯誤語意。
- [節點開發](docs/node-development.md) —— 節點生命週期與骨架。
- [部署與復原](docs/optimization/immutable-workers.md) —— 固定設定多 worker 的約束。

## 執行與測試

### 執行

```bash
python main.py                 # APP_PORT 可覆寫 8001
curl http://localhost:8001/health
curl http://localhost:8001/health/ready
```

初始化與關閉由 FastAPI lifespan 統一負責：目錄註冊、儲存、帳號庫、RAG 與持久化的 LLM 路由在啟動
時初始化；關閉時釋放連線、子代理、RAG 引擎與路由。

### 測試

```bash
pytest                                  # 全量（asyncio_mode=auto）
pytest -m "not real_database"           # CI 預設；不依賴外部服務
pytest tests/test_nodes/                # 單一目錄
pytest -k "test_execute"                # 依關鍵字篩選
DE_REAL_DB_TESTS=1 pytest tests/real_db # 一次性 PostgreSQL/MySQL 執行個體
```

測試使用真實 SQLite、DuckDB、LanceDB 與 FTS5，不做 mock。共享 fixture 從
`tests/fixtures/test_schema.sql` 建構電子商務結構（`users`、`products`、`orders`）。

### 品質閘門

```bash
ruff check .                            # 行寬 100、py312、E/F/I/N/W/UP/B/C4/SIM
mypy app/                               # strict；CI 核心之外仍有已知債務
npm run check:frontend                  # 頁面編譯與函式契約
npm run check:frontend:build            # 可重現建置；失敗時保留舊產物
python -m scripts.check_dependencies    # 依賴與環境一致性
python scripts/check_readmes.py         # 章節對齊、本地連結、流程圖動畫
```

CI 核心（`checks.yml`）執行同一組閘門，外加 `pytest -m 'not real_database'`。
`real-databases.yml` 啟動一次性 PostgreSQL 16 與 MySQL 8.4 服務並執行 `tests/real_db`。

### 評估與壓測

這些是離線工具，不是正確性基準：

```bash
python scripts/evaluate_sql_contracts.py     # 契約級 SQL 檢查
python scripts/evaluate_quality.py           # 合成品質報告
python scripts/run_nl2sql_acceptance.py      # 凍結集批次驗收
python scripts/run_real_db_tests.py          # 驅動級驗收
python scripts/load_test.py                  # 有界併發探測
python -m scripts.run_synthetic_baseline --output data/synthetic-baseline-20261118/aggregate.json --requests 20 --rows 1000
```

彙總入口會在單一報告記錄所有 fixture 雜湊與分母。合成套件評估的是契約一致，不是業務正確性，
結果不構成生產容量或準確率基線。見[2026-11-18 範圍與結果](docs/optimization/synthetic-quality-baseline-2026-11-18.md)。

## 目錄結構

```text
app/api/             create_app() 工廠、13 個路由、操作員相依、靜態前端
app/workflow/        請求服務、計畫執行期與選擇、QueryResponse 契約、7 個 YAML 計畫
app/harness/         PlanLoader · WorkflowRunner · 權限 · 約束 · 工作階段
app/nodes/           12 個已註冊節點；request_query 的五個是主鏈路
app/core/            NL 解析、提示建構、SQL 產生與驗證
app/db/              連線、結構內省、方言適配器、固定的查詢目標
app/rag/             LanceDB 向量、BM25/FTS5、RRF 融合、索引維護
app/knowledge/       Schema 圖、詞彙表、業務規則、檢索原語
app/learning/        回饋收集、編輯模式分析、已審查規則儲存
app/llm/             LiteLLM 路由、預算、完成策略、用量、金鑰加密
app/auth/            JWT 簽發、MySQL 使用者庫、管理員閘門
app/mcp/             四個 FastMCP 服務與外部工具用戶端
app/evaluation/      凍結驗收集、契約套件、預測匯出
assets/              執行流程圖（動態與靜態）
docs/                使用、架構、支援矩陣、逐階段工程紀錄
frontend_design/     來源頁面，由 npm 編譯到 frontend_dist/
scripts/             建置、評估、驗收與繪圖入口
tests/               單元、整合、e2e 與 real_db 套件
```

## 常見問題

**需要付費模型金鑰嗎？**
不需要。沒有金鑰時路由使用 mock 模式：流水線能端到端跑通，SQL 是佔位符，`warnings` 會寫
`MOCK LLM`。適合驗證 Harness，不適合當作業務答案。

**一定要安裝 MySQL 嗎？**
帳號功能需要。帳號庫與查詢目標是兩套東西。`init_auth_db()` 失敗時服務仍會啟動，但登入與註冊
會被停用。

**為什麼 `confidence` 恆為 `0`？**
因為沒有測過經校正的機率，報一個數字就是編造精度。請看每個候選的 `evidence`。

**`status` 是 `completed`，但 `execution` 是 `null`，是 bug 嗎？**
不是。`execute=false`、沒有 `db_id`，或執行節點被跳過都會這樣。節點結果是 `skipped`，執行物件
保持 `null`，警告會說明缺少哪一環。

**哪些資料庫真的驗證過？**
SQLite、DuckDB、PostgreSQL、MySQL、ClickHouse、Trino、StarRocks 有唯讀驗收。Snowflake 有實作
但待獨立驗收；BigQuery、Redshift、Databricks 只有方言定義。

**一套部署能給多個團隊共用嗎？**
單 worker 和單機不可變多 worker 部署可按租戶開放查詢、領域和模型設定，由部署端預先設定資料來源。不可變模式透過停機維護更新設定。系統管理由預設租戶管理員操作。[設定說明](docs/usage.md#tenant-operator-setup)

**為什麼啟動時報 JWT 錯誤？**
`JWT_SECRET` 缺少、仍是 `change-me-in-production`，或不足 32 字元。這是 `create_app()` 中
刻意的 fail-closed 檢查。

**怎麼新增節點或計畫？**
讀 [CONTRIBUTING.md](CONTRIBUTING.md) 與 [docs/node-development.md](docs/node-development.md)。
節點實作 `setup_input → execute → update_context`；計畫是帶固定 `node_order` 的 YAML。

**Windows 要注意什麼？**
用 `.venv\Scripts\activate` 啟用，用 `copy .env.example .env` 複製，curl 範例建議在 PowerShell
或 Git Bash 執行。Windows 會自動安裝 `tzdata`。

## 貢獻

歡迎貢獻，尤其是能鎖定失敗語意的測試。先讀 [CONTRIBUTING.md](CONTRIBUTING.md)：環境、分支模型、
提交慣例，以及新增節點 / 計畫的骨架。參與即表示同意[行為準則](CODE_OF_CONDUCT.md)。

修改查詢鏈路時，請在同一次提交跑完上述閘門，並同步計畫、流程圖文案與四份語言的 README。

## 安全性

請依 [SECURITY.md](SECURITY.md) 私下回報漏洞，不要在公開 Issue、PR 或討論區張貼。回報中不要放
真實憑證、連線字串或客戶資料，請先脫敏。

與安全相關的預設：

- 唯讀 SQL 由單一語句 AST 策略與 `execute_sql` 內部的獨立寫入檢查共同強制；SQLite 與 ClickHouse
  另有驅動端防護，部署時應使用資料庫原生的唯讀帳號。
- `JWT_SECRET` 不足 32 字元會中止啟動。
- 透過工作區設定的提供者金鑰先經 AES-GCM 加密再落庫，任何端點都不會回顯。
- 可選用量稽核預設不存問題文字，也不存 SQL。
- 未列出的工具由 `PermissionManager` 預設拒絕；資料列、重試、逾時與 LLM 呼叫上限不受模型控制。

支援版本、範圍與揭露流程見 [SECURITY.md](SECURITY.md)。

## 授權條款

[MIT](LICENSE) © 2026 Majiaqi。
