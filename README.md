# GovTrans

> Production-grade evidence-driven government translation system (Chinese → English).

基于官方平行语料、Translation Memory、术语知识库、专业 Translation Skills、
网络研究工具与多阶段 Agent 审校的政务翻译生产系统。

**架构**：Browser → GovTrans API (FastAPI :8100) → Orchestrator（deterministic
Stage Graph）→ ToFu（独立 agent runtime :15000）→ DashScope（OpenAI-compatible）。

## 快速开始（本地 dev）

```bash
cp .env.example .env          # 填入 DASHSCOPE_API_KEY（必需）
make migrate                  # 初始化数据库（默认 SQLite）
make dev-api                  # 启动 API :8100
make dev-web                  # 启动前端 :3000（另开终端，需先 npm install）
```

Docker 一体化部署见 `docker/README.md`（`docker compose up`）。

## 校验

```bash
make scan                     # secret 扫描
make test-unit                # 单元测试
python scripts/smoke_agent_call.py   # 一次真实 ToFu→DashScope 调用
```

## 文档

- `docs/ARCHITECTURE.md` — 总体架构
- `docs/TRANSLATION_PIPELINE.md` — 14 阶段流水线规范
- `docs/SECURITY.md` — 秘钥与机密分级管控
- `docs/CHARTER.md` / `docs/EPIC_BOARD.md` — 项目章程与 Epic 看板
