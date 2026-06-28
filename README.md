# multi_agent

需求分析师 Agent MVP：把用户自然语言需求转成结构化需求报告，支持追问澄清、自由补充、报告预览，并生成面向下游 Agent 的任务包和详细 Markdown 交接文档。

## 当前能力

当前项目已经实现：

- 规则分析：意图识别、字段抽取、推断补全、完整度评分、追问生成。
- LLM 增强入口：可选接入 DeepSeek 风格接口，失败时自动回退到规则分析。
- 多轮补充：支持追问答案和自由文本补充，并刷新同一个需求报告。
- 报告生成：输出结构化 JSON 报告和 Markdown 报告。
- Handoff 任务包：为文化 IP、设计、营销等下游 Agent 生成差异化任务包。
- 详细任务文档：为每个下游 Agent 生成本地 Markdown 详细任务描述。
- Demo 页面：强制初始输入弹窗、强制追问弹窗、报告预览弹窗、handoff 文档预览和处理中动效。
- 轻量持久化：SQLite 保存任务和报告，输出目录保存报告文件和 handoff Markdown 文件。

当前尚未实现：

- OSS / CDN 上传 handoff Markdown。
- `detail_doc.url` 在线 Markdown 预览。
- `agent_registry` 下游 Agent 配置表和管理页面。
- `handoff_results` 下游执行结果表。
- 下游 Agent 完成任务后的回调接口。
- Handoff 状态轮询、SSE 或 WebSocket 实时刷新。

相关设计方案见：

- [需求Agent当前实现分析文档.md](需求Agent当前实现分析文档.md)
- [需求Agent下游任务文档URL化与持久化方案.md](需求Agent下游任务文档URL化与持久化方案.md)

## 项目结构

```text
multi_agent/
  demand_agent/
    api.py            # 标准库 HTTP API 和 demo 静态资源服务
    service.py        # 需求分析、报告生成、followup、handoff 主流程
    rules.py          # 规则抽取、推断、评分和追问
    models.py         # 任务、报告、字段等数据模型
    storage.py        # SQLite 和输出文件持久化
    llm_client.py     # DeepSeek 风格 LLM 客户端
    llm_analyzer.py   # LLM 输出校验、修复和规则 fallback
  demo/
    index.html
    app.js
    styles.css
  tests/
    test_demand_agent.py
```

## 运行环境与依赖安装

当前项目只依赖 Python 标准库，没有额外 `pip install` 依赖，也没有前端构建步骤。

需要安装：

- Python 3.9 或更高版本

检查本机是否已安装 Python：

```bash
python3 --version
```

如果能看到类似下面的输出，就可以直接运行：

```text
Python 3.9.6
```

macOS 推荐用 Homebrew 安装 Python：

```bash
brew install python
```

安装完成后再次检查：

```bash
python3 --version
which python3
```

如果系统只有 `python` 没有 `python3`，建议优先安装 Homebrew 版本 Python，并统一使用本文档中的 `python3` 命令。

可选：创建虚拟环境。当前项目没有第三方依赖，不创建虚拟环境也可以运行；如果希望隔离环境，可以执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

退出虚拟环境：

```bash
deactivate
```

## 运行测试

```bash
python3 -m unittest
```

当前测试覆盖主流程、LLM fallback/repair、多轮补充、SQLite 重载、API 错误路径、demo 静态资源和 handoff 文档生成。

## 启动 API

```bash
python3 -m demand_agent.api --host 127.0.0.1 --port 8000 --storage-dir outputs
```

启动后 API 会监听：

```text
http://127.0.0.1:8000
```

默认持久化位置：

- SQLite 数据库：`outputs/demand_agent.sqlite3`
- 报告 JSON：`outputs/demand_reports/{report_id}.json`
- 报告 Markdown：`outputs/demand_reports/{report_id}.md`
- Handoff Markdown：`outputs/handoff_docs/{report_id}_{agent}.md`

## 打开功能展示页

启动 API 后访问：

```text
http://127.0.0.1:8000/demo
```

Demo 页面会调用本地 API，演示：

1. 通过强制弹窗填写原始需求、品牌上下文和目标渠道。
2. 自动分析并生成字段抽取结果、完整度评分和报告。
3. 如果有追问，通过强制追问弹窗回答。
4. 在左侧自由补充需求并刷新报告。
5. 打开完整报告弹窗预览 Markdown 报告。
6. 生成三类下游 Agent 任务包。
7. 打开 handoff 详细任务文档弹窗。

## 启用 LLM 语义分析

默认情况下 LLM 关闭，系统使用内置规则分析。需要接入 DeepSeek 时，在项目根目录创建 `.env`：

