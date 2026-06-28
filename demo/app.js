const state = {
  taskId: null,
  report: null,
  questions: [],
  intakeSubmitted: false,
  isEditingIntake: false,
  activeModal: "intake",
};

const elements = {
  form: document.querySelector("#demandForm"),
  questionsForm: document.querySelector("#questionsForm"),
  analyzeButton: document.querySelector("#analyzeButton"),
  cancelIntakeButton: document.querySelector("#cancelIntakeButton"),
  editIntakeButton: document.querySelector("#editIntakeButton"),
  submitAnswersButton: document.querySelector("#submitAnswersButton"),
  submitQuestionsButton: document.querySelector("#submitQuestionsButton"),
  handoffButton: document.querySelector("#handoffButton"),
  openReportButton: document.querySelector("#openReportButton"),
  closeReportButton: document.querySelector("#closeReportButton"),
  intakeModal: document.querySelector("#intakeModal"),
  questionsModal: document.querySelector("#questionsModal"),
  reportModal: document.querySelector("#reportModal"),
  intakeError: document.querySelector("#intakeError"),
  questionsError: document.querySelector("#questionsError"),
  intakeProgress: document.querySelector("#intakeProgress"),
  questionsProgress: document.querySelector("#questionsProgress"),
  userInput: document.querySelector("#userInput"),
  brandInput: document.querySelector("#brandInput"),
  channelsInput: document.querySelector("#channelsInput"),
  followupInput: document.querySelector("#followupInput"),
  notice: document.querySelector("#notice"),
  currentDemand: document.querySelector("#currentDemand"),
  currentBrand: document.querySelector("#currentBrand"),
  currentChannels: document.querySelector("#currentChannels"),
  projectTitle: document.querySelector("#projectTitle"),
  scoreValue: document.querySelector("#scoreValue"),
  metaGrid: document.querySelector("#metaGrid"),
  fieldsTable: document.querySelector("#fieldsTable"),
  modalQuestionsPanel: document.querySelector("#modalQuestionsPanel"),
  questionCount: document.querySelector("#questionCount"),
  reportPreview: document.querySelector("#reportPreview"),
  reportDocument: document.querySelector("#reportDocument"),
  reportModalTitle: document.querySelector("#reportModalTitle"),
  turnsPanel: document.querySelector("#turnsPanel"),
  handoffPanel: document.querySelector("#handoffPanel"),
};

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await analyzeDemand();
});

elements.questionsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitQuestionAnswers();
});

elements.cancelIntakeButton.addEventListener("click", () => {
  closeIntakeModal();
});

elements.editIntakeButton.addEventListener("click", () => {
  state.isEditingIntake = true;
  openIntakeModal();
});

elements.submitAnswersButton.addEventListener("click", async () => {
  await submitAnswers();
});

elements.handoffButton.addEventListener("click", async () => {
  await generateHandoff();
});

elements.openReportButton.addEventListener("click", () => {
  openReportModal();
});

elements.closeReportButton.addEventListener("click", () => {
  closeReportModal();
});

elements.followupInput.addEventListener("input", () => {
  elements.submitAnswersButton.disabled = !state.taskId;
});

elements.modalQuestionsPanel.addEventListener("click", (event) => {
  const button = event.target.closest("[data-option-field]");
  if (!button) return;
  const field = button.dataset.optionField;
  const value = button.dataset.optionValue || "";
  const input = elements.modalQuestionsPanel.querySelector(`[data-answer-field="${cssEscape(field)}"]`);
  if (!input) return;

  input.value = value;
  input.focus();
  elements.modalQuestionsPanel
    .querySelectorAll(`[data-option-field="${cssEscape(field)}"]`)
    .forEach((item) => item.classList.toggle("selected", item === button));
});

