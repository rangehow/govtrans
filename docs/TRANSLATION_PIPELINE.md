# GovTrans 翻译流水线白皮书 (Translation Pipeline)

## 1. 14 阶段确定性状态机 (Stage Graph)

GovTrans 核心业务逻辑由 `services/orchestrator/stage_graph.py` 定义，采用严谨的 14 个阶段（Stage）确定性状态机。每个阶段职责单一、输入输出明确、支持幂等重试与事件广播。

1. **`parse` (解析阶段)**: 
   - **输入**: 原始文档 (PDF/Word/Text)。
   - **输出**: 结构化段落与元数据。
   - **幂等性**: 相同文件 Hash 幂等跳过。
2. **`analyze` (分析阶段)**:
   - **输入**: 结构化文本。
   - **输出**: 文档领域、文体类型、机密等级分类。
3. **`terminology` (术语匹配阶段)**:
   - **输入**: 文本与领域标签。
   - **输出**: `document_glossaries`（包含术语对、origin 来源与 exception 排除项）。
4. **`retrieve` (检索增强阶段)**:
   - **输入**: 句段与术语。
   - **输出**: TM 记忆库匹配项、官方白名单网页检索证据。
5. **`plan` (翻译规划阶段)**:
   - **输入**: 全文分析与检索证据。
   - **输出**: 翻译策略、特殊数字/名称处理大纲。
6. **`translate` (翻译执行阶段)**:
   - **输入**: 段落、术语表、TM、翻译计划。
   - **输出**: `segments.versions['ai_draft']`。
7. **`deterministic_qa` (确定性 QA 阶段)**:
   - **输入**: AI 初稿、数字/专名规范。
   - **输出**: 基础格式与数字对齐错误报告。
8. **`term_review` (术语评审阶段)**:
   - **输入**: 初稿与 `document_glossaries`。
   - **输出**: 术语一致性检查及 MQM 缺陷记录。
9. **`semantic_review` (语义评审阶段)**:
   - **输入**: 源文与译文草稿。
   - **输出**: 忠实度与语义偏差评估。
10. **`style_review` (文体风格评审阶段)**:
    - **输入**: 译文与政务文体规范 (`skills/`)。
    - **输出**: 风格合规性建议。
11. **`consistency_review` (全局一致性评审阶段)**:
    - **输入**: 全文各段落译文。
    - **输出**: 上下文连贯与指代一致性问题。
12. **`finalize` (定稿与修订阶段)**:
    - **输入**: 各项评审发现 (`issues`)。
    - **输出**: 修复后版本，保存修订痕迹 (`before/after/diff/reason`)。
13. **`final_qa` (终审 QA 阶段)**:
    - **输入**: 终稿版本。
    - **输出**: 最终质量判定。
14. **`complete` (完成阶段)**:
    - **输入**: 验证通过的任务。
    - **输出**: 归档状态，生成最终导出资产。

---

## 2. DocumentGlossary 与 term_exception 机制

在 `terminology` 阶段，系统自动生成 `document_glossaries`（关联 `run_id` 与 `version`）。
- **`entries`**: 记录术语的原文、推荐译文、官方来源及置信度。
- **`term_exception` (术语异常/例外)**: 允许在特定上下文中对标准术语库进行安全覆盖或豁免，避免机械套用造成的语境不通，所有例外均记录审计日志。

---

## 3. Reviewer 独立性原则

为保证质量审查的客观公正，GovTrans 严格执行 **Reviewer 独立性架构**：
- 审校模型/Agent 在执行评审时，**严禁读取** Translator 的内部思考链（Reasoning）或中间 prompt 日志。
- 审校仅基于**原文 (`source`)**、**译文 (`translation`)**、**术语表 (`document_glossaries`)** 与**权威证据 (`evidence_refs`)** 独立进行 MQM 缺陷判定（Critical / Major / Minor）。

---

## 4. Finalizer 核心约束

Finalizer 阶段（Stage 12）负责将评审意见转化为高质量译文修订。其受到严格约束：
- **核心职责**: 只解决 `issues` 中指出的缺陷、提升行文流畅度、确保 `document_glossaries` 严格遵守、确保事实与数字零偏差。
- **审计留痕**: 每次修改必须完整保存 `before`（修改前）、`after`（修改后）、`diff`（差异对照）及 `reason`（修改理由）。

---

## 5. Release Gate 循环与 WAITING_HUMAN_REVIEW 状态机

系统的运行状态机（`CREATED` -> `PARSING` -> `ANALYZING` -> `RESEARCHING` -> `TRANSLATING` -> `REVIEWING` -> `FINALIZING` -> `QA` -> `COMPLETED` / `FAILED` / `CANCELLED` / `WAITING_HUMAN_REVIEW`）通过 Release Gate 进行流转控制：
- **Gate 检查**: 在 `final_qa` 阶段，若检测到 `critical > 0` 的缺陷，流水线自动回退（Loop）至 `finalize` 阶段重新修正。
- **循环上限 (Max Finalize Loops)**: 回退循环次数设有上限 (`max_finalize_loops = 2`)。
- **人工干预熔断**: 若连续循环 2 次仍无法消除 critical 缺陷，流水线自动熔断并转入 `WAITING_HUMAN_REVIEW` 状态，暂停自动化流转，等待人类专员介入人工审核与决策。
