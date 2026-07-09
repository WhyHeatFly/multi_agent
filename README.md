# 文化 IP 设计师 Agent

这是一个 Docker 化的文化 IP 设计师 Agent MVP，实现从结构化需求报告到文化 IP 方向、故事内核、符号体系、视觉转译、风险审核、最终报告和下游任务包的基础链路。

## 能力范围

- FastAPI 后端服务。
- PostgreSQL + pgvector 容器部署。
- 本地规则库 + 本地知识库 RAG 检索。
- 文化 IP 方向生成、报告生成、反馈收敛、handoff 任务包。
- Nginx HTTPS 生产反向代理配置。
- Alembic 数据库迁移。

## 真实生成配置

当前版本不使用固定模板生成 IP 方向。必须在 .env 中配置 OpenAI-compatible 模型参数，否则创建任务会返回 503：

`env
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your_api_key
LLM_MODEL=gpt-4.1-mini
` 

没有 LLM_API_KEY 时，系统只允许访问页面、健康检查和知识库入库，不会回退到固定模板。

## 本地启动

```bash
cp .env.example .env
docker compose up --build
```

服务启动后检查：

```bash
curl http://localhost:18080/healthz
curl http://localhost:18080/readyz
```

初始化知识库：

```bash
docker compose exec api python scripts/ingest_knowledge.py
```

创建示例任务：

```bash
curl -X POST http://localhost:18080/v1/agents/cultural-ip/tasks \
  -H "Content-Type: application/json" \
  --data-binary @data/examples/create_task_xihu_wedding.json
```


## Web 控制台

本地启动后直接打开：

```text
http://localhost:18080
```

页面支持填写结构化需求、生成 IP 方向、查看最终报告、提交反馈、生成下游任务包，并可直接触发知识库入库。
## 主要接口

- `POST /v1/agents/cultural-ip/tasks`
- `GET /v1/agents/cultural-ip/tasks/{ip_task_id}/directions`
- `POST /v1/agents/cultural-ip/tasks/{ip_task_id}/feedback`
- `GET /v1/agents/cultural-ip/tasks/{ip_task_id}/report`
- `POST /v1/agents/cultural-ip/tasks/{ip_task_id}/handoff`
- `POST /v1/admin/knowledge/ingest`
- `GET /v1/admin/tasks/{ip_task_id}/debug`
`/v1/admin/*` 接口当前直接开放给应用使用，建议生产部署时通过 Nginx、内网访问或其他认证层限制访问。

## 服务器部署

服务器需要 Docker 和 Docker Compose。

```bash
git clone <repo-url>
cd <repo>
cp .env.example .env
```

编辑 `.env`：

- 设置强密码：`POSTGRES_PASSWORD`
- 设置模型参数：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`
- 放置 HTTPS 证书到 `./certs/fullchain.pem` 和 `./certs/privkey.pem`

启动：

```bash
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml run --rm migrate
docker compose -f docker-compose.prod.yml up -d
```

检查：

```bash
curl https://your-domain.com/healthz
curl https://your-domain.com/readyz
```

## 开发验证

如果本机已安装 Python 依赖：

```bash
pytest
python -m py_compile app/main.py app/services/cultural_ip.py
```

## 说明

当前版本优先保证 MVP 可运行和可部署。真实图数据库、图像检索、版权相似度检测、专家审核闭环和更复杂的异步任务队列属于后续迭代。