async function analyzeDemand() {
  const userInput = elements.userInput.value.trim();
  const brand = elements.brandInput.value.trim();
  const channels = splitList(elements.channelsInput.value);
  if (!userInput) {
    showIntakeError("请先输入一段原始需求。");
    elements.userInput.focus();
    return;
  }
  if (!brand) {
    showIntakeError("请补充品牌上下文。");
    elements.brandInput.focus();
    return;
  }
  if (channels.length === 0) {
    showIntakeError("请至少填写一个目标渠道。");
    elements.channelsInput.focus();
    return;
  }

  setLoading(true, "正在分析需求...", "intake");
  try {
    const payload = {
      user_input: userInput,
      context: {
        brand,
        target_channel: channels,
      },
    };
    const task = await requestJson("/v1/agents/demand-analysis/tasks", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.taskId = task.demand_task_id;
    state.handoff = null;
    state.intakeSubmitted = true;
    state.isEditingIntake = false;
    state.questions = [];
    renderQuestions();
    renderCurrentIntake(userInput, brand, channels);
    closeIntakeModal();
    await refreshQuestions();
    await refreshReport();
    elements.editIntakeButton.disabled = false;
    elements.handoffButton.disabled = false;
  } catch (error) {
    showIntakeError(error.message);
  } finally {
    setLoading(false, "", "intake");
  }
}

async function refreshQuestions() {
  if (!state.taskId) return;
  const data = await requestJson(`/v1/agents/demand-analysis/tasks/${state.taskId}/questions`);
  state.questions = data.questions || [];
  renderQuestions();
  if (state.questions.length > 0) {
    openQuestionsModal();
  } else {
    closeQuestionsModal();
  }
}

async function refreshReport() {
  if (!state.taskId) return;
  const data = await requestJson(`/v1/agents/demand-analysis/tasks/${state.taskId}/report`);
  state.report = data;
  renderReport(data);
}

async function submitAnswers() {
  if (!state.taskId) return;
  const message = elements.followupInput.value.trim();
  if (!message) {
    showNotice("请先输入一段自由补充需求。", "warning");
    return;
  }

  setLoading(true, "正在提交补充内容...", "followup");
  try {
    await requestJson(`/v1/agents/demand-analysis/tasks/${state.taskId}/followups`, {
      method: "POST",
      body: JSON.stringify({ message, answers: {} }),
    });
    elements.followupInput.value = "";
    await refreshQuestions();
    await refreshReport();
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    setLoading(false, "", "followup");
  }
}

async function submitQuestionAnswers() {
  if (!state.taskId) return;
  const answers = {};
  const missing = [];
  for (const question of state.questions) {
    const input = elements.modalQuestionsPanel.querySelector(`[data-answer-field="${cssEscape(question.field)}"]`);
    const value = input ? input.value.trim() : "";
    if (value) {
      answers[question.field] = value;
    } else {
      missing.push(question.field);
    }
  }
  if (missing.length > 0) {
    showQuestionsError("请回答所有追问后再继续。");
    const firstMissing = elements.modalQuestionsPanel.querySelector(`[data-answer-field="${cssEscape(missing[0])}"]`);
    if (firstMissing) firstMissing.focus();
    return;
  }

  setLoading(true, "正在提交追问答案...", "questions");
  try {
    await requestJson(`/v1/agents/demand-analysis/tasks/${state.taskId}/followups`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    });
    await refreshQuestions();
    await refreshReport();
  } catch (error) {
    showQuestionsError(error.message);
  } finally {
    setLoading(false, "", "questions");
  }
}

