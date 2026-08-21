# GovTrans 安全与合规白皮书 (Security & Compliance)

## 1. 秘钥全生命周期安全管理

GovTrans 严格遵循国家政务与企业级信息安全标准，对敏感凭证实施全生命周期零泄露管控：
- **`.env.example` 占位机制**: 代码库中仅提供无实际敏感信息的 `.env.example` 模板文件。
- **`.gitignore` 严格排除**: 所有真实配置文件与 `.env` 文件均被 Git 严格忽略，杜绝凭证入库。
- **启动校验机制**: API 启动时（`apps/api/main.py`）强制校验 `DASHSCOPE_API_KEY` 等核心秘钥是否存在。
  - *本地开发豁免*: 仅在本地开发环境中允许设置 `GOVTRANS_ALLOW_MISSING_KEYS=true` 进行特批豁免。
  - *生产环境阻断*: 生产环境中若缺失核心秘钥，系统启动立即抛出异常并终止运行。
- **静态扫描器 (`scan_secrets.py`)**: `scripts/scan_secrets.py` 是一个自包含的扫描脚本，基于 `git ls-files` 对全量代码进行硬编码秘钥与敏感信息深度扫描。
- **日志脱敏 (SecretRedactionFilter)**: 内置 `SecretRedactionFilter` 日志过滤器，自动拦截并抹除日志中的凭证、Authorization Token 及敏感字段。
- **Raw Content 默认隔离**: 环境变量 `GOVTRANS_LOG_RAW_CONTENT` 默认设为 `false`，严禁在日志中明文打印原始机密文本。
- **前后端权限隔离**: API 服务绝不向前端透传任何 Provider 秘钥；前端页面全无接触秘钥的能力与通道。

---

## 2. 机密分级与搜索管控 (Confidentiality & Search Control)

- **机密分级**: 系统支持任务机密性分级（`confidentiality` 字段）。
- **外网搜索管控**: 对于标记为 `CONFIDENTIAL`（机密）或涉密级别的政务任务，系统**强制禁绝外网搜索**（禁用 `tofu-search` 的外部互联网检索功能），所有检索严格限制在本地高安全隔离的 `translation_memory` 与内部私有 `corpus` 库内，防止敏感国情与公文内容外泄。

---

## 3. QueryLeakGuard (检索防泄漏网关)

- **职责**: `services/retrieval/search.py` 中集成了 `QueryLeakGuard` 安全网关。
- **机制**: 在执行 `perform_web_search` 前，自动对查询 Query 进行敏感词过滤与正则审计，拦截可能导致政务机密、内部代号泄露的外部检索请求。
- **官方白名单机制**: 联网检索严格限定在国家级官方权威信源白名单内：
  - `scio.gov.cn` (国务院新闻办公室)
  - `english.scio.gov.cn` (国新办英文网)
  - `gov.cn` (中国政府网)
  - `xinhuanet.com` (新华网)

---

## 4. 审计与合规追溯

- **全链路审计**: 系统对模型调用 (`model_usage`)、术语修改 (`term_audit_log`)、评测与状态变更均生成不可篡改的结构化审计日志。
- **零入库零日志原则**: 确保所有传输与存储过程符合国家信息安全等级保护（等保）及相关合规要求。
