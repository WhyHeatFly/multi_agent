const state = { chunks: [] };
const $ = (id) => document.getElementById(id);

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
      "Content-Type": "application/json",      ...(options.headers || {}),
    },
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    throw new Error(typeof payload === "string" ? payload : payload.detail || "请求失败");
  }
  return payload;
}

function renderChunks(total = state.chunks.length) {
  $("knowledgeCount").textContent = `${total} 条，当前显示 ${state.chunks.length} 条`;
  $("knowledgeStatus").textContent = `${state.chunks.length} 条`;
  const list = $("knowledgeList");
  if (!state.chunks.length) {
    list.className = "knowledge-list empty-state";
    list.textContent = "暂无知识块。";
    return;
  }
  list.className = "knowledge-list";
  list.innerHTML = state.chunks.map((chunk) => `
    <article class="knowledge-card">
      <div class="knowledge-card-head">
        <div>
          <h2>${escapeHtml(chunk.title)}</h2>
          <p>${escapeHtml(chunk.source_id)} · ${escapeHtml(chunk.source_type)} · 可信度 ${escapeHtml(chunk.credibility_level)}</p>
        </div>
        <button class="secondary-button compact danger" type="button" data-source-id="${escapeHtml(chunk.source_id)}">删除来源</button>
      </div>
      <p>${escapeHtml(chunk.summary)}</p>
      <div class="chips">${(chunk.tags || []).map((tag) => `<span class="chip">${escapeHtml(tag)}</span>`).join("")}</div>
    </article>
  `).join("");
  list.querySelectorAll("[data-source-id]").forEach((button) => {
    button.addEventListener("click", () => deleteSource(button.dataset.sourceId));
  });
}

async function loadChunks() {
  const query = $("knowledgeQuery").value.trim();
  const path = query
    ? `/v1/admin/knowledge/chunks?limit=100&query=${encodeURIComponent(query)}`
    : "/v1/admin/knowledge/chunks?limit=100";
  const data = await request(path);
  state.chunks = data.chunks || [];
  renderChunks(data.total || 0);
}

async function ingestKnowledge() {
  const data = await request("/v1/admin/knowledge/ingest?reset=true", { method: "POST" });
  toast(`已入库 ${data.chunks_inserted} 个知识块`);
  await loadChunks();
}

async function deleteSource(sourceId) {
  if (!window.confirm(`删除来源 ${sourceId} 的所有知识块？`)) return;
  const data = await request(`/v1/admin/knowledge/sources/${encodeURIComponent(sourceId)}`, { method: "DELETE" });
  toast(`已删除 ${data.deleted_chunks} 个知识块`);
  await loadChunks();
}

$("searchKnowledge").addEventListener("click", () => loadChunks().catch((error) => toast(error.message)));
$("refreshKnowledge").addEventListener("click", () => loadChunks().catch((error) => toast(error.message)));
$("ingestKnowledge").addEventListener("click", () => ingestKnowledge().catch((error) => toast(error.message)));
$("knowledgeQuery").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadChunks().catch((error) => toast(error.message));
});

loadChunks().catch((error) => toast(error.message));

