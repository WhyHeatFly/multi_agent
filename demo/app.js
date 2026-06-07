const state = {
  taskId: null,
  report: null,
  questions: [],
};

const elements = {
  form: document.querySelector("#demandForm"),
  analyzeButton: document.querySelector("#analyzeButton"),
  submitAnswersButton: document.querySelector("#submitAnswersButton"),
  handoffButton: document.querySelector("#handoffButton"),
  userInput: document.querySelector("#userInput"),
  brandInput: document.querySelector("#brandInput"),
  channelsInput: document.querySelector("#channelsInput"),
  followupInput: document.querySelector("#followupInput"),
  notice: document.querySelector("#notice"),
  projectTitle: document.querySelector("#projectTitle"),
  scoreValue: document.querySelector("#scoreValue"),
  metaGrid: document.querySelector("#metaGrid"),
  fieldsTable: document.querySelector("#fieldsTable"),
  questionsPanel: document.querySelector("#questionsPanel"),
  questionCount: document.querySelector("#questionCount"),
  reportPreview: document.querySelector("#reportPreview"),
  turnsPanel: document.querySelector("#turnsPanel"),
  handoffPanel: document.querySelector("#handoffPanel"),
};

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await analyzeDemand();
});

elements.submitAnswersButton.addEventListener("click", async () => {
  await submitAnswers();
});

elements.handoffButton.addEventListener("click", async () => {
  await generateHandoff();
});

elements.followupInput.addEventListener("input", () => {
  elements.submitAnswersButton.disabled = !state.taskId;
});

elements.questionsPanel.addEventListener("click", (event) => {
  const button = event.target.closest("[data-option-field]");
  if (!button) return;
  const field = button.dataset.optionField;
  const value = button.dataset.optionValue || "";
  const input = document.querySelector(`[data-answer-field="${cssEscape(field)}"]`);
  if (!input) return;

  input.value = value;
  input.focus();
  document
    .querySelectorAll(`[data-option-field="${cssEscape(field)}"]`)
    .forEach((item) => item.classList.toggle("selected", item === button));
});

