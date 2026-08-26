# GovTrans 架构白皮书 (System Architecture)

## 1. 总体架构与数据流图

GovTrans 采用现代化前后端分离与微服务化编排架构。前端使用 Vite + React + TS，通过生产环境 Nginx（`:8080`）或开发代理访问 FastAPI 后端（`:8100`）。后端核心通过带 Bearer Token 的 Tofu Agent sidecar（`tofu:15001`）对接大模型 Provider（DashScope）。

```ascii
+---------------------------------------------------------------------------------+
| Browser / Client (Vite + React + TS)                                            |
| - 持久化任务历史 + 句段级双语对齐文档 + 质量情报面板                    |
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
| Tofu Agent Sidecar (默认端口 15001) & 检索/安全护栏                            |
| - POST 异步受理一次，并按任务句柄轮询（Idempotency-Key 幂等）                  |
| - services/retrieval/search.py (QueryLeakGuard, 官方白名单检索)                 |
+---------------------------------------------------------------------------------+
          |                                              |
          v                                              v
+-----------------------+                    +------------------------------------+
| Model Providers       |                    | Persistent Storage                 |
| - DashScope           |                    | - PostgreSQL + pgvector (prod)     |
|   (DASHSCOPE_BASE_URL)|                    | - SQLite (local dev)               |
|   (DASHSCOPE_API_KEY) |                    | - Docker volume backups            |
+-----------------------+                    +------------------------------------+
```

### 横向领域服务职责
- **Language Capability (语言能力)**: `services/languages.py` 统一管理语言目录、BCP 47、RTL 和语言对能力。模型翻译是通用能力；语料、术语、专项文风和确定性规则按语言对独立声明，不把“无专属语料”误表达为“不支持翻译”。
- **Terminology (术语服务)**: 保存可由人维护的“源语言 → 目标语言”硬约束；任务级术语可覆盖当前语言对的全局术语。它不负责文风学习。
- **Corpus & Alignment (语料与对齐)**: 保存官方中英文正文、对齐关系和出处，作为可追溯证据；对齐分不低于 85% 的句对自动进入运行时参考检索，但始终是软参考，不会变成强制译法。
- **Style Skills (文风能力)**: 从高质量官方对照语料中蒸馏句法、语域、衔接和段落组织规则，按文种版本化启用；Skill 不夹带固定术语。
- **Official Reference Retrieval (官方参考检索)**: 同一检索路径合并高置信自动对齐句对与 `translation_memory` 中的人工核验句对，按文种、领域、词汇相关度和权威等级重排。不存在单独的“发布到哪里”步骤；人工核验只是提高信任级别和留下审计记录。
- **Search (检索与防泄漏)**: `services/retrieval/search.py` 执行 `perform_web_search`，内建 `QueryLeakGuard` 与官方源白名单（`scio.gov.cn`, `english.scio.gov.cn`, `gov.cn`, `xinhuanet.com`）。
- **QA & Validators (校验网关)**: 数字、括号、术语等通用规则在所有语言对生效；中文日期、书名号和英文大小写等规则仅在语义适用的语言对启用，其余由显式知道语言方向的模型审校补齐。
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
   ToFu 暂时拒绝准入时写入 `WAITING_RESOURCES`，保留 `current_stage` 并自动重试；
   该状态属于活动任务，服务重启后照常恢复。任务受理后只轮询对应句柄，不会因读取超时
   重复提交；超过限定等待时间时先请求中止，再按配置使用同 Provider 直连兜底。
3. **刷新恢复**: 客户端先通过 `GET /api/runs` 读取轻量任务历史，再用
   `GET /api/runs/{id}` 恢复源文、句段、多版译文与 QA 结果。浏览器端不保存文档内容。
4. **实时推送**: 客户端通过 `GET /api/runs/{id}/events` 订阅 Server-Sent Events (SSE)。
   - **断线重连 (Resume)**: 支持 `cursor` / `Last-Event-ID` 机制，确保断线期间事件不丢失。
   - **心跳机制**: SSE 每 15 秒发送传输层注释心跳；执行阶段另每 8 秒持久化一条不改变
     完成百分比的存活事件。并行阶段在时间线上会同时保持活动状态。
   - **JSON 回放**: `GET /api/runs/{id}/event-log?after=N` 用于刷新恢复和 SSE 降级。

