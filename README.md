# GovTrans

面向政务文本的多语种翻译生产系统。它把文档解析、术语抽取、官方资料检索、全文翻译、
语义/文体审校、增强定稿和发布质检组织成可恢复的持久化流水线。

本仓库已经包含从空白电脑部署所需的应用代码、数据库迁移、前后端依赖锁、Docker
编排，以及与当前异步任务接口匹配的 Tofu Agent 运行时。目标机器不需要预装 Python、
Node.js、PostgreSQL、Redis、MinIO 或 Tofu；只需要 Git（或解压工具）和 Docker Compose。

## 1. 交付内容

Docker Compose 会启动四个服务：

| 服务 | 作用 | 宿主机端口 | 持久化 |
| --- | --- | --- | --- |
| `web` | Nginx + React 前端，同时反向代理 API/SSE | `8080`，默认监听所有网卡 | 无 |
| `api` | FastAPI、流水线、Alembic 自动迁移、SCIO Chromium | `8100`，仅本机 | PostgreSQL |
| `tofu` | 随包交付的 Tofu Agent 0.17 sidecar | `15001`，仅本机 | 任务为进程内状态 |
| `db` | PostgreSQL 16 + pgvector | 不对宿主机开放 | Docker 卷 `pgdata` |

Redis 和 MinIO 没有进入必需服务，因为当前代码没有对它们进行任何实际读写。删掉无效
依赖可以减少空白机器上的安装时间、内存占用和攻击面。