async function generateHandoff() {
  if (!state.taskId) return;
  setLoading(true, "正在生成下游任务包...", "handoff");
  try {
    const data = await requestJson(`/v1/agents/demand-analysis/tasks/${state.taskId}/handoff`, {
      method: "POST",
      body: JSON.stringify({
        target_agents: ["cultural_ip_agent", "designer_agent", "marketer_agent"],
      }),
    });
    state.handoff = data.task_packages || [];
    renderHandoff(data.task_packages || []);
    showNotice("下游任务包已生成。", "success");
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    setLoading(false, "", "handoff");
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
  elements.reportPreview.innerHTML = reportPreviewHtml(data);
  elements.reportDocument.classList.remove("empty");
  elements.reportDocument.innerHTML = markdownToHtml(data.report_markdown || "暂无报告。");
  elements.reportModalTitle.textContent = report.project_summary || data.report_id || "需求分析报告";
  elements.openReportButton.disabled = !data.report_markdown;

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
    elements.questionCount.textContent = "0";
    elements.modalQuestionsPanel.innerHTML = "";
    return;
  }
  elements.modalQuestionsPanel.innerHTML = state.questions
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
      (item, index) => `
        <section class="handoff-item">
          <div class="handoff-title">
            <div>
              <h3>${escapeHtml(item.agent_name || item.agent || "agent")}</h3>
              <small>${escapeHtml(item.task || "通用需求交接")}</small>
            </div>
            <button class="secondary-button compact-button" type="button" data-doc-index="${index}">预览详细文档</button>
          </div>
          <div class="handoff-summary">
            <div><span>来源报告</span><strong>${escapeHtml(item.source_report_id || "--")}</strong></div>
            <div><span>输出物</span><strong>${escapeHtml((item.expected_outputs || []).join("、") || "--")}</strong></div>
            <div><span>文档地址</span><strong>${escapeHtml(item.detail_doc?.markdown_path || "--")}</strong></div>
          </div>
          <pre>${escapeHtml(JSON.stringify(withoutMarkdownPayload(item), null, 2))}</pre>
        </section>
      `,
    )
    .join("");
}

elements.handoffPanel.addEventListener("click", (event) => {
  const button = event.target.closest("[data-doc-index]");
  if (!button) return;
  const index = Number(button.dataset.docIndex);
  const packageItem = (state.handoff || [])[index];
  if (!packageItem?.detail_doc?.markdown) return;
  openMarkdownDocument(packageItem.detail_doc.title || `${packageItem.agent}详细任务描述`, packageItem.detail_doc.markdown);
});

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

function reportPreviewHtml(data) {
  const report = data.report_json || {};
  const fields = report.fields || {};
  const summaryItems = [
    ["完整度", `${data.completeness_score ?? report.completeness_score ?? "--"}`],
    ["建议动作", report.recommended_action || "--"],
    ["待确认", `${(report.pending_questions || []).length} 项`],
  ];
  const fieldBadges = ["target_users", "usage_scenarios", "product_categories", "budget_range"]
    .map((name) => fields[name])
    .filter(Boolean)
    .map((field) => `<span>${escapeHtml(displayFieldValue(field.field_value))}</span>`)
    .join("");
  return `
    <div class="report-preview-card">
      <div>
        <p class="eyebrow">Report Snapshot</p>
        <h3>${escapeHtml(report.project_summary || "需求分析报告")}</h3>
      </div>
      <div class="preview-metrics">
        ${summaryItems
          .map(([label, value]) => `<div><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`)
          .join("")}
      </div>
      <div class="field-chip-row">${fieldBadges || "<span>暂无核心字段</span>"}</div>
    </div>
  `;
}

function setLoading(isLoading, message = "", scope = "global") {
  elements.analyzeButton.disabled = isLoading;
  elements.submitAnswersButton.disabled = isLoading || !state.taskId;
  elements.handoffButton.disabled = isLoading || !state.taskId;
  elements.openReportButton.disabled = isLoading || !state.report;
  elements.submitQuestionsButton.disabled = isLoading;
  elements.cancelIntakeButton.disabled = isLoading;
  elements.editIntakeButton.disabled = isLoading || !state.intakeSubmitted;
  elements.analyzeButton.textContent = isLoading && scope === "intake" ? "处理中..." : "开始分析";
  elements.submitQuestionsButton.textContent = isLoading && scope === "questions" ? "处理中..." : "提交追问答案";
  elements.submitAnswersButton.textContent =
    isLoading && scope === "followup" ? "提交中..." : "提交补充并刷新报告";
  elements.handoffButton.textContent = isLoading && scope === "handoff" ? "生成中..." : "生成三类 Agent 任务包";
  setFormControlsDisabled(elements.form, isLoading && scope === "intake");
  setFormControlsDisabled(elements.questionsForm, isLoading && scope === "questions");
  setButtonLoading(scope, isLoading);
  elements.intakeProgress.classList.toggle("hidden", !(isLoading && scope === "intake"));
  elements.questionsProgress.classList.toggle("hidden", !(isLoading && scope === "questions"));
  elements.intakeModal.classList.toggle("is-processing", isLoading && scope === "intake");
  elements.questionsModal.classList.toggle("is-processing", isLoading && scope === "questions");
  if (message) showNotice(message, "success");
}

