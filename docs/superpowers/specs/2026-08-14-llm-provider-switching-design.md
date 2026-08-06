# 设计文档:LLM 提供商切换(用户选模型 + 填 Key 即生效)

> 日期:2026-08-14 · 状态:已批准 · 关联问题:设置面板保存的模型/密钥不影响查询执行

## 1. 背景与目标

当前 `/api/v1/settings/llm` 的保存只写入内存 settings store + MySQL,查询管线使用的
`LiteLLMRouter` 单例由 `agent.yml` + `.env` 构建,两者未打通;API Key 输入仅存布尔标记。

**目标**:用户在系统设置里选择默认模型、为各家提供商填写 API Key(含自定义
OpenAI 兼容端点)后,**立即生效**且**重启后保留**;聊天面板支持每次查询临时选择模型。

## 2. 方案

- 密钥 **AES-256-GCM 加密**存 MySQL(密钥由 `JWT_SECRET` 经 SHA-256 派生),任何 API 不回显明文;
- 保存设置后**动态重建 router 单例**(`build_router_from_settings`),查询管线零改动;
- 服务启动时(main.py 与 FastAPI lifespan)从 MySQL 回载设置并重建,**重启后仍生效**;
- 自定义提供商:名字为 LiteLLM 已知 provider 时用 `name/model`,否则走
  `openai/{model}` + base_url(无 base_url 则标记不可用);
- 查询 API 增加可选 `model` 参数,透传 `GenerateSQLNode.config["model"]`(节点已支持 override);
- 前端:设置面板增加各家 key 输入框与自定义 base_url 输入;聊天面板增加模型下拉(默认"跟随系统设置")。

## 3. 模块设计

### 3.1 `app/security/crypto.py`(新增)
- `encrypt_secret(plaintext) -> str`:`base64(nonce(12) || AESGCM(plaintext))`;
- `decrypt_secret(token) -> str | None`:解密失败返回 None(不抛异常);
- 密钥:`sha256(JWT_SECRET)`;JWT_SECRET 为空时使用固定后备密钥并打 warning。

### 3.2 `app/api/settings.py`
- provider 配置结构扩展:`{enabled, models, api_key_configured, api_key_enc?, base_url?}`;
- `PUT /settings/llm`:providers 更新中 `api_key` 非空 → 加密存 `api_key_enc`、置
  `api_key_configured=True`;`api_key` 为空 → 保留原密文;保存成功后调用
  `app.llm.factory.apply_llm_settings()` 重建 router;
- `GET /settings/llm`:仅返回 `api_key_configured`,永不返回密钥;
- `GET /settings/available-models`:`available` = enabled 且(api_key_configured 为真,或服务运行于 LLM_MOCK_MODE)。

### 3.3 `app/llm/router.py`
- `ProviderConfig` 增加 `api_key: str | None`、`base_url: str | None`;
- `_call_provider`:显式 `api_key` 优先于 env;自定义 provider(非 LiteLLM 已知名)以
  `openai/{model}` + `api_base=base_url` 调用;缺少必要信息时报
  `ProviderConfigurationError`(非重试错误,直接失败)。

### 3.4 `app/llm/factory.py`
- `build_router_from_settings(settings_llm) -> LiteLLMRouter`:
  遍历 enabled providers 注册 ProviderConfig;`default_model` 不属于任何 provider 时
  回退到第一个可用 provider 的首个模型;`fallback_model` 同理;无可用 provider 时
  保持 mock 模式;
- `apply_llm_settings() -> LiteLLMRouter`:读 `_settings_store["llm"]`(必要时先从 MySQL
  回载)并替换 `_default_router` 单例;
- 原 `build_router`(agent.yml/.env)保留,作为无任何已保存设置时的启动默认。

### 3.5 `app/api/query.py`
- `QueryRequest` 增加 `model: str | None = None`;
- 非流式与流式两条链路均把 `model` 写入 `GenerateSQLNode` 的 `config["model"]`;
- `model` 不在任何启用 provider 的 models 中 → 422 错误提示。

### 3.6 前端 `frontend_design/workspace.html`
- SettingsPanel:每 provider 一个 key 输入框(占位"已配置,留空保持不变")、自定义
  provider 增加"接口地址"输入、默认模型下拉来自 available-models;
- ChatPanel:输入区加模型下拉,默认项"跟随系统设置"(不传 model)。

### 3.7 启动回载
- `main.py` initialize 第 9 步之后调用 `apply_llm_settings()`;
- `app/api/__init__.py` lifespan 在 RAG 初始化后同样调用(覆盖 uvicorn --factory 直启场景)。

## 4. 错误处理

- 解密失败/无 JWT_SECRET → provider 视为"key 未配置",router 回退 mock(查询带 warning);
- 保存设置后重建失败 → 保留旧 router,HTTP 返回 500 并提示;
- 自定义 provider 缺 base_url 且非已知名 → 该 provider 在 available-models 中标记不可用。

## 5. 测试

- `tests/test_security/test_crypto.py`:加解密往返、错误 token、密钥派生确定性;
- `tests/test_api/test_settings_llm.py`:PUT 保存 → router 单例 default_model 真实切换;
  GET 不回显密钥;重启回载(apply_llm_settings);
- `tests/test_llm/test_factory_settings.py`:settings 构建 router、default 回退、
  openai-compatible 自定义 provider 路由、无 key 回退 mock;
- 遵循现有原则:真实组件、可注入假模型,不新增 mock 框架。

## 6. 改动清单

新增:`app/security/__init__.py`、`app/security/crypto.py`、`tests/test_security/*`、
`tests/test_api/test_settings_llm.py`、`tests/test_llm/test_factory_settings.py`
修改:`app/api/settings.py`、`app/llm/router.py`、`app/llm/factory.py`、
`app/api/query.py`、`main.py`、`app/api/__init__.py`、`frontend_design/workspace.html`、
`pyproject.toml`(依赖声明 cryptography>=42)

## 7. 安全边界

- 密钥密文与 JWT_SECRET 分离存储假设不成立时(同一 DB + 同一 .env)为弱安全,
  仅防"数据库泄露但环境变量未泄露";文档中如实标注;
- 前端永不渲染明文 key;日志不打印 key。
