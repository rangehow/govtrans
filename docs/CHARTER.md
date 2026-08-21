# GovTrans 项目宪章 (Project Charter & Architecture Decisions)

## 1. North Star (北极星愿景)
> **Production-grade evidence-driven government translation system**
> (打造面向高标准政务公文与白皮书的、生产级证据驱动的智能翻译系统)

---

## 2. 非目标 (Non-Goals)
为了聚焦核心价值与系统稳健性，GovTrans 明确**不予实现**以下内容：
1. **不做玩具级 Demo**: 拒绝任何形如“前端 `textarea` 直连大模型进行翻译”的轻量 Demo 架构。
2. **不做无 QA 校验的翻译**: 拒绝盲目相信单次大模型输出、缺少确定性校验与 MQM 审校闭环的系统。
3. **不做无真实证据检索的“虚幻智能”**: 拒绝没有经过官方语料白名单与 TM 记忆库支撑的“幻觉式”翻译。

---

## 3. 架构决策清单 (Architecture Decisions, AD)

GovTrans 在架构设计上做出了以下不可动摇的顶层技术决策：

- **AD-01: ToFu 独立 Runtime 隔离**
  - *决策*: ToFu 作为独立运行时（默认端口 `:15000`），通过标准 API 与核心业务系统解耦，绝不直接侵入修改核心业务代码。
- **AD-02: 模型全环境变量配置**
  - *决策*: 所有大模型角色与服务（`TRANSLATOR_MODEL`, `REVIEW_MODEL`, `FAST_MODEL`, `EMBEDDING_MODEL`, `RERANK_MODEL` 及 `DASHSCOPE_BASE_URL`）必须通过环境变量动态配置，严禁硬编码。
- **AD-03: Skill / Corpus / Glossary 三分离**
  - *决策*: 技能规则 (`skills/`)、证据语料 (`corpus/`) 与术语词典 (`terminology/`) 必须严格三分离，独立演进。
- **AD-04: Deterministic Stage Graph (确定性状态机)**
  - *决策*: 核心翻译流程必须采用确定性的 14 阶段状态机（`stage_graph.py`）进行编排，拒绝不可控的纯自由对话式 AI 流程。
- **AD-05: 无 Fake Progress (杜绝虚假进度条)**
  - *决策*: 前端进度条与状态展示必须严格基于后端真实的 SSE 事件与 `run_events` 推进，绝不使用模拟假进度。
- **AD-06: Baseline 永不删除**
  - *决策*: 固定 single-pass LLM baseline 必须永久保留，作为所有版本评测的恒定锚点。
- **AD-07: Prompts & Schemas 独立存储**
  - *决策*: 所有 Prompt 模板与结构化 Schema 必须统一存放在 `agents/` 目录中，严禁硬编码散落于 Python 字符串中。
- **AD-08: Secrets 零入库零日志**
  - *决策*: 严格执行凭证零入库、零日志、零前端透传的安全红线。
