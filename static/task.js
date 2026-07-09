const state = {
  taskId: new URLSearchParams(window.location.search).get("id") || "",
  tasks: [],
  currentTask: null,
  directions: [],
  report: null,
  handoff: null,
  reportDirectionId: "",
  activeTab: "directions",
  activeAdjustTab: "direction",
};

const $ = (id) => document.getElementById(id);

function joinItems(items, fallback = "-") {
  return Array.isArray(items) && items.length ? items.join("、") : fallback;
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function statusLabel(status) {
  const labels = {
    completed: "报告已生成",
    failed: "失败",
    revised: "已调整",
    report_revised: "报告已调整",
    direction_revised: "方向已调整",
    directions_ready: "方向已生成",
    created: "已创建",
    retrieval: "检索中",
    keyword_expansion: "关键词扩展",
    llm_generating: "生成中",
    final_report: "报告中",
  };
  return labels[status] || status || "未知";
}

function statusClass(status) {
  if (["completed", "report_revised", "direction_revised", "directions_ready"].includes(status)) {
    return "status-completed";
  }
  if (status === "failed") return "status-failed";
  return "status-running";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

function renderMarkdown(markdown) {
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let listType = null;
  let inCode = false;
  let codeLines = [];

  function closeList() {
    if (listType) {
      html.push(`</${listType}>`);
      listType = null;
    }
  }

  function openList(type) {
    if (listType !== type) {
      closeList();
      html.push(`<${type}>`);
      listType = type;
    }
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (line.trim().startsWith("```")) {
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeLines = [];
        inCode = false;
      } else {
        closeList();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeLines.push(rawLine);
      continue;
    }
    if (!line.trim()) {
      closeList();
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    if (/^[-*_]{3,}$/.test(line.trim())) {
      closeList();
      html.push("<hr>");
      continue;
    }
    const unordered = line.match(/^[-*]\s+(.+)$/);
    if (unordered) {
      openList("ul");
      html.push(`<li>${renderInlineMarkdown(unordered[1])}</li>`);
      continue;
    }
    const ordered = line.match(/^\d+[.)]\s+(.+)$/);
    if (ordered) {
      openList("ol");
      html.push(`<li>${renderInlineMarkdown(ordered[1])}</li>`);
      continue;
    }
    closeList();
    html.push(`<p>${renderInlineMarkdown(line)}</p>`);
  }
  if (inCode) html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  closeList();
  return html.join("\n");
}

function riskClass(level) {
  const normalized = String(level || "").toLowerCase();
  if (normalized.includes("high")) return "risk-high";
  if (normalized.includes("medium")) return "risk-medium";
  return "risk-low";
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  window.setTimeout(() => el.classList.remove("show"), 2800);
}
function setFeedbackStatus(kind, message = "", tone = "") {
  const el = kind === "report" ? $("reportFeedbackStatus") : $("directionFeedbackStatus");
  el.textContent = message;
  el.className = `feedback-status ${tone}`.trim();
}

function setPageError(message = "") {
  const el = $("taskError");
  el.textContent = message;
  el.classList.toggle("active", Boolean(message));
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    throw new Error(typeof payload === "string" ? payload : payload.detail || "请求失败");
  }
  return payload;
}

function activateTab(tabName) {
  state.activeTab = tabName;
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-view").forEach((view) => view.classList.remove("active"));
  $(`${tabName}View`).classList.add("active");
  syncFeedbackLauncher();
}

function scrollToDetailTop() {
  const target = document.querySelector(".detail-topbar") || document.body;
  target.scrollIntoView({ block: "start", behavior: "smooth" });
}
function feedbackTabForPage(tabName = state.activeTab) {
  if (tabName === "directions") return "direction";
  if (tabName === "report") return "report";
  return "";
}

function feedbackLabel(tabName) {
  return tabName === "report" ? "报告反馈" : "方向反馈";
}

function syncFeedbackLauncher() {
  const adjustTab = feedbackTabForPage();
  const panel = $("agentPanel");
  if (!adjustTab) {
    closeAgentPanel();
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  state.activeAdjustTab = adjustTab;
  const label = feedbackLabel(adjustTab);
  $("agentPanelToggle").textContent = label;
  $("agentPanelTitle").textContent = label;
  switchAdjustTab(adjustTab);
}


function openAgentPanel(tabName = feedbackTabForPage()) {
  if (!tabName) return;
  const panel = $("agentPanel");
  panel.classList.remove("collapsed");
  $("agentPanelToggle").setAttribute("aria-expanded", "true");
  state.activeAdjustTab = tabName;
  $("agentPanelTitle").textContent = feedbackLabel(tabName);
  switchAdjustTab(tabName);
}

function closeAgentPanel() {
  const panel = $("agentPanel");
  panel.classList.add("collapsed");
  $("agentPanelToggle").setAttribute("aria-expanded", "false");
}

function switchAdjustTab(tabName) {
  document.querySelectorAll(".agent-panel-view").forEach((view) => view.classList.remove("active"));
  $(`${tabName}AdjustView`).classList.add("active");
}

function setButtonLoading(button, isLoading, loadingText) {
  if (!button) return;
  if (isLoading) {
    button.dataset.defaultText = button.textContent;
    button.textContent = loadingText;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.defaultText || button.textContent;
    delete button.dataset.defaultText;
  }
}

function safeFilename(value, fallback) {
  return String(value || fallback)
    .replace(/[\\/:*?"<>|]/g, "_")
    .replace(/\s+/g, "_")
    .slice(0, 80);
}

function downloadText(filename, content, type = "text/plain;charset=utf-8") {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function renderTaskHeader() {
  const task = state.currentTask;
  if (!task) {
    $("taskTitle").textContent = state.taskId ? "未找到任务" : "缺少任务 ID";
    $("taskSubtitle").textContent = state.taskId ? "请检查链接是否正确。" : "请从首页历史任务进入详情页。";
    $("taskStatus").textContent = "不可用";
    $("taskStatus").className = "";
    $("detailUsers").textContent = "-";
    $("detailScenarios").textContent = "-";
    $("detailCategories").textContent = "-";
    $("detailCulture").textContent = "-";
    $("deleteTask").disabled = true;
    return;
  }
  $("taskTitle").textContent = task.title;
  $("taskSubtitle").textContent = `更新于 ${formatDate(task.updated_at)}`;
  $("taskStatus").textContent = statusLabel(task.status);
  $("taskStatus").className = statusClass(task.status);
  $("detailUsers").textContent = joinItems(task.target_users);
  $("detailScenarios").textContent = joinItems(task.usage_scenarios);
  $("detailCategories").textContent = joinItems(task.product_categories);
  $("detailCulture").textContent = joinItems(task.cultural_preferences);
  $("deleteTask").disabled = false;
}

function fillDirectionSelect(select, directions, selectedValue) {
  select.innerHTML = "";
  directions.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.direction_id;
    option.textContent = `方向 ${item.direction_number}｜${item.name}`;
    select.appendChild(option);
  });
  if (selectedValue && directions.some((item) => item.direction_id === selectedValue || item.name === selectedValue)) {
    const matched = directions.find((item) => item.direction_id === selectedValue || item.name === selectedValue);
    select.value = matched.direction_id;
  }
}

function renderDirections() {
  const list = $("directionsList");
  $("directionCount").textContent = `${state.directions.length} 个方向`;

  if (!state.directions.length) {
    list.className = "direction-list empty-state";
    list.textContent = state.currentTask?.status === "failed" ? "该任务生成失败，请检查配置后重新创建任务。" : "暂无方向结果。";
    $("submitFeedback").disabled = true;
    $("createHandoff").disabled = true;
    fillDirectionSelect($("selectedDirection"), [], "");
    return;
  }

  list.className = "direction-list direction-result-list";
  list.innerHTML = state.directions
    .map(
      (item) => `
        <article class="direction-card direction-result-card">
          <div class="direction-top">
            <div class="direction-copy">
              <span class="direction-number">方向 ${escapeHtml(item.direction_number)}</span>
              <h3>${escapeHtml(item.name)}</h3>
              <p class="direction-summary">${escapeHtml(item.summary)}</p>
            </div>
            <div class="score">${escapeHtml(item.score)}</div>
          </div>
          <p class="direction-story"><strong>故事内核</strong>${escapeHtml(item.story_core)}</p>
          <div class="chips">
            <span class="chip">${escapeHtml(item.type)}</span>
            <span class="chip">${escapeHtml(item.recommendation)}</span>
            <span class="chip ${riskClass(item.risk_level)}">${escapeHtml(item.risk_level)}</span>
          </div>
          <button class="secondary-button compact choose-direction" type="button" data-direction-id="${escapeHtml(item.direction_id)}">生成报告</button>
        </article>
      `
    )
    .join("");

  const currentSelected = $("selectedDirection").value || state.directions[0].direction_id;
  fillDirectionSelect($("selectedDirection"), state.directions, currentSelected);
  $("submitFeedback").disabled = false;
  $("createHandoff").disabled = !state.report;

  list.querySelectorAll(".choose-direction").forEach((button) => {
    button.addEventListener("click", () => generateReport(button.dataset.directionId, button));
  });
}

function renderReport() {
  const content = $("reportContent");
  const hasReport = Boolean(state.report?.report_markdown);
  if (!hasReport) {
    content.className = "report empty-state";
    content.textContent = "请选择一个方向生成报告。";
    $("downloadReport").disabled = true;
    $("submitReportFeedback").disabled = true;
    $("createHandoff").disabled = true;
    return;
  }
  content.className = "report markdown-body";
  content.innerHTML = renderMarkdown(state.report.report_markdown);
  $("downloadReport").disabled = false;
  $("submitReportFeedback").disabled = false;
  $("createHandoff").disabled = false;
}

function renderPackageValue(value) {
  if (Array.isArray(value)) {
    if (!value.length) return "<span class=\"muted-text\">无</span>";
    return `<ul>${value.map((item) => `<li>${renderPackageValue(item)}</li>`).join("")}</ul>`;
  }
  if (value && typeof value === "object") {
    return `<pre class="package-json">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
  }
  return `<span>${escapeHtml(value || "-")}</span>`;
}

function renderHandoff() {
  const list = $("handoffList");
  const packages = state.handoff?.packages || [];
  if (!packages.length) {
    list.className = "package-list empty-state";
    list.textContent = state.report ? "暂无任务包。" : "请先生成报告，再生成任务包。";
    $("downloadHandoff").disabled = true;
    return;
  }
  list.className = "package-list json-package-list";
  list.innerHTML = packages
    .map(
      (item, index) => `
        <article class="package-card json-package-card">
          <div class="package-card-head">
            <h3>${escapeHtml(item.agent || "下游 Agent")}</h3>
            <button class="secondary-button compact download-agent-package" type="button" data-agent-index="${index}">下载该任务包</button>
          </div>
          <pre>${escapeHtml(JSON.stringify(item, null, 2))}</pre>
        </article>
      `
    )
    .join("");
  list.querySelectorAll(".download-agent-package").forEach((button) => {
    button.addEventListener("click", () => downloadAgentHandoff(Number(button.dataset.agentIndex)));
  });
  $("downloadHandoff").disabled = false;
}

async function loadTask() {
  if (!state.taskId) {
    setPageError("缺少任务 ID，请从首页历史任务进入详情页。");
    renderTaskHeader();
    return;
  }
  const data = await request("/v1/agents/cultural-ip/tasks?limit=100");
  state.tasks = data.tasks || [];
  state.currentTask = state.tasks.find((task) => task.ip_task_id === state.taskId) || null;
  if (!state.currentTask) {
    setPageError("没有找到这个任务，可能已被删除或 ID 不正确。");
    renderTaskHeader();
    return;
  }
  setPageError("");
  renderTaskHeader();
}

async function loadDirections() {
  if (!state.taskId || !state.currentTask || state.currentTask.status === "failed") {
    renderDirections();
    return;
  }
  try {
    const data = await request(`/v1/agents/cultural-ip/tasks/${state.taskId}/directions`);
    state.directions = data.directions || [];
    if (state.currentTask) state.currentTask.status = data.status;
    renderTaskHeader();
    renderDirections();
  } catch (error) {
    state.directions = [];
    renderDirections();
    toast(error.message);
  }
}

async function loadReport({ quiet = false } = {}) {
  if (!state.taskId || !state.currentTask) return;
  try {
    state.report = await request(`/v1/agents/cultural-ip/tasks/${state.taskId}/report`);
    renderReport();
  } catch (error) {
    state.report = null;
    renderReport();
    if (!quiet) toast(error.message);
  }
}

async function submitFeedback() {
  if (!state.taskId) return;
  const feedback = $("feedbackText").value.trim();
  if (!feedback) {
    setFeedbackStatus("direction", "请先输入方向反馈。", "error");
    return;
  }
  const button = $("submitFeedback");
  setFeedbackStatus("direction", "正在更新方向...", "loading");
  setButtonLoading(button, true, "更新中");
  try {
    const response = await request(`/v1/agents/cultural-ip/tasks/${state.taskId}/feedback`, {
      method: "POST",
      body: JSON.stringify({
        selected_direction: $("selectedDirection").value,
        feedback,
      }),
    });
    await loadTask();
    await loadDirections();
    if (response.selected_direction) {
      fillDirectionSelect($("selectedDirection"), state.directions, response.selected_direction);
    }
    $("feedbackText").value = "";
    setFeedbackStatus("direction", "方向更新成功。", "success");
  } catch (error) {
    setFeedbackStatus("direction", error.message, "error");
  } finally {
    setButtonLoading(button, false);
    button.disabled = !state.directions.length;
  }
}

async function generateReport(directionId, button = null) {
  if (!state.taskId || !directionId) return;
  state.reportDirectionId = directionId;
  const targetButton = button || document.querySelector(`.choose-direction[data-direction-id="${CSS.escape(directionId)}"]`);
  setButtonLoading(targetButton, true, "生成中");
  try {
    state.report = await request(`/v1/agents/cultural-ip/tasks/${state.taskId}/report`, {
      method: "POST",
      body: JSON.stringify({ selected_direction: directionId }),
    });
    await loadTask();
    state.handoff = null;
    renderReport();
    renderHandoff();
    activateTab("report");
    scrollToDetailTop();
    toast("报告已生成");
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonLoading(targetButton, false);
    if (targetButton) targetButton.disabled = !state.directions.length;
  }
}

async function submitReportFeedback() {
  if (!state.taskId || !state.report) return;
  const feedback = $("reportFeedbackText").value.trim();
  if (!feedback) {
    setFeedbackStatus("report", "请先输入报告反馈。", "error");
    return;
  }
  const button = $("submitReportFeedback");
  setFeedbackStatus("report", "正在更新报告...", "loading");
  setButtonLoading(button, true, "更新中");
  try {
    state.report = await request(`/v1/agents/cultural-ip/tasks/${state.taskId}/report/feedback`, {
      method: "POST",
      body: JSON.stringify({ feedback }),
    });
    await loadTask();
    state.handoff = null;
    renderReport();
    renderHandoff();
    $("reportFeedbackText").value = "";
    setFeedbackStatus("report", "报告更新成功。", "success");
  } catch (error) {
    setFeedbackStatus("report", error.message, "error");
  } finally {
    setButtonLoading(button, false);
    button.disabled = !state.report;
  }
}

async function createHandoff() {
  if (!state.taskId) return;
  const button = $("createHandoff");
  setButtonLoading(button, true, "生成中");
  try {
    state.handoff = await request(`/v1/agents/cultural-ip/tasks/${state.taskId}/handoff`, {
      method: "POST",
      body: JSON.stringify({
        target_agents: ["designer_agent", "marketer_agent", "quality_controller_agent"],
      }),
    });
    renderHandoff();
    toast("任务包已生成");
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonLoading(button, false);
    button.disabled = !state.report;
  }
}

async function deleteCurrentTask() {
  if (!state.taskId || !state.currentTask) return;
  const title = state.currentTask.title || "当前任务";
  if (!window.confirm(`确定删除任务「${title}」？删除后无法在历史任务中查看。`)) return;
  try {
    await request(`/v1/agents/cultural-ip/tasks/${state.taskId}`, { method: "DELETE" });
    toast("任务已删除");
    window.location.href = "/";
  } catch (error) {
    toast(error.message);
  }
}

function downloadReport() {
  if (!state.report) return;
  const title = state.report.report_json?.theme || state.currentTask?.title || "文化IP报告";
  downloadText(`${safeFilename(title, "report")}.md`, state.report.report_markdown, "text/markdown;charset=utf-8");
}

function downloadAgentHandoff(index) {
  const item = state.handoff?.packages?.[index];
  if (!item) return;
  const agent = item.agent || `agent-${index + 1}`;
  downloadText(
    `${safeFilename(agent, "agent")}-任务包.json`,
    JSON.stringify(item, null, 2),
    "application/json;charset=utf-8"
  );
}
function downloadHandoff() {
  const packages = state.handoff?.packages || [];
  if (!packages.length) return;
  const title = state.currentTask?.title || "文化IP任务包";
  downloadText(
    `${safeFilename(title, "handoff")}-任务包.json`,
    JSON.stringify(state.handoff, null, 2),
    "application/json;charset=utf-8"
  );
}

function setupTabs() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.tab));
  });
}

$("submitFeedback").addEventListener("click", submitFeedback);
$("submitReportFeedback").addEventListener("click", submitReportFeedback);
$("deleteTask").addEventListener("click", deleteCurrentTask);
$("createHandoff").addEventListener("click", createHandoff);
$("downloadReport").addEventListener("click", downloadReport);
$("downloadHandoff").addEventListener("click", downloadHandoff);
$("agentPanelToggle").addEventListener("click", () => {
  const collapsed = $("agentPanel").classList.contains("collapsed");
  if (collapsed) openAgentPanel();
  else closeAgentPanel();
});
$("collapseAgentPanel").addEventListener("click", closeAgentPanel);

setupTabs();
loadTask()
  .then(() => Promise.all([loadDirections(), loadReport({ quiet: true })]))
  .then(() => {
    renderHandoff();
    syncFeedbackLauncher();
  })
  .catch((error) => {
    setPageError(error.message);
    toast(error.message);
  });