随包 Tofu 位于 `vendor/tofu-agent/`，其 wheel、依赖锁、MIT License 和 SHA-256 都已
固定。Docker 构建会先验证 wheel 哈希，再安装；新机器不需要另行 clone Tofu，也不会
带走本机的 Tofu 对话、数据库、日志、上传文件或密钥。上游项目见
[rangehow/ToFu](https://github.com/rangehow/ToFu)。

## 2. 机器要求

推荐部署环境：

- Ubuntu 22.04 / 24.04 64 位；x86_64 是当前验证环境。
- 至少 4 核 CPU、8 GB 内存、25 GB 可用磁盘；16 GB 内存更适合并发文档与语料同步。
- 不需要 GPU。模型通过 OpenAI-compatible API 调用，默认配置为阿里云百炼 DashScope。
- 首次构建需要访问 Docker Hub、PyPI/npm 镜像源和 DashScope；同步官方语料时还需访问
  SCIO 官方站点。

Windows 建议使用 Docker Desktop 的 WSL 2 后端，并在 WSL Ubuntu 终端执行本文命令；
macOS 使用 Docker Desktop。安装入口分别见
[Windows 官方说明](https://docs.docker.com/desktop/setup/install/windows-install/)和
[macOS 官方说明](https://docs.docker.com/desktop/setup/install/mac-install/)。Docker 官方
条款指出，政府实体使用 Docker Desktop 需要付费订阅；政务环境更建议直接使用 Linux
Docker Engine，并由本单位确认许可要求。

## 3. 空白 Ubuntu 从零安装

以下流程适用于一台尚未安装开发环境的 Ubuntu 服务器。

### 3.1 安装 Git 和 Docker Engine

先安装基础工具：

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
```

按 Docker 官方 apt 仓库方式安装 Engine、Buildx 和 Compose 插件（提供
`docker compose` 命令）：

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run --rm hello-world
sudo docker compose version
```

这些命令来自 [Docker Ubuntu 官方安装文档](https://docs.docker.com/engine/install/ubuntu/)。
为了直接运行下文的部署脚本，可按官方 post-install 文档把专用部署用户加入
`docker` 组，然后重新登录；注意该组拥有近似 root 的宿主机权限。如果不加入该组，
`init` 照常以普通用户运行，但之后所有调用 Docker 的脚本命令都需要加 `sudo`，例如
`sudo ./scripts/deploy.sh up`。

### 3.2 把干净部署包放到新电脑

在当前开发电脑的项目根目录生成不含 `.env`、数据库、缓存和日志的交付包：

```bash
./scripts/package_deployment.sh
```

打包脚本只收录 Git 中已跟踪以及未跟踪但未被忽略的项目文件，会自动排除
真实 `.env`、数据库、`build/`、Tofu 历史/记忆、缓存和 IDE 状态。它会在 `release/`
生成：

```text
govtrans-deploy-YYYYMMDDTHHMMSSZ.tar.gz
govtrans-deploy-YYYYMMDDTHHMMSSZ.tar.gz.sha256
```

把这两个文件复制到新电脑，先校验再解压：

```bash
sha256sum -c govtrans-deploy-*.tar.gz.sha256
tar -xzf govtrans-deploy-*.tar.gz
cd govtrans
sha256sum -c vendor/tofu-agent/SHA256SUMS
```

macOS 没有 `sha256sum` 时，分别使用
`shasum -a 256 -c govtrans-deploy-*.tar.gz.sha256` 和
`shasum -a 256 -c vendor/tofu-agent/SHA256SUMS`。校验清单只记录相对文件名，
可在不同电脑间移动。

如果代码已经放在私有 Git 仓库，也可以直接 `git clone <你的仓库地址>`，然后进入仓库
根目录。不要把旧机器的 `.env` 提交到 Git。

### 3.3 生成部署配置

运行初始化脚本：

```bash
./scripts/deploy.sh init
```

脚本会：

- 从 `.env.production.example` 创建权限为 `0600` 的 `.env`；
- 生成 URL-safe 的 PostgreSQL 密码；
- 生成 GovTrans 调用 Tofu sidecar 的内部 Bearer Token；
- 校验随包 Tofu wheel 的 SHA-256；
- 已存在 `.env` 时保留原值，只补齐尚未生成的占位符。

然后编辑 `.env`：

```bash
nano .env
```

至少替换这一项：

```dotenv
DASHSCOPE_API_KEY=你的真实百炼APIKey
```

如使用其他 OpenAI-compatible 服务，同时修改：

```dotenv
DASHSCOPE_BASE_URL=https://你的服务/v1
TRANSLATOR_MODEL=实际模型名
REVIEW_MODEL=实际模型名
FAST_MODEL=实际模型名
```

`TOFU_API_KEY` 是容器之间的内部认证 Token，不是模型 API Key，不要把两者设成同一个值。

### 3.4 一键构建并启动

```bash
./scripts/deploy.sh up
```

首次运行会下载基础镜像、安装锁定的 Python/npm 依赖并安装 Chromium，通常需要
10–30 分钟，取决于网络和 CPU；后续启动会复用缓存。脚本会等到数据库、Tofu、API 和
Web 全部健康才返回，不需要凭日志猜测是否启动完成。

检查状态：

```bash
./scripts/deploy.sh doctor
./scripts/deploy.sh status
```

全部通过后访问：

```text
本机：   http://localhost:8080
局域网： http://服务器IP:8080
```

API 调试口 `127.0.0.1:8100` 和 Tofu 调试口 `127.0.0.1:15001` 默认只允许服务器本机
访问；浏览器业务请求统一经 8080 的 Nginx 代理。

### 3.5 做一次真实链路验证

先打开页面翻译一小段非敏感测试文本。也可以执行一次会产生少量模型费用的
Tofu → Provider 冒烟调用：

```bash
docker compose exec -T api python scripts/smoke_agent_call.py
```

成功时会打印任务 ID、终态和用量，不会打印密钥。

## 4. 网络与安全边界

默认 `GOVTRANS_BIND_ADDRESS=0.0.0.0`，便于局域网访问。只在本机使用时改为：

```dotenv
GOVTRANS_BIND_ADDRESS=127.0.0.1
```

修改后重新运行 `./scripts/deploy.sh up`。

当前应用没有面向公网的账号登录/RBAC。不要把 8080 直接暴露到互联网；公网或政务网
部署应在前面增加 HTTPS、统一身份认证、访问控制、审计集中存储和出口策略。Docker
发布端口可能绕过部分 ufw 规则，防火墙策略应写入 `DOCKER-USER` 链，具体以
[Docker 防火墙说明](https://docs.docker.com/engine/network/packet-filtering-firewalls/)为准。

`.env` 含真实模型密钥和数据库密码：

- 必须保持 `chmod 600 .env`；
- 不要通过聊天、邮件或 Git 发送；
- 部署包脚本会主动排除所有真实 `.env*` 文件；
- 若密钥曾进入 Git 或日志，应立即在 Provider 控制台轮换。

受限网络可在 `.env` 设置镜像源：

```dotenv
PIP_INDEX_URL=https://你的可信PyPI镜像/simple
NPM_REGISTRY=https://你的可信npm镜像
```

基础 Docker 镜像仍需通过单位允许的 Docker Registry mirror 获取。

## 5. 日常运维

查看全部日志：

```bash
./scripts/deploy.sh logs
```

只查看一个服务：

```bash
./scripts/deploy.sh logs api
./scripts/deploy.sh logs tofu
./scripts/deploy.sh logs db
./scripts/deploy.sh logs web
```

停止服务但保留全部数据：

```bash
./scripts/deploy.sh stop
```

再次启动：

```bash
./scripts/deploy.sh up
```

API 每次启动都会先执行 `alembic upgrade head`。活动翻译任务、句段、版本、QA 和事件
由 PostgreSQL 持久化；浏览器刷新或 API 重启不会清空它们。Tofu 的单次执行句柄是进程
内状态，因此重启 Tofu 时不要同时主动发起新翻译，GovTrans 会依据自己的持久化阶段状态
恢复或明确报错。

## 6. 备份、恢复与迁移

创建 PostgreSQL 自包含格式备份：

```bash
./scripts/deploy.sh backup
```

输出位于 `backups/govtrans-时间.dump`，同时生成 `.sha256`。把两者复制到独立磁盘或备份
系统；`backups/` 不会进入 Git 或干净部署包。

恢复会替换当前数据库对象。脚本要求精确文件路径和显式 `--yes`，并在恢复前自动再做
一次当前库备份：

```bash
./scripts/restore_backup.sh /绝对路径/govtrans-时间.dump --yes
```

迁移到另一台电脑时：

1. 在旧机运行 `./scripts/deploy.sh backup`。
2. 生成并复制干净部署包以及 `.dump`/`.sha256`，不要复制旧 `.env`。
3. 在新机执行第 3 章的安装、`init` 和 `up`。
4. 校验备份 SHA-256 后执行恢复脚本。
5. 运行 `./scripts/deploy.sh doctor` 并在页面抽查历史任务。

如果只需要一套全新空库，第 1、4 步直接跳过。当前开发目录中的 SQLite
`data/govtrans.db` 不会自动塞进生产包；这是刻意的干净边界。

## 7. 升级与回滚

升级前先备份：

```bash
./scripts/deploy.sh backup
```

替换代码或 `git pull --ff-only` 后运行：

```bash
sha256sum -c vendor/tofu-agent/SHA256SUMS
./scripts/deploy.sh up
./scripts/deploy.sh doctor
```

`up` 会重建变更镜像并自动执行数据库迁移。若升级包含数据库迁移，不要只回退代码镜像；
应使用升级前的数据库备份做成对回滚。

绝不要把 `docker compose down -v` 当成普通停止命令：`-v` 会删除 PostgreSQL 持久卷，
只有在确认已有可恢复备份且确实要彻底重置时才可使用。

## 8. 常见故障

### `Docker is not installed` / `Docker daemon is unavailable`

确认 `docker compose version` 和 `docker info` 都能成功。Linux 普通用户若没有 daemon
权限，可暂时用 `sudo`，或按 Docker 官方 post-install 流程配置权限后重新登录。

### `Set DASHSCOPE_API_KEY in .env`

`.env` 仍是占位符或空值。填入真实 key 后重新执行 `./scripts/deploy.sh up`。不要在
`docker-compose.yml` 或前端代码中硬编码 key。

### Tofu 一直 unhealthy

```bash
./scripts/deploy.sh logs tofu
sha256sum -c vendor/tofu-agent/SHA256SUMS
```

常见原因是 Provider endpoint/model 配置错误、依赖下载不完整或内存不足。Tofu 默认
限制 4 GB；小内存机器可降低并发 `TOFU_MAX_CONCURRENCY`，但不建议把内存限制降到 2 GB
以下。

### API unhealthy

```bash
./scripts/deploy.sh logs api
docker compose exec -T db pg_isready -U govtrans -d govtrans
```

重点检查数据库迁移错误、模型 key 缺失以及 Tofu URL/Token 是否一致。

### 页面能打开但进度不动

时间线通过 SSE 实时接收持久化事件，断线时每 2.5 秒补拉；执行阶段每 8 秒写入一次不
虚增百分比的存活事件。先看 `api` 与 `tofu` 日志。如果超过 30 秒没有业务事件，页面会
明确显示响应较慢，而不是伪造完成比例。

### 端口冲突

在 `.env` 修改：

```dotenv
GOVTRANS_PORT=18080
API_PORT=18100
TOFU_PORT=15011
```

然后重新运行 `./scripts/deploy.sh up`。

## 9. 本地开发（可选）

生产部署不需要本节。开发环境推荐 Python 3.12、Node 20：

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps .
python -m playwright install --with-deps chromium

cp .env.example .env
# 填写 DASHSCOPE_API_KEY，并指向一个已启动的 Tofu
python -m alembic upgrade head
make dev-api
```

另开终端：

```bash
cd apps/web
npm ci
npm run dev
```

测试与构建：

```bash
make scan
make test
cd apps/web && npm run build
```

## 10. 产品能力与文档

- 支持 19 种语言的任意不同方向，语言对级隔离术语、翻译记忆与语料增强。
- 短文可并行执行分析、术语与翻译；长文按连续批次继承上下文。
- 14 阶段流水线自动审校、增强定稿，并以有界循环阻断 critical/major 问题。
- 运行、阶段、句段、模型用量、QA 和事件全链路入库；SSE 支持游标续传。
- Word、XLIFF、TMX 导出按任务语言动态生成语言标签。

进一步资料：

- `docs/ARCHITECTURE.md`：总体架构与数据流
- `docs/TRANSLATION_PIPELINE.md`：14 阶段流水线
- `docs/SECURITY.md`：密钥与机密分级管控
- `docker/README.md`：Compose 文件速查
- `vendor/tofu-agent/README.md`：随包 Tofu 来源与完整性信息

“政府级”不仅是译文质量。正式上线前仍需按实际网络完成统一身份认证/RBAC、TLS、静态
加密、集中审计、备份演练、等保/涉密边界评估，以及经专家锁定的黄金集回归门禁。
