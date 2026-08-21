# GovTrans 评测与基准测试白皮서 (Evaluation & Benchmark)

## 1. Gold Set 与 Baseline 机制

GovTrans 建立了一套严苛的质量评测体系，确保每一次迭代都有据可依、有数可查：
- **固定 Single-Pass LLM Baseline**: 系统永久保留一个标准的单次直接调用大模型基准（Baseline），该基准**永不删除**，作为衡量所有流水线改进、提示词优化、模型升级的绝对参照物。
- **Gold Set (黄金测试集)**: 包含涵盖白皮书、领导人讲话、政策法规、新闻发布会等高难度、具代表性的政务平行语料集，由国家级资深译审专家人工核准。

---

## 2. 八大核心评测指标定义与计算方式

系统从多个维度对翻译质量进行综合评分（满分 100 分或 0-1 归一化得分）：
1. **Faithfulness (忠实度)**: 评估译文对源文语义的还原程度，严禁无中生有或主观删减。计算方式基于语义嵌入相似度及幻觉实体检出率。
2. **Terminology (术语准确率)**: 核心政务术语与 `document_glossaries` 及国家标准术语库的符合度。
3. **Numbers (数字精确度)**: 确保原文中的阿拉伯数字、百分比、年份、财政金额与译文完全一致。计算方式采用正则表达式与数值对齐校验。
4. **Entities (命名实体准确率)**: 专有名词、机构名称、地名等参照官方英文译名规范的准确率。
5. **Style (文体规范性)**: 是否符合英语政务公文、白皮书的庄重、准确、简洁文体风格（对照 `skills/` 规则）。
6. **MQM (Multidimensional Quality Metrics)**: 基于多维质量度量标准的缺陷扣分模型（Critical 严重扣分、Major 中度扣分、Minor 轻度扣分）。
7. **Latency (响应时延)**: 任务从接收到完成的总耗时及各 Stage 平均耗时（毫秒）。
8. **Cost (计算成本)**: 基于 `model_usage` 记录的 Token 消耗量（Input Tokens / Output Tokens）折算的计算开销。

---

## 3. Regression Gate (回归门禁)

- **门禁规则**: 任何涉及 Prompt、Skill、Model、Retrieval 策略或 Pipeline 代码的变更，必须通过自动化 Regression Gate。
- **触发条件**: 评估得分若低于 Baseline 或引入新的 Critical 缺陷，CI/CD 流水线与版本发布门禁将自动拦截并报错。

---

## 4. Benchmark 多维对比维度

GovTrans 评测中心支持按以下四个核心维度进行全方位 Benchmark 对比分析：
- **Model (模型维度)**: 对比不同底层大模型（如 DashScope 系列、不同参数量版本）的综合表现。
- **Skill Version (技能版本维度)**: 对比不同政务技能包（如 `gov-cn-en-core`, `gov-leader-speech`）对翻译质量的提升效果。
- **Pipeline Version (流水线版本维度)**: 对比流水线编排逻辑优化前后的吞吐与准确率变化。
- **Corpus Version (语料版本维度)**: 对比不同白皮书语料库与 TM 记忆库版本对检索增强（RAG）召回率的影响。