```bash
DEMAND_LLM_ENABLED=true
DEEPSEEK_API_KEY=你的APIKey
DEMAND_LLM_BASE_URL=https://api.deepseek.com
DEMAND_LLM_MODEL=deepseek-v4-flash
DEMAND_LLM_TIMEOUT_SECONDS=30
```

可选修复配置：

```bash
DEMAND_LLM_REPAIR_ENABLED=true
DEMAND_LLM_MAX_REPAIR_ATTEMPTS=1
```

`.env` 已在 `.gitignore` 中忽略，不会被提交到仓库。系统启动时会自动读取 `.env`；如果同名系统环境变量已经存在，则优先使用系统环境变量。

LLM 调用失败、超时、关闭或返回非法 JSON 时，系统会自动回退到规则分析，并在报告中记录：

- `analysis_mode`
- `llm_status`
- `llm_model`
- `llm_error`
- `llm_repair_status`
- `llm_repair_attempts`
- `llm_validation_issues`

## API 示例

### 创建需求任务

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

### 获取追问

```bash
curl http://127.0.0.1:8000/v1/agents/demand-analysis/tasks/{demand_task_id}/questions
```

### 提交追问答案

```bash
curl -X POST http://127.0.0.1:8000/v1/agents/demand-analysis/tasks/{demand_task_id}/answers \
  -H 'Content-Type: application/json' \
  -d '{
    "answers": {
      "budget_range": "300元以内"
    }
  }'
```

### 提交自由补充

```bash
curl -X POST http://127.0.0.1:8000/v1/agents/demand-analysis/tasks/{demand_task_id}/followups \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "整体风格更年轻一点，预算控制在300元以内。",
    "answers": {}
  }'
```

### 获取报告

```bash
curl http://127.0.0.1:8000/v1/agents/demand-analysis/tasks/{demand_task_id}/report
```

### 生成下游任务包

```bash
curl -X POST http://127.0.0.1:8000/v1/agents/demand-analysis/tasks/{demand_task_id}/handoff \
  -H 'Content-Type: application/json' \
  -d '{
    "target_agents": ["cultural_ip_agent", "designer_agent", "marketer_agent"]
  }'
```

当前 `handoff` 响应中的 `detail_doc` 结构类似：

```json
{
  "title": "文化IP设计师Agent详细任务描述",
  "format": "markdown",
  "markdown_path": "outputs/handoff_docs/dr_xxx_cultural_ip_agent.md",
  "markdown": "# 文化IP设计师Agent详细任务描述\n..."
}
```

注意：当前没有 `detail_doc.url`，不会上传 OSS/CDN。

## 已实现接口

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| `POST` | `/v1/agents/demand-analysis/tasks` | 创建需求任务 |
| `GET` | `/v1/agents/demand-analysis/tasks/{demand_task_id}/questions` | 获取追问 |
| `POST` | `/v1/agents/demand-analysis/tasks/{demand_task_id}/answers` | 提交追问答案 |
| `POST` | `/v1/agents/demand-analysis/tasks/{demand_task_id}/followups` | 提交自由补充或追问答案 |
| `GET` | `/v1/agents/demand-analysis/tasks/{demand_task_id}/report` | 获取报告 |
| `POST` | `/v1/agents/demand-analysis/tasks/{demand_task_id}/handoff` | 生成下游任务包 |
| `GET` | `/demo`、`/demo/*` | 打开 demo 页面和静态资源 |

## 当前未实现接口

- `GET /v1/agents/demand-analysis/handoffs/{handoff_id}`
- `GET /v1/agents/demand-analysis/tasks/{demand_task_id}/handoffs`
- `POST /v1/agents/demand-analysis/handoffs/{handoff_id}/retry`
- `POST /v1/agents/demand-analysis/handoffs/{handoff_id}/packages/{package_id}/callback`
- `GET /v1/agents/demand-analysis/handoff-documents/{doc_id}/markdown`
- Agent 注册表管理接口

## 当前持久化模型

SQLite 当前只有两张表：

- `demand_tasks`
- `demand_reports`

尚未实现：

- `handoff_batches`
- `handoff_packages`
- `handoff_documents`
- `agent_registry`
- `handoff_results`

Handoff 详细文档目前只写入本地 Markdown 文件，不会独立落库。

## 开发注意事项

- 当前 API 使用 Python 标准库 `http.server`，不是 FastAPI。
- 当前 demo 页面是静态 HTML/JS/CSS，没有前端构建步骤。
- 当前前端 handoff 文档预览只读取 `detail_doc.markdown`，不会 fetch 在线 URL。
- 当前没有登录鉴权、OpenAPI、分页、任务列表和任务删除接口。
- 当前没有下游 Agent 执行状态轮询、SSE 或 WebSocket。
