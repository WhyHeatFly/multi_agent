# multi_agent

需求分析师 Agent MVP：把用户自然语言需求转成结构化需求报告，并生成下游 Agent 任务包。

## 运行测试

```bash
python -m unittest
```

## 启动 API

```bash
python -m demand_agent.api --host 127.0.0.1 --port 8000 --storage-dir outputs
```

## 打开功能展示页

启动 API 后访问：

```text
http://127.0.0.1:8000/demo
```

展示页会调用本地 API，演示需求输入、字段抽取、完整度评分、追问、报告预览和下游 Agent 任务包生成。

## 启用 LLM 语义分析

默认情况下 LLM 关闭，系统使用内置规则分析。需要接入 DeepSeek 时，在项目根目录创建 `.env`：

```bash
DEMAND_LLM_ENABLED=true
DEEPSEEK_API_KEY=你的APIKey
DEMAND_LLM_BASE_URL=https://api.deepseek.com
DEMAND_LLM_MODEL=deepseek-v4-flash
DEMAND_LLM_TIMEOUT_SECONDS=30
```

`.env` 已在 `.gitignore` 中忽略，不会被提交到仓库。系统启动时会自动读取 `.env`；如果同名系统环境变量已经存在，则优先使用系统环境变量。

LLM 调用失败、超时或返回非法 JSON 时，系统会自动回退到规则分析，并在报告中记录 `analysis_mode`、`llm_status`、`llm_model` 和 `llm_error`。

默认会持久化到：

- SQLite 数据库：`outputs/demand_agent.sqlite3`
- 报告 JSON：`outputs/demand_reports/{report_id}.json`
- 报告 Markdown：`outputs/demand_reports/{report_id}.md`

## 示例调用

```bash
curl -X POST http://127.0.0.1:8000/v1/agents/demand-analysis/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "user_input": "为 3 月西湖春游的新婚人群设计一套丝绸伴手礼。",
    "context": {
      "brand": "南浔丝绸文化产业园",
      "target_channel": ["小红书", "线下文旅店"]
    }
  }'
```

已实现接口：

- `POST /v1/agents/demand-analysis/tasks`
- `GET /v1/agents/demand-analysis/tasks/{demand_task_id}/questions`
- `POST /v1/agents/demand-analysis/tasks/{demand_task_id}/answers`
- `GET /v1/agents/demand-analysis/tasks/{demand_task_id}/report`
- `POST /v1/agents/demand-analysis/tasks/{demand_task_id}/handoff`
