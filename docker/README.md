# GovTrans Docker 化基础设施指南

本项目提供了一套完整、健壮、生产级别的 Docker 容器化编排方案，用于一键部署 GovTrans（中英政府公文翻译平台）的开发与运行环境。

## 基础设施架构
- **db**: PostgreSQL 16 数据库，并集成 `pgvector` 向量扩展，用于嵌入向量存储与检索。
- **redis**: Redis 7 内存数据库，用于缓存与异步任务。
- **minio**: MinIO 对象存储，用于存放双语语料与原始文档（附带自动创建 `govtrans-corpus` bucket 的初始化容器）。
- **api**: 基于 FastAPI 的后端服务，支持 Alembic 数据库迁移自动升级及 Uvicorn 异步服务。
- **web**: 前端 Vite SPA 应用，由 Nginx 伺服，并正确配置了针对 SSE (Server-Sent Events) 流式传输的反向代理（关闭 buffering，延长超时时间）。
- **tofu** *(可选)*: Web 研究/检索核心（ChatUI），使用 Compose Profile 隔离，支持按需启动或在宿主机独立运行。

---

## 快速启动步骤

1. **复制并配置环境变量**
   在项目根目录下复制 `.env.example` 并生成 `.env` 文件：
   ```bash
   cp .env.example .env
   ```

2. **配置大模型 API Key**
   使用文本编辑器打开 `.env` 文件，在 `DASHSCOPE_API_KEY` 项中填入您真实的阿里云百炼 (DashScope) API Key：
   ```env
   DASHSCOPE_API_KEY=your_real_dashscope_api_key_here
   ```
   *(注意：`.env` 文件已被 `.gitignore` 忽略，切勿将其提交至代码仓库。)*

3. **启动 GovTrans 核心服务**
   运行以下命令后台构建并启动所有核心服务：
   ```bash
   docker compose up -d
   ```
   *首次启动时会编译前后端镜像，请耐心等待构建完成。*

4. **启动可选的 ToFu 搜索服务 (Profile)**
   如果您的翻译工作流需要调用 ToFu 检索增强服务，可以通过以下命令带 profile 启动：
   ```bash
   docker compose --profile tofu up -d
   ```
   *提示：ToFu 源码位于仓库外部（默认路径 `../chatui`）。您也可以选择在宿主机上直接运行 `python server.py`（端口 15000）。*

---

## 常用服务访问与测试

- **前端 Web 界面**: [http://localhost:8080](http://localhost:8080)
- **后端 API 文档 (Swagger)**: [http://localhost:8100/docs](http://localhost:8100/docs)
- **API 健康检查**: [http://localhost:8100/healthz](http://localhost:8100/healthz)
- **MinIO 管理后台**: [http://localhost:9001](http://localhost:9001) (默认账号密码: `govtrans` / `change-me-in-.env`)

## 常用维护命令

- **查看实时日志**:
  ```bash
  docker compose logs -f
  ```
- **查看特定服务日志**:
  ```bash
  docker compose logs -f api
  ```
- **停止并清理容器**:
  ```bash
  docker compose down
  ```
- **停止并清空所有持久化数据卷（重置数据库与 MinIO）**:
  ```bash
  docker compose down -v
  ```
