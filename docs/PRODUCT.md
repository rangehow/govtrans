# GovTrans 产品与工作台体验白皮서 (Product & UX Design)

## 1. 产品定位与目标用户角色

GovTrans 是面向高标准、严要求的政府政务公文与白皮书翻译场景的专业级、生产级（Production-grade）证据驱动翻译系统。

### 用户角色定义
- **译员 (Translator)**: 负责查看 AI 草稿、利用翻译记忆与术语库进行句段精修。
- **审校 (Reviewer)**: 独立执行 MQM 审校，识别 critical/major/minor 缺陷，提出修改建议。
- **管理员 (Administrator)**: 管理术语表、学习候选库、白皮书语料资产、系统配置与审计日志。
- **评测员 (Evaluator)**: 监控 Gold Set 基准测试、管理评估指标（Faithfulness, Terminology, Numbers 等）与回归门禁。

---

## 2. Workspace 三栏工作台架构

Workspace 采用经典且高效的三栏布局，实现极致的交互体验：
1. **Source 面板 (左栏)**: 展示源文本、段落结构及源端证据引用。
2. **Translation 面板 (中栏)**: 展示分段译文、多版本对照（AI Draft / Reviewed / Final / Human Edited）与实时状态。
3. **Intelligence 面板 (右栏)**: 聚合 ToFu 智能检索结果、官方对齐参考、术语高亮、RAG 证据链及 MQM 缺陷诊断。

### 核心联动特性：Segment 对齐滚动
- 源文面板与译文面板实现像素级滚动同步与句段高亮对齐。
- 点击任意句段，Intelligence 面板实时加载该句段相关的术语、TM 匹配项与网络检索证据。

---

## 3. Evidence UX (证据交互设计)

GovTrans 强调“证据驱动，拒绝虚幻大模型幻觉”：
- **可点击短语 (Clickable Phrase)**: 译文中的关键实体与术语支持悬停与点击。
- **弹窗情报卡片**: 点击后弹出浮窗，展示:
  - `Term / Official translation`: 官方核准译名。
  - `Source document`: 原文出处与上下文。
  - `Authority`: 权威级别（`official_verified`, `official_aligned`, `government_term`, `official_web`, `trusted`, `general_web`）。
  - `Similarity`: 向量检索相似度得分。

---

## 4. QA UX 与 MQM 缺陷工作流

系统在 QA 阶段自动或半自动触发校验，QA UX 提供高效的交互处置机制：
- **严重级别分类**:
  - `critical`: 致命事实错误、数字偏离、严重政治表述偏差。
  - `major`: 重要术语不统一、语法或语体违规。
  - `minor`: 标点、润色建议等轻微瑕疵。
- **交互动作**:
  - `Accept Fix` (采纳建议): 一键应用 AI 提供的修复方案。
  - `Dismiss` (忽略): 人工确认无误后归档忽略。
  - `Edit Manually` (手动编辑): 直接在译文输入框中订正。

---

## 5. Diff 版本演进与管理

GovTrans 严格记录译文的演化历史，通过 `segments.versions` 字段精准追踪四个版本状态：
1. **AI Draft (AI 初稿)**: 模型初次生成的直译/意译草稿。
2. **Reviewed (审校版)**: 经独立审校与 MQM 修正后的版本。
3. **Final (终稿版)**: 经过最终确认与润色生成的版本。
4. **Human Edited (人工修订版)**: 译员或管理员人工覆盖的最终定稿。

工作台提供直观的文本 Diff 对比视图，支持任意两版之间的差异高亮。

---

## 6. LearningCandidate 审批流与知识沉淀

- **学习候选 (LearningCandidate)**: 系统在翻译与审校过程中自动沉淀的高质量译文对与术语变体。
- **审批流**: 管理员可在 Knowledge Admin 后台对 LearningCandidate 进行审核、编辑或驳回，审核通过后自动写入 `translation_memory` 或 `terms` 数据库，实现系统能力的持续进化。

---

## 7. Evaluation Center (评测中心)

评测中心为管理员和评测员提供可视化看板：
- 监控固定 single-pass LLM baseline 与当前流水线的性能对比。
- 跟踪 Faithfulness、Terminology、Numbers、Entities、Style、MQM、Latency、Cost 八大核心指标。
- 支持按 Model、Skill Version、Pipeline Version、Corpus Version 维度进行多维 Benchmark 对比。