function setButtonLoading(scope, isLoading) {
  const scopedButtons = {
    intake: elements.analyzeButton,
    questions: elements.submitQuestionsButton,
    followup: elements.submitAnswersButton,
    handoff: elements.handoffButton,
  };
  Object.values(scopedButtons).forEach((button) => button.classList.remove("is-loading"));
  if (isLoading && scopedButtons[scope]) {
    scopedButtons[scope].classList.add("is-loading");
  }
}

function setFormControlsDisabled(form, disabled) {
  form.querySelectorAll("input, textarea, button").forEach((control) => {
    control.disabled = disabled;
  });
}

function openIntakeModal() {
  elements.intakeModal.classList.remove("hidden");
  elements.questionsModal.classList.add("hidden");
  elements.reportModal.classList.add("hidden");
  state.activeModal = "intake";
  document.body.classList.add("modal-open");
  elements.cancelIntakeButton.classList.toggle("hidden", !state.intakeSubmitted);
  elements.intakeError.classList.add("hidden");
  elements.userInput.focus();
}

function closeIntakeModal() {
  if (!state.intakeSubmitted) return;
  elements.intakeModal.classList.add("hidden");
  if (state.questions.length === 0) {
    document.body.classList.remove("modal-open");
    state.activeModal = null;
  }
}

function openQuestionsModal() {
  elements.questionsModal.classList.remove("hidden");
  elements.intakeModal.classList.add("hidden");
  elements.reportModal.classList.add("hidden");
  state.activeModal = "questions";
  document.body.classList.add("modal-open");
  elements.questionsError.classList.add("hidden");
  const firstInput = elements.modalQuestionsPanel.querySelector("[data-answer-field]");
  if (firstInput) firstInput.focus();
}

function closeQuestionsModal() {
  elements.questionsModal.classList.add("hidden");
  if (state.activeModal === "questions") {
    state.activeModal = null;
  }
  if (!state.activeModal) {
    document.body.classList.remove("modal-open");
  }
}

function openReportModal() {
  if (!state.report) return;
  openMarkdownDocument(
    elements.reportModalTitle.textContent || "需求分析报告",
    state.report.report_markdown || "暂无报告。",
  );
}

function openMarkdownDocument(title, markdown) {
  elements.reportModalTitle.textContent = title;
  elements.reportDocument.classList.remove("empty");
  elements.reportDocument.innerHTML = markdownToHtml(markdown || "暂无内容。");
  elements.reportModal.classList.remove("hidden");
  state.activeModal = "report";
  document.body.classList.add("modal-open");
  elements.closeReportButton.focus();
}

function closeReportModal() {
  elements.reportModal.classList.add("hidden");
  if (state.activeModal === "report") {
    state.activeModal = null;
  }
  if (!state.activeModal) {
    document.body.classList.remove("modal-open");
  }
}

function showIntakeError(message) {
  elements.intakeError.classList.remove("hidden");
  elements.intakeError.textContent = message;
}

function showQuestionsError(message) {
  elements.questionsError.classList.remove("hidden");
  elements.questionsError.textContent = message;
}

function renderCurrentIntake(userInput, brand, channels) {
  elements.currentDemand.textContent = userInput;
  elements.currentBrand.textContent = brand;
  elements.currentChannels.textContent = channels.join("、");
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

function displayFieldValue(value) {
  return Array.isArray(value) ? value.join("、") : String(value ?? "");
}

function withoutMarkdownPayload(item) {
  const copy = { ...item };
  if (copy.detail_doc) {
    copy.detail_doc = { ...copy.detail_doc, markdown: "[see detail_doc.markdown_path]" };
  }
  return copy;
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