async function analyzeDemand() {
  const userInput = elements.userInput.value.trim();
  if (!userInput) {
    showNotice("请先输入一段需求。", "error");
    return;
  }

  setLoading(true, "正在分析需求...");
  try {
    const payload = {
      user_input: userInput,
      context: {
        brand: elements.brandInput.value.trim(),
        target_channel: splitList(elements.channelsInput.value),
      },
    };
    const task = await requestJson("/v1/agents/demand-analysis/tasks", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.taskId = task.demand_task_id;
    state.handoff = null;
    await refreshQuestions();
    await refreshReport();
    elements.handoffButton.disabled = false;
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function refreshQuestions() {
  if (!state.taskId) return;
  const data = await requestJson(`/v1/agents/demand-analysis/tasks/${state.taskId}/questions`);
  state.questions = data.questions || [];
  renderQuestions();
}

async function refreshReport() {
  if (!state.taskId) return;
  const data = await requestJson(`/v1/agents/demand-analysis/tasks/${state.taskId}/report`);
  state.report = data;
  renderReport(data);
}

async function submitAnswers() {
  if (!state.taskId) return;
  const answers = {};
  for (const question of state.questions) {
    const input = document.querySelector(`[data-answer-field="${cssEscape(question.field)}"]`);
    if (input && input.value.trim()) {
      answers[question.field] = input.value.trim();
    }
  }
  const message = elements.followupInput.value.trim();
  if (Object.keys(answers).length === 0 && !message) {
    showNotice("请至少填写一个追问答案，或输入一段自由补充需求。", "warning");
    return;
  }

  setLoading(true, "正在提交补充内容...");
  try {
    await requestJson(`/v1/agents/demand-analysis/tasks/${state.taskId}/followups`, {
      method: "POST",
      body: JSON.stringify({ message, answers }),
    });
    elements.followupInput.value = "";
    await refreshQuestions();
    await refreshReport();
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function generateHandoff() {
  if (!state.taskId) return;
  setLoading(true, "正在生成下游任务包...");
  try {
    const data = await requestJson(`/v1/agents/demand-analysis/tasks/${state.taskId}/handoff`, {
      method: "POST",
      body: JSON.stringify({
        target_agents: ["cultural_ip_agent", "designer_agent", "marketer_agent"],
      }),
    });
    renderHandoff(data.task_packages || []);
    showNotice("下游任务包已生成。", "success");
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `请求失败：${response.status}`);
  }
  return data;
}

function renderReport(data) {
  const report = data.report_json || {};
  elements.projectTitle.textContent = report.project_summary || "需求分析报告";
  elements.scoreValue.textContent = data.completeness_score ?? report.completeness_score ?? "--";
  renderMeta(data, report);
  renderFields(report.fields || {});
  renderTurns(report.conversation_turns || []);
  elements.reportPreview.classList.remove("empty");
  elements.reportPreview.innerHTML = markdownToHtml(data.report_markdown || "暂无报告。");

  const llmStatus = report.llm_status;
  if (report.llm_repair_status === "success") {
    showNotice("LLM 输出已自动修复后用于报告生成。", "success");
  } else if (report.analysis_mode === "rules_fallback" && llmStatus && llmStatus !== "disabled") {
    showNotice(`LLM 未成功，本次已回退到规则分析。错误：${report.llm_error || llmStatus}`, "warning");
  } else if (report.analysis_mode === "llm_enhanced") {
    showNotice("LLM 语义增强分析已完成。", "success");
  } else {
    showNotice("规则分析已完成。", "success");
  }
}

function renderMeta(data, report) {
  const rows = [
    ["任务 ID", report.demand_task_id || state.taskId || "--", false],
    ["报告 ID", data.report_id || report.report_id || "--", false],
    ["任务状态", data.status || "--", false],
    ["分析模式", badge(report.analysis_mode || "--", report.analysis_mode), true],
    ["LLM 状态", badge(report.llm_status || "--", report.llm_status), true],
    ["LLM 模型", report.llm_model || "--", false],
    ["修复状态", badge(report.llm_repair_status || "not_needed", report.llm_repair_status), true],
    ["修复次数", String(report.llm_repair_attempts ?? 0), false],
  ];
  elements.metaGrid.innerHTML = rows
    .map(
      ([label, value, isHtml]) =>
        `<div><span>${escapeHtml(label)}</span><strong>${isHtml ? value : escapeHtml(value)}</strong></div>`,
    )
    .join("");
}

function renderFields(fields) {
  const entries = Object.values(fields);
  if (entries.length === 0) {
    elements.fieldsTable.innerHTML = '<tr><td colspan="4" class="empty-cell">暂无字段。</td></tr>';
    return;
  }
  elements.fieldsTable.innerHTML = entries
    .map((field) => {
      const value = Array.isArray(field.field_value)
        ? field.field_value.join("、")
        : String(field.field_value ?? "");
      const confidence =
        typeof field.confidence === "number" ? `${Math.round(field.confidence * 100)}%` : "--";
      return `
        <tr>
          <td>${escapeHtml(field.field_name)}</td>
          <td>${escapeHtml(value)}</td>
          <td>${badge(field.source, field.source)}</td>
          <td>${escapeHtml(confidence)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderQuestions() {
  elements.questionCount.textContent = String(state.questions.length);
  elements.submitAnswersButton.disabled = !state.taskId;
  if (state.questions.length === 0) {
    elements.questionsPanel.className = "question-list empty";
    elements.questionsPanel.textContent = "暂无待确认问题。";
    return;
  }
  elements.questionsPanel.className = "question-list";
  elements.questionsPanel.innerHTML = state.questions
    .map(
      (question) => `
        <div class="question-item">
          <p>${escapeHtml(question.question)}</p>
          <div class="option-row">${(question.options || [])
            .map(
              (option) => `
                <button
                  class="option-pill"
                  type="button"
                  data-option-field="${escapeHtml(question.field)}"
                  data-option-value="${escapeHtml(option)}"
                >${escapeHtml(option)}</button>
              `,
            )
            .join("")}</div>
          <input data-answer-field="${escapeHtml(question.field)}" placeholder="填写答案，或输入一个选项" />
        </div>
      `,
    )
    .join("");
}

function renderTurns(turns) {
  if (turns.length === 0) {
    elements.turnsPanel.className = "turn-list empty";
    elements.turnsPanel.textContent = "暂无补充记录。";
    return;
  }
  elements.turnsPanel.className = "turn-list";
  elements.turnsPanel.innerHTML = turns
    .map((turn) => {
      const changed = (turn.changed_fields || []).map((field) => field.field_name).join("、") || "无字段变化";
      const answers = Object.keys(turn.answers || {}).length
        ? JSON.stringify(turn.answers, null, 2)
        : "未填写追问答案";
      return `
        <section class="turn-item">
          <div class="turn-title">
            <strong>第 ${escapeHtml(turn.turn_index || "-")} 轮补充</strong>
            ${badge(turn.llm_status || turn.analysis_mode || "--", turn.llm_status || turn.analysis_mode)}
          </div>
          <p>${escapeHtml(turn.message || "仅提交追问答案")}</p>
          <pre>${escapeHtml(answers)}</pre>
          <small>字段变化：${escapeHtml(changed)}</small>
        </section>
      `;
    })
    .join("");
}

function renderHandoff(packages) {
  if (packages.length === 0) {
    elements.handoffPanel.className = "handoff-grid empty";
    elements.handoffPanel.textContent = "暂无任务包。";
    return;
  }
  elements.handoffPanel.className = "handoff-grid";
  elements.handoffPanel.innerHTML = packages
    .map(
      (item) => `
        <section class="handoff-item">
          <div class="turn-title">
            <h3>${escapeHtml(item.agent || "agent")}</h3>
            ${badge(item.clarification_status || "--", item.clarification_status)}
          </div>
          <p>${escapeHtml(item.execution_brief || item.task || "暂无任务目标。")}</p>
          ${renderWarningList(item.handoff_warnings || [])}
          <dl class="handoff-meta">
            <div><dt>Markdown</dt><dd>${escapeHtml(item.brief_files?.markdown_path || "--")}</dd></div>
            <div><dt>JSON</dt><dd>${escapeHtml(item.brief_files?.json_path || "--")}</dd></div>
          </dl>
          <div class="handoff-summary">
            <div>
              <strong>输入摘要</strong>
              <pre>${escapeHtml(JSON.stringify(item.inputs || {}, null, 2))}</pre>
            </div>
            <div>
              <strong>约束摘要</strong>
              <pre>${escapeHtml(JSON.stringify(item.constraints || {}, null, 2))}</pre>
            </div>
          </div>
          <details>
            <summary>查看完整 JSON</summary>
            <pre>${escapeHtml(JSON.stringify(item, null, 2))}</pre>
          </details>
          ${
            item.brief_markdown
              ? `<details><summary>查看 Markdown Brief</summary><pre>${escapeHtml(item.brief_markdown)}</pre></details>`
              : ""
          }
        </section>
      `,
    )
    .join("");
}

function renderWarningList(warnings) {
  if (!warnings.length) return "";
  return `<ul class="handoff-warnings">${warnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function markdownToHtml(markdown) {
  const lines = markdown.split("\n");
  const html = [];
  let inList = false;

  for (const line of lines) {
    if (line.startsWith("# ")) {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
      html.push(`<h1>${escapeHtml(line.slice(2))}</h1>`);
    } else if (line.startsWith("## ")) {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
      html.push(`<h2>${escapeHtml(line.slice(3))}</h2>`);
    } else if (line.startsWith("- ")) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${escapeHtml(line.slice(2))}</li>`);
    } else if (line.trim()) {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
      html.push(`<p>${escapeHtml(line)}</p>`);
    }
  }
  if (inList) html.push("</ul>");
  return html.join("");
}

function setLoading(isLoading, message = "") {
  elements.analyzeButton.disabled = isLoading;
  elements.submitAnswersButton.disabled = isLoading || !state.taskId;
  elements.handoffButton.disabled = isLoading || !state.taskId;
  elements.analyzeButton.textContent = isLoading ? "处理中..." : "分析需求";
  if (message) showNotice(message, "success");
}

function showNotice(message, type = "success") {
  elements.notice.className = `notice ${type}`;
  elements.notice.textContent = message;
}

function splitList(value) {
  return value
    .split(/[、,\s，]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function badge(value, className = "") {
  return `<span class="badge ${escapeHtml(className || "")}">${escapeHtml(value || "--")}</span>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function cssEscape(value) {
  if (window.CSS && window.CSS.escape) return window.CSS.escape(value);
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}
