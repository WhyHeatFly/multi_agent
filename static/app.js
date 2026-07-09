const state = {
  tasks: [],
  loadingTimer: null,
  loadingStepIndex: 0,
};

const loadingSteps = [
  "解析需求报告",
  "扩展文化关键词",
  "检索知识库来源",
  "生成 IP 方向",
  "撰写 Markdown 报告",
];

const $ = (id) => document.getElementById(id);

function splitInput(value) {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

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
    completed: "已完成",
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
  if (["completed", "revised", "report_revised", "direction_revised", "directions_ready"].includes(status)) return "status-completed";
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

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  window.setTimeout(() => el.classList.remove("show"), 2800);
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

function buildPayload() {
  return {
    demand_report_id: `dr_${Date.now()}`,
    structured_report: {
      target_users: splitInput($("targetUsers").value),
      usage_scenarios: splitInput($("usageScenarios").value),
      product_categories: splitInput($("productCategories").value),
      cultural_preferences: splitInput($("culturalPreferences").value),
      emotional_keywords: splitInput($("emotionalKeywords").value),
      risk_hints: splitInput($("riskHints").value),
    },
    options: {
      num_directions: Number($("numDirections").value || 5),
      risk_check: true,
      need_visual_translation: true,
    },
  };
}

function setLoading(isLoading) {
  const overlay = $("loadingOverlay");
  const form = $("demandForm");
  form.querySelectorAll("button, input, textarea, select").forEach((el) => {
    el.disabled = isLoading;
  });

  if (isLoading) {
    state.loadingStepIndex = 0;
    $("loadingStep").textContent = loadingSteps[0];
    overlay.classList.add("active");
    overlay.setAttribute("aria-hidden", "false");
    state.loadingTimer = window.setInterval(() => {
      state.loadingStepIndex = (state.loadingStepIndex + 1) % loadingSteps.length;
      $("loadingStep").textContent = loadingSteps[state.loadingStepIndex];
    }, 1800);
  } else {
    overlay.classList.remove("active");
    overlay.setAttribute("aria-hidden", "true");
    if (state.loadingTimer) window.clearInterval(state.loadingTimer);
    state.loadingTimer = null;
  }
}

function setError(message = "") {
  const el = $("formError");
  el.textContent = message;
  el.classList.toggle("active", Boolean(message));
}

function renderTasks() {
  const list = $("taskList");
  const query = $("taskSearch").value.trim().toLowerCase();
  const tasks = state.tasks.filter((task) => {
    const haystack = [
      task.ip_task_id,
      task.title,
      task.status,
      task.selected_direction,
      ...(task.target_users || []),
      ...(task.usage_scenarios || []),
      ...(task.product_categories || []),
      ...(task.cultural_preferences || []),
    ].join(" ").toLowerCase();
    return !query || haystack.includes(query);
  });

  $("taskCount").textContent = `${state.tasks.length} 个任务`;
  if (!tasks.length) {
    list.className = "task-list empty-state";
    list.textContent = state.tasks.length ? "没有匹配的任务。" : "暂无历史任务。";
    return;
  }

  list.className = "task-list history-table-body";
  list.innerHTML = tasks
    .map(
      (task) => `
        <button class="history-row task-item" type="button" data-task-id="${escapeHtml(task.ip_task_id)}">
          <span class="history-title">${escapeHtml(task.title)}</span>
          <span><em class="task-state ${statusClass(task.status)}">${escapeHtml(statusLabel(task.status))}</em></span>
          <span class="task-meta">${escapeHtml(joinItems(task.usage_scenarios, "未填写场景"))}</span>
          <span class="task-meta">${escapeHtml(joinItems(task.cultural_preferences, "未填写文化偏好"))}</span>
          <span class="task-time">${escapeHtml(formatDate(task.updated_at))}</span>
        </button>
      `
    )
    .join("");

  list.querySelectorAll(".task-item").forEach((button) => {
    button.addEventListener("click", () => {
      window.location.href = `/task?id=${encodeURIComponent(button.dataset.taskId)}`;
    });
  });
}

async function loadTasks() {
  const data = await request("/v1/agents/cultural-ip/tasks?limit=100");
  state.tasks = data.tasks || [];
  renderTasks();
}

async function createTask(event) {
  event.preventDefault();
  setError("");
  setLoading(true);
  try {
    const created = await request("/v1/agents/cultural-ip/tasks", {
      method: "POST",
      body: JSON.stringify(buildPayload()),
    });
    toast("任务生成完成");
    window.location.href = `/task?id=${encodeURIComponent(created.ip_task_id)}`;
  } catch (error) {
    setError(error.message);
    toast(error.message);
    await loadTasks().catch(() => undefined);
  } finally {
    setLoading(false);
  }
}

function fillExample() {
  $("targetUsers").value = "新婚夫妇, 婚礼宾客";
  $("usageScenarios").value = "婚礼回礼, 西湖春游纪念";
  $("productCategories").value = "丝巾, 香囊, 礼盒";
  $("culturalPreferences").value = "西湖, 婚嫁, 丝绸, 江南";
  $("emotionalKeywords").value = "浪漫, 吉祥, 温柔, 春日";
  $("riskHints").value = "避免悲情爱情典故, 避免孤独意象";
  $("numDirections").value = "5";
}

$("demandForm").addEventListener("submit", createTask);
$("fillExample").addEventListener("click", fillExample);
$("refreshTasks").addEventListener("click", () => loadTasks().catch((error) => toast(error.message)));
$("taskSearch").addEventListener("input", renderTasks);

loadTasks().catch((error) => toast(error.message));