---

## 4. 数据库 Schema 概览

系统基于 SQLAlchemy 定义了以下核心表：
- `translation_runs`: 记录翻译任务状态、`source_language`、`target_language`、机密性、源文本、文风 Skills、任务级术语、上下文策略、摘要、当前阶段、进度、循环计数、错误信息及 `version_pins`。
- `segments`: 段落级持久化与双语展示锚点 (`run_id`, `idx`, `source`, `translation`, `status`, `versions` [ai_draft | reviewed | final])；它不是彼此隔离的模型调用边界。
- `run_events`: 运行事件流 (`run_id`, `seq`, `type`, `phase`, `status`, `title`, `summary`, `progress`, `segment_ids`, `evidence`, `metrics`)；`(run_id, seq)` 唯一，可安全续传。
- `issues`: MQM 质量问题记录 (`reviewer`, `severity` [critical | major | minor], `category`, `source_span`, `target_span`, `message`, `suggested_fix`, `evidence_refs`, `status`)。
- `document_glossaries`: 文档级术语表 (`run_id`, `version`, `entries` 含 origin 与 exception)。
- `model_usage`: 模型调用审计 (`role`, `model`, `tofu_task_id`, `latency_ms`, `input_tokens`, `output_tokens`, `retries`, `status`).
- `terms`, `term_variants`, `term_evidence`, `term_audit_log`: 按 `source_language + target_language` 隔离的术语知识库。
- `translation_memory`: 按语言对检索且可安全反向使用的翻译记忆库 (`source`, `target`, `source_language`, `target_language`, `document_type`, `domain`, `url`, `authority`, `embedding`, `provenance`).

### SCIO 语料完整性

- 自动任务按当前年份滚动同步近十年归档，而非固定抓取最新几份；同步清单、逐份结果和句对数量写入 `corpus_sync_jobs`，API 或页面重启后从已完成项续跑，同范围复查采用增量同步。
- 抓取层使用原始 HTML，保留标题、段落、分页链接与原始出处，不把可读性抽取后的 Markdown 误交给 HTML 解析器。
- 英文白皮书的 `content_<id>_N.htm` 分页以及旧版专题页中独立内容 ID 的分章会按官方目录顺序自动发现、完整抓取并合并；`Please see the attachment` 一类下载提示壳不视为正文，任一已发现正文页失败时整次导入失败，不发布残缺证据。
- 相同中英文内容对只有一条 `document_pairs` 记录；重复导入复用既有对齐结果，不能增加规则支持度。
- 自动生效的动态规则要求置信度不低于 80%，且至少由两份不同 SCIO 官方文档共同支持；未达阈值的观察项不生效也不形成待办。
- 中文目录及正文出现两阶段 JavaScript 验证时，抓取层在严格官方域名白名单内启动隔离 Chromium，执行站点挑战并只捕获最终 200 正文响应；无需用户上传或手工复制 Cookie。保存的官方 HTML 仅作为回源故障灾备，仍校验 URL 与正文有效性。
- 对齐层剔除重复目录块，按每份文档动态估计中英长度比例并支持最多 3:1 的段落边界差异。对齐算法带版本号；升级时自动重建自动证据，同时保留人工修正、核验或排除状态。

---

## 5. 部署形态

项目根目录提供 `docker-compose.yml`，支持一键容器化部署：
- **Database**: PostgreSQL 16 + pgvector (`db=pgvector/pgvector:pg16`)。
- **Agent Runtime**: 随仓库 wheel 构建的 Tofu Agent 0.17 sidecar，Token 鉴权且只向宿主机回环地址暴露调试端口。
- **Services**: API (`apps/api`) 与 Web (`apps/web`)；Redis/MinIO 当前没有实际调用点，因而不属于必需部署面。
- **Operations**: `scripts/deploy.sh` 负责初始化、构建、健康检查和备份；API 启动前自动执行 Alembic 迁移。
