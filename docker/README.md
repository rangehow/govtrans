# GovTrans Compose 速查

完整的空白机器安装、配置、备份、恢复和排障流程以根目录 `README.md` 为准。

当前 Compose 包含：

- `db`：PostgreSQL 16 + pgvector，数据卷 `pgdata`；
- `tofu`：由 `docker/tofu-agent.Dockerfile` 安装随包 Tofu Agent wheel；
- `api`：启动前自动执行 Alembic 迁移；
- `web`：Nginx 静态前端和 API/SSE 反向代理。

推荐只通过封装脚本操作：

```bash
./scripts/deploy.sh init
# 编辑 .env，填入 DASHSCOPE_API_KEY
./scripts/deploy.sh up
./scripts/deploy.sh doctor
```

常用命令：

```bash
./scripts/deploy.sh status
./scripts/deploy.sh logs api
./scripts/deploy.sh backup
./scripts/deploy.sh stop
```

`stop` 保留数据。不要把 `docker compose down -v` 当作普通停止命令，因为 `-v` 会删除
PostgreSQL 持久卷。
