# multi_agent

需求分析师 Agent MVP：把用户自然语言需求转成结构化需求报告，并生成下游 Agent 任务包。

## 运行测试

```bash
python3 -m unittest
```

## 启动 API

```bash
python3 -m demand_agent.api --host 127.0.0.1 --port 8000 --storage-dir outputs
```

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
