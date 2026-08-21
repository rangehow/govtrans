# GovTrans 架构白皮书 (System Architecture)

## 1. 总体架构与数据流图

GovTrans 采用现代化前后端分离与微服务化编排架构。前端使用 Vite + React + TS，通过生产环境 Nginx（`:8080`）或开发代理访问 FastAPI 后端（`:8100`）。后端核心通过 ToFu 运行时（`tofu:15000`）对接大模型 Provider（DashScope）。

```ascii
+---------------------------------------------------------------------------------+
| Browser / Client (Vite + React + TS)                                            |
| - Workspace 三栏工作台 (Source / Translation / Intelligence)                    |
| - SSE 实时事件订阅 (cursor / Last-Event-ID resume, 15s heartbeat)               |
+---------------------------------------------------------------------------------+
          |                                              |
          | HTTP / REST API                              | SSE 实时日志流
          v                                              v
+---------------------------------------------------------------------------------+
| API Gateway & Backend Service (FastAPI, 端口 8100)                              |
| - apps/api/main.py (入口)                                                       |
| - apps/api/db.py (SQLAlchemy 统一数据访问)                                      |
+---------------------------------------------------------------------------------+
          |                                              |
          +-------------------+--------------------------+
                              |
                              v
+---------------------------------------------------------------------------------+
| Orchestrator & Pipeline Engine (14-Stage 状态机)                                |
| - services/orchestrator/stage_graph.py                                          |
| - services/orchestrator/tofu_client.py                                          |
+---------------------------------------------------------------------------------+
          |                                              |
          +-------------------+--------------------------+
                              |
                              v
+---------------------------------------------------------------------------------+
| ToFu Runtime (默认端口 15000) & 检索/安全护栏                                   |
| - POST /api/v1/agent/run (Idempotency-Key 幂等)                                 |
| - services/retrieval/search.py (QueryLeakGuard, 官方白名单检索)                 |
+---------------------------------------------------------------------------------+
          |                                              |
          v                                              v
+-----------------------+                    +------------------------------------+
| Model Providers       |                    | Persistent Storage                 |
| - DashScope           |                    | - PostgreSQL + pgvector (prod)     |
|   (DASHSCOPE_BASE_URL)|                    | - SQLite (local dev)               |
|   (DASHSCOPE_API_KEY) |                    | - MinIO Object Storage             |
+-----------------------+                    +------------------------------------+
```

### 横向领域服务职责
- **Terminology (术语服务)**: 管理 `terms`, `term_variants`, `term_evidence`, `term_audit_log` 及 `document_glossaries`（含 origin 与 exception），确保政务术语翻译统一。
- **Translation Memory (TM 记忆库)**: 基于 `translation_memory`（source, target, document_type, domain, url, authority [official_verified | official_aligned | government_term | official_web | trusted | general_web], embedding, provenance），提供高精度句段匹配。
- **Corpus & Alignment (语料与对齐)**: 管理平行语料、白皮书、政策文件等资产，支撑精准 RAG。
- **Search (检索与防泄漏)**: `services/retrieval/search.py` 执行 `perform_web_search`，内建 `QueryLeakGuard` 与官方源白名单（`scio.gov.cn`, `english.scio.gov.cn`, `gov.cn`, `xinhuanet.com`）。
- **QA & Validators (校验网关)**: 对译文进行确定性与语义 QA 校验。
- **Export (多格式导出)**: 支持将最终版本导出为 Word、PDF、双语对照等格式。

---

## 2. 真实代码结构与模块职责

项目核心目录结构如下：

- `apps/api/`: FastAPI 后端主入口与数据访问层
  - `main.py`: 应用初始化、中间件挂载、路由注册、启动校验。
  - `db.py`: SQLAlchemy `Base` 与 `engine` 定义，区分生产 PostgreSQL + pgvector 与开发 SQLite。
- `apps/web/`: 前端工作台（Vite + React + TS）
- `services/`: 核心业务逻辑微服务
  - `orchestrator/`: 状态机与 ToFu 客户端集成（`stage_graph.py`, `tofu_client.py`）。
  - `retrieval/`: 检索服务与防泄漏策略（`search.py`）。
- `skills/`: 领域技能与规则定义（`gov-cn-en-core`, `gov-white-paper`, `gov-policy-document`, `gov-press-conference`, `gov-leader-speech`, `gov-number-name-formatting`）。
- `scripts/`: 工具脚本，如 `scan_secrets.py` 自包含密钥扫描器。

---

## 3. 核心数据流与事件系统 (SSE Resume)

### 数据流转过程
1. **任务创建**: 客户端提交源文本与参数，API 创建 `translation_runs` 记录，初始化状态为 `CREATED`。
2. **流水线调度**: Orchestrator 驱动 14 个 Stage 顺序或条件执行，每次状态变更生成 `run_events` 记录。
3. **实时推送**: 客户端通过 `GET /api/v1/tasks/{id}/stream` 订阅 Server-Sent Events (SSE)。
   - **断线重连 (Resume)**: 支持 `cursor` / `Last-Event-ID` 机制，确保断线期间事件不丢失。
   - **心跳机制**: 每 15 秒发送一次心跳 (`heartbeat`) 保持连接。

---

## 4. 数据库 Schema 概览

系统基于 SQLAlchemy 定义了以下核心表：
- `translation_runs`: 记录翻译任务状态、方向、机密性、源文本、摘要、当前阶段、进度、循环计数、错误信息及 `version_pins`。
- `segments`: 细粒度句段管理 (`run_id`, `idx`, `source`, `translation`, `status`, `versions` [ai_draft | reviewed | final])。
- `run_events`: 运行事件流 (`run_id`, `seq`, `type`, `phase`, `status`, `title`, `summary`, `progress`, `segment_ids`, `evidence`, `metrics`)。
- `issues`: MQM 质量问题记录 (`reviewer`, `severity` [critical | major | minor], `category`, `source_span`, `target_span`, `message`, `suggested_fix`, `evidence_refs`, `status`)。
- `document_glossaries`: 文档级术语表 (`run_id`, `version`, `entries` 含 origin 与 exception)。
- `model_usage`: 模型调用审计 (`role`, `model`, `tofu_task_id`, `latency_ms`, `input_tokens`, `output_tokens`, `retries`, `status`).
- `terms`, `term_variants`, `term_evidence`, `term_audit_log`: 术语知识库。
- `translation_memory`: 翻译记忆库 (`source`, `target`, `document_type`, `domain`, `url`, `authority`, `embedding`, `provenance`).

---

## 5. 部署形态

项目根目录提供 `docker-compose.yml`，支持一键容器化部署：
- **Database**: PostgreSQL 16 + pgvector (`db=pgvector/pgvector:pg16`)。
- **Cache & Message Broker**: Redis 7-Alpine。
- **Object Storage**: MinIO + minio-init（用于大文件与多媒体附件存储）。
- **Services**: API (`apps/api`), Web (`apps/web`), ToFu Runtime（可选 profile）。
