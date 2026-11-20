# NL2SQL データエンジニアリングエージェント

**リレーショナルデータベースに自然言語で問い合わせ、検証・説明可能な SQL を取得する —— できない場合は、その理由を明示します。**

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · **日本語**

[![MVP checks](https://github.com/Qorq-byte/Data_engineer/actions/workflows/checks.yml/badge.svg?branch=main)](https://github.com/Qorq-byte/Data_engineer/actions/workflows/checks.yml)
[![Real database acceptance](https://github.com/Qorq-byte/Data_engineer/actions/workflows/real-databases.yml/badge.svg?branch=main)](https://github.com/Qorq-byte/Data_engineer/actions/workflows/real-databases.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-3DA639.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg?logo=fastapi&logoColor=white)](app/api/)
[![Node.js 22+](https://img.shields.io/badge/Node.js-22%2B-339933.svg?logo=nodedotjs&logoColor=white)](package.json)

[![SQL: read-only](https://img.shields.io/badge/SQL-read--only-0B7285.svg)](app/security/sql_policy.py)
[![Status: pre-1.0 MVP](https://img.shields.io/badge/status-pre--1.0%20MVP-F5C451.svg)](#プロジェクトの状況)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-4C9A5A.svg)](CONTRIBUTING.md)
[![Security policy](https://img.shields.io/badge/security-policy-4B5563.svg)](SECURITY.md)

<a href="assets/runtime-flow-static.svg"><img src="assets/runtime-flow.svg" width="1000" alt="簡潔なランタイムフロー：起動行、入口と受け入れの行、固定された request_query プラン、五つのノード、応答カード、ガードと分岐の脚注"></a>

*生成された図であり、実行記録ではありません。[静止版](assets/runtime-flow-static.svg) · [図が示す内容](docs/runtime-flow.md)。*

## 概要

これはリレーショナルデータベースに対する自然言語分析のためのエージェントハーネスです。言語モデルが
SQL を書き、固定の YAML プランと宣言的な制限が、次にどのステップを実行するか、どのツールに触れられる
か、最大何行返すか、いつ停止するかを決めます。出力はスコアではなく、SQL 文とその根拠、そして検査
できなかった場合の明示的な理由です。

設計は三つの原則に従います：

- **モデルはルーティングしない。** YAML プランがノード順を固定し、`plan_id` または `auto_plan` は
  どのプランを使うかだけを決め、`WorkflowRunner` が実行します。
- **読み取り専用は要求ではなく強制。** 単一ステートメントの AST ポリシー、`execute_sql` 内の書き込み
  検査、ドライバが提供するガード（SQLite authorizer、ClickHouse の読み取り専用プロファイル）が
  それぞれ独立して機能します。
- **欠落は欠落として報告。** スキップされた実行、欠けたスキーマ、mock プロバイダ、未検証の結合は、
  明示的なフィールド、警告、`null` になります。無言の成功にはしません。

単一の管理者グループ向けの pre-1.0 MVP です。[プロジェクトの状況](#プロジェクトの状況)に、接続済み・
部分実装・意図的に未実装の項目をまとめています。

## 主な機能

- **決定的な制御フロー。** 選択したプランがノード順を固定します（既定の `request_query.yml` は五つ）。
  ノード同士は呼び出さず、`WorkflowContext` だけで通信します。失敗したノードは、プランが
  `on_failure: skip` を宣言しない限り中止します。既定プランの `max_retries` は `0` です。
- **多層の読み取り専用。** 単一ステートメントを解析して AST 検査します。`execute_sql` はデータベース
  に触れる前に書き込みを遮断します。SQLite と ClickHouse はドライバ側のガードを追加します。対象
  データベースではネイティブの読み取り専用アカウントを使用してください。
- **信頼度スコアではなくエビデンス。** 各候補は構造の出所、取得時刻、ダイアレクト、前提、検査ごとの
  合格 / 不合格 / 未検証を持ちます。校正済み確率が存在しないため、`confidence` は設計上 `0` です。
- **スキーマ認識の生成。** テーブル、カラム、キー、コメント、ビジネス用語集、ドメインルール。BM25 と
  ベクトル検索を逆順位融合（RRF）で統合します。
- **学習は提案のみ、SQL は書き換えない。** 承認済みの編集パターンは次に一致するクエリのプロンプトと
  検証警告になります。`applied_rules` が適用ルールを示し、SQL は検証と読み取り専用実行をそのまま
  経由します。
- **実データベースで受け入れ済みの七つのコネクタ。** SQLite、DuckDB、PostgreSQL、MySQL、ClickHouse、
  Trino、StarRocks。Snowflake は実装済みですが、独立した受け入れは未完了です。
- **一つのパイプライン、三つの出口。** Web ワークスペース、JSON/SSE API、MCP ツールは同じリクエスト
  サービスを呼びます。JSON と SSE は `QueryResponse` を返し、MCP は同じステータス、識別子、SQL、
  エラーをツール結果の辞書に投影します。
- **厳格な予算付きのプロバイダルーティング。** LiteLLM は OpenAI、Anthropic、Google、DeepSeek、Qwen、
  または OpenAI 互換の独自エンドポイントにルーティングします。`max_llm_calls_per_query`、リトライ、
  タイムアウト、同時実行はハーネスが強制します。
- **MCP の双方向利用。** 四つの FastMCP サーバーが NL2SQL とデータツールを外部クライアントに公開し、
  同じプロセスが外部 MCP ツールを利用できます。

## プロジェクトの状況

状況は設計文書ではなく実行中のコードを反映します。機能ごとの一覧は
[docs/support-matrix.md](docs/support-matrix.md) にあります。

| 領域 | 状況 |
|---|---|
| 固定された五ノードの `request_query` パイプライン | **実装済み** —— JSON、SSE、MCP に接続 |
| 制約、権限、SQL ポリシーの適用 | **実装済み** |
| スキーマリンク、多段階検証、ハイブリッド検索 | **実装済み** |
| 七つの読み取り専用データベースコネクタ | **実装・テスト済み**。Snowflake は受け入れ待ち |
| 残り六つのワークフロープラン | **既定で五つが実行可能** —— `gensql_agentic`、`ez_query`、`reflection`、`chat_agentic`、`explore`。決定的な `metric_query` v1 はカタログ設定と明示的な指標名が必要 |
| 自動プラン選択 | **実装済み** —— `auto_plan=true` で L1 ルール、L2 履歴、能力フィルタ後の既定の順に選択。既定は `request_query` |
| マルチエージェントオーケストレーターとサブエージェント | **一部実装** —— スコープ付き REST クエリはアクセス確認と固定ワークフローを使用。自律的な協調は実験段階 |
| フィードバック駆動のルール学習 | **実装済み** —— 承認済みルールは生成プロンプトと検証に反映され、`applied_rules` で返却。ルールが SQL を書き換えることはない |
| マルチテナント分離 | **一部実装** —— 単一ワーカーおよび単一ホストの固定構成で、テナント別のワークベンチ、REST、MCP HTTP をサポート。[設定手順](docs/usage.md#tenant-operator-setup) |
| 合成・校正済みベースライン | **合成ベースラインを記録済み**。検索、SQL 契約、負荷を検証。実業務の精度、再現率、本番容量の校正は今後の検証項目。[検証結果](docs/optimization/synthetic-quality-baseline-2026-11-18.md) |

## クイックスタート

**要件：** Python 3.12+、Node.js 22+（フロントエンドのビルドのみ）、MySQL 8.0+ のアカウントストア。
MySQL がなくてもサーバーは起動しますが、ログインと登録は無効になります。

```bash
git clone https://github.com/Qorq-byte/Data_engineer.git
cd Data_engineer

python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -e ".[dev]" -c constraints.txt

npm ci --ignore-scripts && npm run build:frontend

cp .env.example .env                      # Windows: copy .env.example .env
# JWT_SECRET（32 文字以上）、REGISTRATION_TOKEN、AUTH_MYSQL_*、ADMIN_USER_IDS を設定
python main.py                            # http://localhost:8001 · API ドキュメントは /docs
```

1. <http://localhost:8001> を開き、`REGISTRATION_TOKEN` の招待トークンで登録します。
2. ワークスペースで対象データベースに接続します（または `POST /api/v1/databases/connect`）。
3. 質問します。ワークスペースはノードごとにイベントをストリームし、SQL、検証レポート、結果テーブル
   を並べて表示します。

モデルキーがない場合、ルーターは mock モードにフォールバックします。応答は整っていますが SQL は
プレースホルダーで、警告がその旨を示します。ローカル Ollama がない場合は `.env` で
`RAG_EMBEDDING_PROVIDER=mock` を設定し、意味的埋め込みの代わりに決定論的埋め込みを使います。

## インストールと設定

### インストール

```bash
pip install -e ".[dev]" -c constraints.txt   # アプリケーション + テストと lint ツール
pip install -e ".[snowflake]"               # 任意の Snowflake ドライバ（constraints.txt 外）
```

実行時に Node.js は不要です。Node 22+ は `npm run build:frontend` でのみ使われ、
`frontend_design/` を `frontend_dist/` にコンパイルします。FastAPI がその静的ファイルを配信します。

### 環境変数

`.env.example` を `.env` にコピーします。下表は変更する可能性が高い変数です。残りのスイッチは
サンプルファイルにコメント付きで記載されています。

| 変数 | デフォルト | 用途 |
|---|---|---|
| `APP_HOST` / `APP_PORT` | `0.0.0.0` / `8001` | バインドアドレスとポート |
| `DATABASE_URL` | `sqlite:///./data/app.db` | ローカルアプリケーションストア（アカウントストアではない） |
| `AUTH_MYSQL_HOST`、`AUTH_MYSQL_PORT`、`AUTH_MYSQL_USER`、`AUTH_MYSQL_PASSWORD`、`AUTH_MYSQL_DATABASE` | `127.0.0.1`、`3306`、`root`、空、`nl2sql_auth` | MySQL アカウントストア |
| `JWT_SECRET` | なし —— **必須** | 署名キー。32 文字未満またはデフォルト値のままだと起動を中止 |
| `REGISTRATION_TOKEN` | 空 | `POST /api/v1/auth/register` に必要な招待トークン。空なら登録を閉鎖 |
| `ADMIN_USER_IDS` | `[1]` | オペレーターの許可リスト。登録だけでは権限を付与しない |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` / `DEEPSEEK_API_KEY` | 空 | 起動時に読み込むプロバイダ認証情報 |
| `LLM_MOCK_MODE` | `false` | mock ルーターを強制し、プロバイダを呼ばない |
| `LLM_MAX_CONCURRENCY` / `LLM_TIMEOUT_SECONDS` / `LLM_RATE_LIMIT_RPM` | `4` / `30` / `60` | プロセス単位の LLM 制限 |
| `QUERY_TIMEOUT_SECONDS` | `120` | クエリ一回の HTTP/SSE 期限 |
| `MAX_CONCURRENT_QUERIES` | `8` | 受け入れスロット。超過リクエストは `503` |
| `MAX_RESULT_ROWS` / `STATEMENT_TIMEOUT_MS` | `1000` / `30000` | 行数上限とステートメント単位のタイムアウト |
| `RAG_EMBEDDING_PROVIDER` | `ollama` | `ollama`、`openai`、`bge`、`mock` |
| `OLLAMA_BASE_URL` / `OLLAMA_EMBEDDING_MODEL` / `OLLAMA_EMBEDDING_DIM` | `http://127.0.0.1:11434` / `embeddinggemma` / `768` | ローカル埋め込みエンドポイント |
| `LLM_AUDIT_PATH` | 空 | 任意の利用監査ファイル。空なら永続化しない。デフォルトでは SQL を保存しない |
| `MCP_SERVER_HOST` / `MCP_SERVER_PORT` | `0.0.0.0` / `8080` | MCP サーバーのバインド |

### 宣言型設定

| ファイル | 役割 |
|---|---|
| `app/config/agent.yml` | プロバイダのデフォルト、ノード→ツール→権限の対応、厳格な制約 |
| `app/workflow/plans/request_query.yml` | 実行可能なプラン：`node_order`、`failure_policy`、評価指標 |
| `app/workflow/plans/*.yml` | 追加の六つのプラン。既定で五つが実行可能、サーバー指標カタログで `metric_query` v1 を有効化 |
| `app/config/domains/*.yml` | ドメインごとの用語集、ビジネスルール、用語対応 |
| `app/config/subagents/*.yaml` | 範囲を限定したサブエージェント定義と配信先 |

`ConfigLoader` と `PlanLoader` はファイルの mtime でホットリロードするため、編集は再起動なしで次の
リクエストに反映されます。ノード順はプラン読み込み時に固定され、リクエストがノードを挿入・スキップ
することはできません。

プロバイダキーには二つの出所があります。起動時の `.env`、または `PUT /api/v1/settings/llm` です。
後者は AES-GCM で暗号化してから MySQL に保存し、即時反映され再起動後も残ります。`.env` のキーが
書き戻されることはありません。

## 使用例

### 登録とログイン

`REGISTRATION_TOKEN` が設定されている場合のみ登録できます。招待トークンはリクエストヘッダーで送り、
本文には入れません。

```bash
curl -X POST http://localhost:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -H "X-Registration-Token: <.env の値>" \
  -d '{"username":"admin","email":"admin@example.com","password":"<強力なパスワード>"}'

curl -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"<強力なパスワード>"}'
# → {"access_token":"…","token_type":"bearer","user":{…}}
```

### 対象データベースへの接続

```bash
curl -X POST http://localhost:8001/api/v1/databases/connect \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"db_type":"sqlite","config":{"path":"data/app.db"},"alias":"demo"}'
# → {"status":"connected","db_id":"sqlite_demo_20261101143012", …}
```

対象データベースではネイティブの読み取り専用アカウントを使用してください。返された `db_id` が
クエリエンドポイントに渡す識別子です。

### 生成と検証

```bash
curl -X POST http://localhost:8001/api/v1/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"nl_text":"先月の売上上位 10 商品","domain":"ecommerce","dialect":"postgresql","database":"<db_id>","num_candidates":1,"execute":false}'
```

```jsonc
// 抜粋：判定を担うフィールドだけを残しています。
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

`"execute"` を `true` にすると、検証通過後に実行されます。実行オブジェクトには `columns`、`rows`、
`row_count`、`truncated` が含まれ、行数は `MAX_RESULT_ROWS` で制限されます。

### ワークフロープランを選ぶ

既定は `request_query` です。残り五つのプランは同じエンドポイントで実行され、受け入れ、読み取り専用
チェック、行数上限、ステートメント期限、LLM 予算を共有します。

| `plan_id` | パイプライン | 使う場面 |
|---|---|---|
| `gensql_agentic` | リンク → 生成 → 検証 → 実行 | NL 解析を除いた標準パイプライン |
| `ez_query` | リンク → 生成 → 実行 | 検証を省ける単純な読み取り |
| `reflection` | 生成 → 実行 → 反射 → 修正 → 実行 | 実行エラーを想定し、一度だけ自動修正したい |
| `chat_agentic` | 解析 → 検索 → 生成 → 検証 → 応答 | SQL だけでなく自然言語の回答が欲しい |
| `explore` | 解析 → 検索 → 整形 | テーブルと文書の探索だけをしたい |

```bash
curl -X POST http://localhost:8001/api/v1/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"nl_text":"注文を一覧表示","database":"<db_id>","execute":true,"plan_id":"ez_query","num_candidates":1}'
```

`reflection` は `execute: true` と `database` の両方が必要で、欠けるとセッション作成前に失敗します。
`metric_query` v1 は解決 → 検証 → 実行の固定プランを使い、モデルを呼び出しません。
サーバー側の `METRIC_CATALOG_PATH`、明示的な指標名・バージョン・ドメイン・データベースと
`num_candidates: 1` が必要です。有効なカタログがない場合は `400 INVALID_REQUEST` です。
共有 worker には稼働中のリリースに封印されたカタログが必要です。
[共有カタログの設定と検証](docs/optimization/shared-metric-catalog.md)を参照してください。
[設定と受け入れ](docs/optimization/metric-query-v1.md)を参照してください。

管理者用 `GET /api/v1/metrics?domain=...&database=...` で指標名とバージョンを取得できます。
MCP も `list_metrics` と `metric_query` を提供します。HTTP Bearer 認証とローカルプロセスの明示的な信頼は
[アクセスと使用方法](docs/optimization/metric-discovery-mcp.md)を参照してください。
独立 stdio とループバック HTTP の検証は[メトリクス MCP トランスポート検証](docs/optimization/metric-mcp-transports.md)に記録しています。

サブエージェント REST は同じ五ノードの固定ワークフローを使用します。明示的な `db_id` とユーザー ID の許可が必要で、
実行は既定で無効です。[REST の範囲](docs/optimization/subagent-rest-boundary.md)を参照してください。

`"auto_plan"` を `true` にすると、能力で絞り込んだ選択器が `request_query` と `ez_query` の間で
選びます。応答には `plan_selection` が付き、`layer`（`L1` ルール、`L2` 履歴、`default`、
`filtered`）、`reason`、`confidence`、代替プランを含みます。`metric_query`、`reflection`、
`chat_agentic`、`explore` は選ばれず、集合外の判定は `request_query` に戻して理由を残します。
不要なときは無効のままにでき、リクエストは完全に決定的です。

承認済みの学習ルールは生成プロンプトに統合され、検証時に非ブロッキングのルール警告を出します。
応答の `applied_rules` が実際に適用したルールを示します。ルールが SQL を書き換えることはありません。

### SSE ストリーム

```bash
curl -N -X POST http://localhost:8001/api/v1/query/stream \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"nl_text":"月別の売上","database":"<db_id>","execute":false}'
```

各段階でイベント（`parse`、`schema`、`sql`、`validation`、`execution`）を送出し、`done` または
`error` で終了します。クライアントがキャンセルするとストリームは閉じられます。

### Python クライアント

```python
import httpx

response = httpx.post(
    "http://localhost:8001/api/v1/query",
    headers={"Authorization": f"Bearer {token}"},
    json={"nl_text": "月別の売上", "database": db_id, "execute": False},
    timeout=120,
)
payload = response.json()
print(payload["primary_sql"], payload["validation"]["passed"])
```

### 直接 SQL とその他のエンドポイント

| エンドポイント | メソッド | 用途 |
|---|---|---|
| `/api/v1/execute` | POST | NL パイプラインを迂回して SQL を直接実行。読み取り専用のまま |
| `/api/v1/query/generate` | POST | 生成のみ。スキーマリンク、検証、実行はしない |
| `/api/v1/schema/{db_id}` | GET | スキーマスナップショット。`/refresh` で再内省 |
| `/api/v1/settings/llm` | GET/PUT | プロバイダとデフォルトモデル。キーは返さない |
| `/health`、`/health/ready` | GET | 生存と準備完了（初期化、認証、非 mock モデル、接続済み DB） |

### 応答の意味

- **`status`** は `completed` または `failed`。業務上の失敗でも HTTP 200 を返し、判定は状態と
  `errors[]` が担います。
- **インフラ障害**は 4xx/5xx と機械可読な `detail.code`（`QUERY_TIMEOUT`、`QUERY_CAPACITY`、
  `SESSION_CONFLICT`、`SHARED_STATE_UNAVAILABLE`、`INTERNAL_ERROR`）を返し、待機が有効な場合は
  `Retry-After` を付けます。
- **`confidence` は常に `0`。** 代わりに `evidence` を読んでください。構造の出所、前提、確認要求、
  検査ごとの状態が入っています。
- **`execution: null` は何も実行していないことを意味します。** `execute=false`、接続先なし、検査に
  よる遮断のいずれかです。警告とノードトレースがどれかを示します。
- **`validation.score` は診断値。** 遮断的な失敗では `0`、それ以外は診断件数を反映するだけで、
  正しさを表すものではありません。

リクエストフィールドと完全なエラー契約は [docs/usage.md](docs/usage.md) と
[docs/optimization/query-contract.md](docs/optimization/query-contract.md) にあります。

## ランタイムフロー

冒頭の図は `scripts/render_runtime_flow.py` が生成し、実際に出荷される経路を説明するもので、記録された
トレースではありません。四つの段階で読めます：

- **起動** —— `main.py` → `create_app()` → lifespan のプローブ → `:8001`。プローブ失敗は名前付き
  コンポーネントの縮退にとどまり、終了時は固定順で解放を待ちます。
- **入口と受け入れ** —— 四つの入口、オペレーターゲート、リクエスト検証、受け入れスロット、セッション
  結合、120 秒の期限。
- **プランと実行** —— `request_query.yml` が `node_order` を固定し、`WorkflowRunner` が五つのノードを
  順に実行します。ノードは `WorkflowContext` にだけ書き込みます。
- **応答** —— JSON と SSE は `QueryResponse` を返し、MCP はそれを投影します。トレースは応答前に
  永続化されます。

ガードと失敗分岐は図下部の二行の脚注にまとめています：

```text
入   Web ワークスペース · JSON · SSE · MCP  →  認証 · 検証 · セッションと対象の結合
計画 PlanLoader が request_query.yml を読む  →  WorkflowRunner が node_order を所有
実行 parse_nl → schema_linking → generate_sql → validate_sql → execute_sql
出   実行：読み取り専用の行  ·  スキップ/遮断：明示的な状態  →  QueryResponse
ゲート PermissionManager · ConstraintEnforcer · SQL ポリシー · LLM 予算（各ステップ）
```

描画の説明、図が主張する内容、再生成コマンドは [docs/runtime-flow.md](docs/runtime-flow.md) にあります。

## ドキュメント

- [使用法](docs/usage.md) —— アカウント、接続先、リクエストフィールド、応答の根拠、エンドポイント。
- [アーキテクチャ](docs/architecture.md) —— ハーネスの不変条件、レイヤー、リポジトリ構成、設計判断。
- [サポートマトリクス](docs/support-matrix.md) —— 機能状況、データベース、モデル。
- [ギャップ収束計画](docs/optimization/gap-closure-plan-2026-11-16.md) —— 接線済みと未接線の領域、マルチテナントとベースラインの草案。
- [完成経路と受け入れ](docs/optimization/completion-path-2026-11-17.md) —— 各段階、検証の根拠と外部条件。
- [ランタイムフロー](docs/runtime-flow.md) —— 図が何を主張するか、再生成の方法。
- [クエリ契約](docs/optimization/query-contract.md) —— ステータスコードとエラーの意味。
- [ノード開発](docs/node-development.md) —— ノードのライフサイクルと雛形。
- [デプロイと復旧](docs/optimization/immutable-workers.md) —— 固定構成ワーカーの制約。

## 実行とテスト

### 実行

```bash
python main.py                 # APP_PORT で 8001 を上書き可能
curl http://localhost:8001/health
curl http://localhost:8001/health/ready
```

初期化と終了は FastAPI の lifespan が一元管理します。カタログ、ストレージ、認証、RAG、永続化された
LLM ルーターを起動時に初期化し、終了時には接続、サブエージェント、RAG エンジン、ルーターを閉じます。

### テスト

```bash
pytest                                  # 全体（asyncio_mode=auto）
pytest -m "not real_database"           # CI のデフォルト。外部サービス不要
pytest tests/test_nodes/                # 単一ディレクトリ
pytest -k "test_execute"                # キーワードで絞り込み
DE_REAL_DB_TESTS=1 pytest tests/real_db # 使い捨ての PostgreSQL/MySQL インスタンス
```

テストは mock ではなく実際の SQLite、DuckDB、LanceDB、FTS5 を使います。共有フィクスチャは
`tests/fixtures/test_schema.sql` から E コマースのスキーマ（`users`、`products`、`orders`）を構築します。

### 品質ゲート

```bash
ruff check .                            # 行幅 100、py312、E/F/I/N/W/UP/B/C4/SIM
mypy app/                               # strict。CI 核心外には既知の負債あり
npm run check:frontend                  # ページのコンパイルと関数契約
npm run check:frontend:build            # 再現可能なビルド。失敗時は旧成果物を保持
python -m scripts.check_dependencies    # 依存関係と環境の整合性
python scripts/check_readmes.py         # 章の対応、ローカルリンク、図のアニメーション
```

CI 核心（`checks.yml`）は同じゲートに加えて `pytest -m 'not real_database'` を実行します。
`real-databases.yml` は使い捨ての PostgreSQL 16 と MySQL 8.4 を起動し、`tests/real_db` を実行します。

### 評価と負荷プローブ

これらはオフラインツールであり、正しさのベンチマークではありません：

```bash
python scripts/evaluate_sql_contracts.py     # 契約レベルの SQL 検査
python scripts/evaluate_quality.py           # 合成品質レポート
python scripts/run_nl2sql_acceptance.py      # 固定セットのバッチ受け入れ
python scripts/run_real_db_tests.py          # ドライバレベルの受け入れ
python scripts/load_test.py                  # 有界な同時実行プローブ
python -m scripts.run_synthetic_baseline --output data/synthetic-baseline-20261118/aggregate.json --requests 20 --rows 1000
```

集約入口はすべての fixture ハッシュと分母を一つのレポートに記録します。合成スイートは契約一致を評価するもので、
業務上の正しさではなく、本番の容量や精度のベースラインにはなりません。詳細は[2026-11-18 の範囲と結果](docs/optimization/synthetic-quality-baseline-2026-11-18.md)を参照してください。

## リポジトリ構成

```text
app/api/             create_app() ファクトリ、13 ルーター、オペレーター依存、静的フロントエンド
app/workflow/        リクエストサービス、プラン実行と選択、QueryResponse 契約、7 つの YAML プラン
app/harness/         PlanLoader · WorkflowRunner · 権限 · 制約 · セッション
app/nodes/           登録済み 12 ノード。request_query の五つが主経路
app/core/            NL 解析、プロンプト構築、SQL 生成と検証
app/db/              接続、スキーマ内省、ダイアレクトアダプタ、固定クエリターゲット
app/rag/             LanceDB ベクトル、BM25/FTS5、RRF 融合、インデックス保守
app/knowledge/       スキーマグラフ、用語集、ビジネスルール、検索プリミティブ
app/learning/        フィードバック収集、編集パターン分析、承認済みルールストア
app/llm/             LiteLLM ルーター、予算、完了ポリシー、利用量、キー暗号化
app/auth/            JWT 発行、MySQL ユーザーストア、管理者ゲート
app/mcp/             四つの FastMCP サーバーと外部ツールクライアント
app/evaluation/      固定受け入れセット、契約スイート、予測エクスポート
assets/              ランタイムフロー図（アニメーションと静止）
docs/                使用法、アーキテクチャ、サポートマトリクス、段階ごとの記録
frontend_design/     ソースページ。npm で frontend_dist/ にコンパイル
scripts/             ビルド、評価、受け入れ、作図の入口
tests/               ユニット、統合、e2e、real_db スイート
```

## よくある質問

**有料のモデルキーは必要ですか？**
いいえ。キーがない場合、ルーターは mock モードを使います。パイプラインは端から端まで動きますが、
SQL はプレースホルダーで、`warnings` に `MOCK LLM` と出ます。ハーネスの検証には向きますが、業務の
回答には使えません。

**MySQL は必須ですか？**
アカウント機能には必要です。アカウントストアとクエリ対象は別物です。`init_auth_db()` が失敗しても
サーバーは起動しますが、ログインと登録は無効になります。

**なぜ `confidence` は常に `0` なのですか？**
校正済みの確率を測っていないからです。数値を出せば精度を捏造することになります。各候補の
`evidence` を読んでください。

**`status` が `completed` なのに `execution` が `null` なのはバグですか？**
いいえ。`execute=false`、`db_id` なし、実行ノードのスキップで起こります。ノードの結果は `skipped`、
実行オブジェクトは `null` のままで、警告が欠けた対象を示します。

**実際に検証済みのデータベースはどれですか？**
SQLite、DuckDB、PostgreSQL、MySQL、ClickHouse、Trino、StarRocks が読み取り専用の受け入れ済みです。
Snowflake は実装済みですが独立した受け入れ待ち、BigQuery、Redshift、Databricks はダイアレクト定義
のみです。

**複数のチームで一つのデプロイを共有できますか？**
単一ワーカーおよび単一ホストの固定構成では、データソースを登録してテナント別のクエリ、ドメイン、モデル設定を有効化できます。固定構成の設定は停止中に更新します。システム管理は既定テナントの管理者が利用できます。[設定手順](docs/usage.md#tenant-operator-setup)

**起動時に JWT エラーが出るのはなぜですか？**
`JWT_SECRET` が未設定、`change-me-in-production` のまま、または 32 文字未満です。`create_app()` の
意図的な fail-closed 検査です。

**ノードやプランを追加するには？**
[CONTRIBUTING.md](CONTRIBUTING.md) と [docs/node-development.md](docs/node-development.md) を読んで
ください。ノードは `setup_input → execute → update_context` を実装し、プランは固定 `node_order` を
持つ YAML です。

**Windows での注意点は？**
`.venv\Scripts\activate` で有効化し、`copy .env.example .env` でコピーします。curl の例は PowerShell
または Git Bash で実行してください。Windows では `tzdata` が自動的に入ります。

## コントリビュート

貢献を歓迎します。特に失敗の意味を固定するテストを歓迎します。まず
[CONTRIBUTING.md](CONTRIBUTING.md) を読んでください。環境、ブランチモデル、コミット規約、新しい
ノード / プランの雛形があります。参加は[行動規範](CODE_OF_CONDUCT.md)の対象です。

クエリ経路を変更する場合は、上記のゲートを実行し、プラン、図のラベル、四つの言語の README を同じ
コミットで更新してください。

## セキュリティ

脆弱性は [SECURITY.md](SECURITY.md) から非公開で報告してください。公開 Issue、PR、ディスカッションには
書かないでください。報告に実在の認証情報、接続文字列、顧客データを貼らず、伏せ字にしてください。

セキュリティに関わるデフォルト：

- 読み取り専用 SQL は単一ステートメントの AST ポリシーと `execute_sql` 内の独立した書き込み検査で
  強制されます。SQLite と ClickHouse はドライバ側のガードを追加し、デプロイではネイティブの読み取り
  専用アカウントを使うべきです。
- `JWT_SECRET` が 32 文字未満だと起動を中止します。
- ワークスペースで設定したプロバイダキーは保存前に AES-GCM で暗号化され、どのエンドポイントからも
  返されません。
- 任意の利用監査は、デフォルトで質問文も SQL も保存しません。
- 未登録のツールは `PermissionManager` が拒否します。行数、リトライ、タイムアウト、LLM 呼び出しの
  上限はモデルの制御外で適用されます。

サポートバージョン、範囲、開示プロセスは [SECURITY.md](SECURITY.md) にあります。

## ライセンス

[MIT](LICENSE) © 2026 Majiaqi。
