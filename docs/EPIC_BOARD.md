# GovTrans Epic 看板与任务状态 (Epic Board)

根据任务书要求，GovTrans 项目共规划了 20 个 Epic（E00 - E20）。当前整体状态为：**E00 至 E02 处于 In-Progress（进行中），其余 Epic 处于 Open（待启动）状态**。

| Epic 编号 | Epic 名称 | 主要目标 | 主要交付物 | 依赖关系 | 当前状态 | 一句话验收标准 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **E00** | Security & Bootstrap | 安全底座与项目初始化 | `.env.example`, `scan_secrets.py`, 安全过滤与启动校验 | 无 | **In-Progress** | 静态扫描无秘钥泄漏且生产环境缺失 key 时安全阻断。 |
| **E01** | Infrastructure | 基础架构与容器编排 | `docker-compose.yml`, FastAPI 入口, DB 驱动 | E00 | **In-Progress** | PostgreSQL+pgvector 与 API/Web 容器一键成功拉起。 |
| **E02** | ToFu Provider & Runtime | ToFu 运行时集成与代理 | `tofu_client.py`, SSE 流式传输, 幂等网关 | E01 | **In-Progress** | 成功调用 ToFu 并在 15s 心跳下支持 SSE cursor 恢复。 |
| **E03** | Run & Event Engine | 运行状态机与事件总线 | `stage_graph.py`, `run_events` 数据库与 SSE 推送 | E02 | **In-Progress** | 14 阶段状态机顺畅流转并实时向前端广播标准事件。 |
| **E04** | Corpus Ingestion | 语料摄入与白皮书解析 | 语料解析器, 结构化存储 | E01 | Open | 成功解析并入库国标白皮书与政策平行语料。 |
| **E05** | Alignment | 句段与文档级平行对齐 | 对齐算法, 数据库关联 | E04 | Open | 实现中英文政务平行文本的高精度句段对齐。 |
| **E06** | Translation Memory | 翻译记忆库 (TM) 管理 | `translation_memory` 表, 向量检索 | E05 | Open | 基于 embedding 与 authority 实现高精度 TM 匹配。 |
| **E07** | Terminology | 政务术语库与术语评审 | `terms`, `document_glossaries`, 异常处理 | E03 | Open | 自动提取术语并在翻译中严格遵循官方术语库。 |
| **E08** | Retrieval | 检索增强与防泄漏策略 | `search.py`, `QueryLeakGuard`, 官方白名单 | E02 | Open | 联网检索被严格限制在白名单内且无机密外泄。 |
| **E09** | Style Distillation | 政务文体蒸馏与规则库 | `skills/` 规则包, 文体风格对齐 | E03 | Open | 译文符合英文政务白皮书的庄重公文文体规范。 |
| **E10** | Translation Pipeline | 14 阶段核心翻译流水线 | 翻译与 QA 逻辑实现 | E03, E07 | Open | 全自动完成从 parse 到 complete 的 14 阶段转化。 |
| **E11** | QA Validators | 确定性质量校验网关 | `deterministic_qa`, 数字/专名校验器 | E10 | Open | 准确拦截数字偏离与格式错误的初稿译文。 |
| **E12** | Review Agents | 独立审校与 MQM 评估 | Reviewer 独立调用, `issues` 评分表 | E10 | Open | 独立审校模型产出清晰的 critical/major/minor 缺陷。 |
| **E13** | Finalization | 终稿修订与循环门禁 | Finalizer 约束逻辑, release gate 回退 | E12 | Open | 缺陷超标时自动回退循环，超限转人审。 |
| **E14** | Workspace Frontend | 三栏工作台前端实现 | `apps/web/` Vite+React+TS 界面, Diff 视图 | E03 | Open | 用户可在三栏工作台流畅进行高亮对照与审校。 |
| **E15** | Knowledge Admin | 知识后台与学习候选审批 | LearningCandidate 审批流, 后台管理 | E07 | Open | 管理员可在线审核并沉淀高质量术语与 TM。 |
| **E16** | Evaluation Center | 评测中心与基准测试 | Gold Set, 八大指标计算, Regression Gate | E10 | Open | 每次代码变更自动触发 Regression Gate 对比 baseline。 |
| **E17** | Export | 多格式文档导出 | Word, PDF, 双语对照导出器 | E10 | Open | 完美导出符合官方排版规范的政务双语公文。 |
| **E18** | Confidentiality | 机密级别控制与隔离 | `confidentiality` 字段, 涉密任务断网管控 | E00 | Open | 涉密任务被严格禁止外网搜索与数据外流。 |
| **E19** | Production Hardening| 生产环境加固与调优 | 日志脱敏, 资源限制, 性能调优 | E01 | Open | 生产环境平稳运行，日志无任何敏感秘钥与明文。 |
| **E20** | E2E & Regression | 端到端测试与回归演练 | 端到端集成测试脚本, 全链路回归 | 全部 | Open | 全链路端到端集成测试一次性顺利通关。 |
