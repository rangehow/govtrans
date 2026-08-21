# Project Journal

> This file is the project's **evolution journal** — a living record the AI
> assistant maintains across sessions. It is NOT a rules file (project rules
> live in CLAUDE.md / AGENTS.md if present) and NOT a versioned changelog.
> It is a free-form dev log of *how and why* this project changes over time.

## How to use this file

- **Read it first.** At the start of a session, read this journal to understand
  how the project reached its current state before making changes.
- **Keep it current — on your own initiative.** After any meaningful change,
  append a dated entry yourself. This is a standing, pre-authorized action: you
  do NOT need to ask the user before adding an entry, and you should not pause
  to request permission to update the journal. Do not rewrite or delete past
  entries — the history is the point; only ever append.
- **Record the *why*, not just the *what*.** A diff shows what changed; this
  journal explains the reasoning a future reader (human or model) could not
  reconstruct from the code alone.

## What to record

- **Experimental projects:** methods/approaches tried, why each was adopted or
  abandoned, hyperparameter or design changes, and experiment results
  (metrics, observations, dead ends).
- **Engineering projects:** technology-selection changes and their rationale,
  refactoring steps and their motivation, architectural decisions, and the
  current status / known issues / next steps.

## Entries

### 2026-08-21 — E07/E11/E14/E17 落地：术语闭环、QA 全家桶、三栏 Workspace、导出引擎
- **Change:** ①QA validators 补齐 8/8（新增 acronym/entity《》/enumeration，acronym 用显式 Latin 边界——`\b` 在 CJK 旁失效的坑）；②Export Engine（txt/md/json/xliff/tmx/docx/docx_bilingual，python-docx，双语表格）+ `/api/runs/{id}/export` 实测 200/400；③terminology 补 term_update + history 端点，create/update/deprecate 全审计；④前端升级三栏 Workspace（Source/Translation/Intelligence tab：Activity/References/QA/History，segment 对齐高亮 + scrollIntoView，键盘可达）。72 单测全绿，前端 build 通过。
- **Why:** 任务书 §22/§39/§35/§28。全部不依赖 LLM key，属“离线可验证”增量。
- **Lesson:** uvicorn 旧进程残留导致新代码 404——`fuser -k` 静默失败后应以 `ss -tlnp` 验证端口真空再启动；验收新端点前先确认进程启动时间。
- **Next:** E06 TM 语义检索、E08 hybrid retrieval、E15 Knowledge admin UI、E16 Evaluation、E20 E2E。LLM key 仍是全链路验收唯一外部依赖。

### 2026-08-21 — E04/E05 落地：语料摄入 + 平行对齐管线
- **Change:** 新增 `services/corpus/`（models/parser/crawler/aligner/dedup/ingest）+ `scripts/ingest_corpus.py` CLI + `/api/corpus/*` 只读端点 + 迁移 `a45ac689115a`；51 单测全绿。CLI 真实验证：4 段双语白皮书片段 → 2 高锚点句对（0.85/0.76）对齐并 promoted 进 TM（authority=official_aligned，带 provenance 回链）。
- **Why:** 任务书 §11/§12。对齐用确定性 DP（长度比对数高斯 + 数字锚点），不依赖模型/网络，离线可测；语义向量打分留给 E06/E08（需 EMBEDDING_MODEL key）。单句段落只产 sentence 级行，避免 paragraph/sentence 同文本重复。
- **Lesson:** ①alembic autogenerate 空迁移事故——env.py 漏 import 新 models 模块，版本戳被打上但表未建；已存 memory 防再犯。②运行中的 uvicorn 持 SQLite WAL 写锁会卡住迁移，先停 API 再迁移。③脚本直接运行需自插 repo root 到 sys.path（scripts/ 下的 python script 不会自动加项目根）。
- **Next:** E06 TM 语义检索（pgvector+embedding）；E07 术语库种子；E14 三栏 Workspace。等用户提供 `DASHSCOPE_API_KEY` 后跑通 §53 全验收。

### 2026-08-21 — 第一阶段完成：E00–E03 落地（安全引导 / 基础设施 / ToFu 集成 / Run·Event 引擎）
- **Change:** 从空仓库建成可运行的 GovTrans 第一阶段：FastAPI(:8100) + Orchestrator（14 阶段 deterministic stage graph，含 release gate 与 WAITING_HUMAN_REVIEW）+ TofuClient（幂等/SSE cursor resume/重试）+ React 前端（SSE 驱动时间线）+ Alembic 迁移 + docker-compose + 7 份 docs + 32 个单测全绿。首次提交 `7bc98a1`（100 文件）。
- **Why:** 任务书 §52 Task 1-10。关键决策：sync SQLAlchemy（SQLite dev / Postgres prod 一套代码，DATABASE_URL 切换）；prompts/schemas 存 `agents/` 文件而非 Python 字符串；事件 phase 统一为 stage id（开发中发现 create/stage 事件 phase 命名不一致导致前端映射失效）；`pkill -f` 会匹配自身 shell 命令行导致自杀，改用 `fuser -k <port>/tcp`。
- **Result / status:** 验收路径已跑到“真实失败可观测”：run.created→parse→analyze→（无 key）run.failed，事件全量入库 + SSE 实时推送验证通过。**阻塞项：需用户提供 `DASHSCOPE_API_KEY`**（写入 `.env`）后运行 `python scripts/smoke_agent_call.py` 与完整翻译验收；本机无 docker daemon（compose 未实测）、公网 npm/pip 不可达（用内部镜像 r.npm.sankuai.com / pip.sankuai.com --trusted-host）。ToFu server 在 :15000 运行中（未注册任何 provider）。
- **Next:** 按任务书进入 Corpus Pipeline（E04 语料摄入）；E05 对齐；前端补三栏 Workspace（E14）。

<!-- Append newest entries at the top. Suggested format:

### YYYY-MM-DD — short title
- **Change:** what changed
- **Why:** the reasoning / problem being solved
- **Result / status:** outcome, metrics, or current state
-->
