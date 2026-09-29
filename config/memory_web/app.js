/* ============================================================
   Memory Panel — app.js
   Open-LLM-VTuber long-term memory management UI
   ============================================================ */

"use strict";

// ---------------- API ----------------

const API = {
  sessions: "/memory/api/sessions",
  memories: "/memory/api/memories",
  keywords: "/memory/api/keywords",
  state: "/memory/api/state",
  debug: "/memory/api/debug",
  testRetrieve: "/memory/api/test-retrieve",
  config: "/memory/api/config",
  llmInfo: "/memory/api/llm-info",
  llmTest: "/memory/api/llm-test",
  wipe: "/memory/api/wipe",
};

// ---------------- constants ----------------

const MEMORY_TYPES = [
  ["identity", "身份"], ["preference", "偏好"], ["habit", "习惯"],
  ["experience", "经历"], ["goal", "目标"], ["fact", "事实"],
  ["event", "事件"], ["relationship", "关系"],
];

const KEYWORD_CATEGORIES = [
  "interest", "skill", "technology", "person", "place", "organization",
  "food", "hobby", "goal", "project", "event", "preference", "topic",
];

const TYPE_LABEL_ZH = Object.fromEntries(MEMORY_TYPES);
const CAT_LABEL_ZH = {
  interest: "兴趣", skill: "技能", technology: "技术", person: "人物",
  place: "地点", organization: "组织", food: "食物", hobby: "爱好",
  goal: "目标", project: "项目", event: "事件", preference: "偏好", topic: "话题",
};

const EMOTION_EMOJI = {
  happy: "😊", excited: "🤩", sad: "😢", angry: "😠",
  anxious: "😟", neutral: "🙂", tired: "😴", calm: "😌",
};

const NAV = [
  { id: "memories", label: "长期记忆", icon: "brain" },
  { id: "keywords", label: "关键词", icon: "tag" },
  { id: "state", label: "用户状态", icon: "activity" },
  { id: "debug", label: "调试工具", icon: "terminal" },
  { id: "config", label: "系统设置", icon: "settings" },
];

const ICONS = {
  brain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a7 7 0 0 0-7 7c0 2.38 1.19 4.47 3 5.74V17a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-2.26c1.81-1.27 3-3.36 3-5.74a7 7 0 0 0-7-7z"/><line x1="9" y1="21" x2="15" y2="21"/></svg>',
  tag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.83z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>',
  activity: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
  terminal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
  settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/></svg>',
};

// ---------------- app state ----------------

const S = {
  confUid: "",
  confUids: [],
  view: "memories",
  // memory view
  memories: [],
  filter: { status: "active", type: "", q: "" },
  // keywords view
  keywords: [],
  // edit modal state
  editingId: null,
  // confirm modal state
  confirmCb: null,
};

// ---------------- DOM helpers ----------------

const $ = (sel) => document.querySelector(sel);
const el = (id) => document.getElementById(id);

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtAgo(ts) {
  if (!ts) return "从未";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}

function fmtNum(v) {
  return (Math.round(v * 100) / 100).toFixed(2);
}

async function api(path, opts = {}) {
  const init = { headers: {} };
  if (opts.method && opts.method !== "GET") init.method = opts.method;
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON */ }
  if (!res.ok) {
    const msg = (data && data.detail) ? data.detail : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

// ---------------- toast ----------------

function toast(msg, type = "info", title) {
  const t = document.createElement("div");
  t.className = `toast ${type}`;
  t.innerHTML = `<div><div class="t-title">${esc(title || (type === "error" ? "出错了" : type === "success" ? "成功" : type === "warn" ? "注意" : "提示"))}</div><div class="t-msg">${esc(msg)}</div></div>`;
  el("toasts").appendChild(t);
  setTimeout(() => {
    t.style.transition = "opacity 0.25s";
    t.style.opacity = "0";
    setTimeout(() => t.remove(), 260);
  }, 3400);
}

// ---------------- modal helpers ----------------

function openModal(id) { el(id).classList.remove("hidden"); }
function closeModal(id) { el(id).classList.add("hidden"); }

function askConfirm(title, text, cb) {
  el("confirm-title").textContent = title;
  el("confirm-text").textContent = text;
  S.confirmCb = cb;
  openModal("confirm-modal");
}

document.querySelectorAll("[data-close]").forEach((btn) => {
  btn.addEventListener("click", () => closeModal(btn.dataset.close));
});

document.querySelectorAll(".modal-overlay").forEach((ov) => {
  ov.addEventListener("mousedown", (e) => {
    if (e.target === ov) ov.classList.add("hidden");
  });
});

// ---------------- nav & routing ----------------

function renderNav() {
  const nav = el("nav");
  nav.innerHTML = "";
  NAV.forEach((item) => {
    const n = document.createElement("div");
    n.className = `nav-item${S.view === item.id ? " active" : ""}`;
    n.innerHTML = `<span class="nav-icon">${ICONS[item.icon]}</span><span>${item.label}</span>`;
    n.addEventListener("click", () => {
      if (S.view === item.id) return;
      S.view = item.id;
      renderNav();
      renderView();
    });
    nav.appendChild(n);
  });
}

function setPage(title, desc) {
  el("page-title").textContent = title;
  el("page-desc").textContent = desc || "";
}

// ---------------- boot: sessions ----------------

async function refreshSessions(keepSelection) {
  let data;
  try {
    data = await api(API.sessions);
  } catch (e) {
    el("content").innerHTML = `<div class="empty-state"><div class="es-icon">⚠️</div><p>无法连接记忆服务：${esc(e.message)}</p><p style="font-size:12px;margin-top:4px;">请确认后端已启动且 /memory 路由已挂载。</p></div>`;
    return false;
  }
  S.confUids = data.conf_uids || [];
  if (!data.enabled) {
    toast("记忆系统当前处于停用状态（enabled=false），可在系统设置中开启", "warn");
  }
  const sel = el("conf-select");
  const prev = keepSelection ? S.confUid : (sel.value || S.confUid);
  sel.innerHTML = "";
  if (S.confUids.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "（暂无角色 — 与 AI 对话后自动出现）";
    sel.appendChild(opt);
    S.confUid = "";
  } else {
    S.confUids.forEach((u) => {
      const opt = document.createElement("option");
      opt.value = u;
      const live = (data.live_managers || []).includes(u) ? " ●" : "";
      opt.textContent = u + live;
      sel.appendChild(opt);
    });
    S.confUid = S.confUids.includes(prev) ? prev : S.confUids[0];
    sel.value = S.confUid;
  }
  return true;
}

// ---------------- view router ----------------

function renderView() {
  if (!S.confUid) {
    setPage("未选择角色", "还没有任何角色的记忆数据。与 AI 对话几句后，回到这里点击「重扫角色」。");
    el("content").innerHTML = `<div class="empty-state"><div class="es-icon">🧠</div><p>暂无记忆数据</p><p style="font-size:12px;margin-top:6px;color:var(--text-secondary);">和 AI 聊几句，长期记忆会自动沉淀到这里。</p></div>`;
    return;
  }
  switch (S.view) {
    case "memories": viewMemories(); break;
    case "keywords": viewKeywords(); break;
    case "state": viewState(); break;
    case "debug": viewDebug(); break;
    case "config": viewConfig(); break;
  }
}

// ---------------- memories view ----------------

async function viewMemories() {
  setPage("长期记忆", `角色 ${S.confUid} · 记录 AI 对用户的长期认知`);
  el("content").innerHTML = `<div class="loading-state"><div class="spinner"></div><p>加载记忆中…</p></div>`;

  let data;
  try {
    const qs = new URLSearchParams({ conf_uid: S.confUid, status: S.filter.status });
    if (S.filter.type) qs.set("memory_type", S.filter.type);
    if (S.filter.q) qs.set("q", S.filter.q);
    data = await api(`${API.memories}?${qs}`);
  } catch (e) {
    el("content").innerHTML = `<div class="empty-state"><div class="es-icon">⚠️</div><p>加载失败：${esc(e.message)}</p></div>`;
    return;
  }
  S.memories = data.memories || [];

  const activeN = S.filter.status === "all"
    ? S.memories.filter((m) => m.status === "active").length : null;

  const stats = [
    { label: "当前筛选结果", value: S.memories.length, cls: "" },
  ];
  if (activeN !== null) stats.push({ label: "其中生效中", value: activeN, cls: "green" });
  stats.push({ label: "记忆类型分布", value: typeDistributionText(S.memories), cls: "purple", isText: true });
  const typeOpts = ['<option value="">全部类型</option>'].concat(
    MEMORY_TYPES.map(([v, l]) => `<option value="${v}"${S.filter.type === v ? " selected" : ""}>${l} ${v}</option>`)
  ).join("");
  const statusOpts = [
    `<option value="active"${S.filter.status === "active" ? " selected" : ""}>生效中 active</option>`,
    `<option value="deprecated"${S.filter.status === "deprecated" ? " selected" : ""}>已废弃 deprecated</option>`,
    `<option value="all"${S.filter.status === "all" ? " selected" : ""}>全部 all</option>`,
  ].join("");

  el("content").innerHTML = `
    <div class="stats-strip">
      ${stats.map((s) => `<div class="stat-card ${s.cls}"><div class="stat-value"${s.isText ? ' style="font-size:14px;line-height:1.5;padding-top:4px;"' : ""}>${s.isText ? esc(s.value) : s.value}</div><div class="stat-label">${s.label}</div></div>`).join("")}
    </div>
    <div class="filter-bar">
      <input type="text" class="input search" id="flt-q" placeholder="搜索内容 / 关键词…" value="${esc(S.filter.q)}" />
      <select class="input" id="flt-type">${typeOpts}</select>
      <select class="input" id="flt-status">${statusOpts}</select>
      <button class="btn btn-ghost" id="flt-clear">重置</button>
    </div>
    <div class="grid" id="mem-grid"></div>
  `;

  el("flt-q").addEventListener("input", debounce(() => { S.filter.q = el("flt-q").value.trim(); viewMemories(); }, 350));
  el("flt-type").addEventListener("change", () => { S.filter.type = el("flt-type").value; viewMemories(); });
  el("flt-status").addEventListener("change", () => { S.filter.status = el("flt-status").value; viewMemories(); });
  el("flt-clear").addEventListener("click", () => { S.filter = { status: "active", type: "", q: "" }; viewMemories(); });

  renderMemGrid();
}

function typeDistributionText(mems) {
  if (!mems.length) return "—";
  const counts = {};
  mems.forEach((m) => { counts[m.memory_type] = (counts[m.memory_type] || 0) + 1; });
  const parts = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([t, n]) => `${TYPE_LABEL_ZH[t] || t} ${n}`);
  return parts.join(" · ");
}

function renderMemGrid() {
  const grid = el("mem-grid");
  if (!S.memories.length) {
    grid.outerHTML = `<div class="empty-state"><div class="es-icon">🫧</div><p>没有匹配的记忆</p><p style="font-size:12px;margin-top:6px;color:var(--text-secondary);">和 AI 多聊几句，或切换筛选条件。</p></div>`;
    return;
  }
  grid.innerHTML = S.memories.map(memCardHtml).join("");
  bindMemCardEvents();
}

function memCardHtml(m) {
  const kws = (m.keywords || []).map((k) => `<span class="kw-chip">${esc(k)}</span>`).join("");
  const hist = (m.history && m.history.length)
    ? `<div class="mem-history"><div class="h-title">冲突历史</div>${m.history.map((h) => {
        let line = "";
        try {
          const at = h.at ? fmtTime(h.at) : "";
          const reason = h.reason ? ` — ${h.reason}` : "";
          const content = h.replaced_by_content || h.content || "";
          line = `${h.event === "deprecated_by" ? "被替换" : h.event === "replaces" ? "替换了" : esc(h.event || "记录")}：${esc(content)}${esc(reason)} <span style="color:var(--text-muted);">${at}</span>`;
        } catch (e) { line = esc(JSON.stringify(h)); }
        return `<div class="h-entry">${line}</div>`;
      }).join("")}</div>`
    : "";
  return `
  <div class="card mem-card${m.status === "deprecated" ? " deprecated" : ""}" data-mid="${esc(m.memory_id)}">
    <div class="card-body">
      <div class="mem-head">
        <span class="type-badge t-${esc(m.memory_type)}">${TYPE_LABEL_ZH[m.memory_type] || esc(m.memory_type)}</span>
        <span class="status-badge ${m.status === "active" ? "active" : "deprecated"}">${m.status === "active" ? "生效中" : "已废弃"}</span>
      </div>
      <div class="mem-content">${esc(m.content)}</div>
      ${kws ? `<div class="mem-keywords">${kws}</div>` : ""}
      <div class="mem-meters">
        <div class="meter importance">
          <div class="meter-label"><span>重要度</span><b>${fmtNum(m.importance)}</b></div>
          <div class="meter-track"><div class="meter-fill" style="width:${Math.round(m.importance * 100)}%"></div></div>
        </div>
        <div class="meter confidence">
          <div class="meter-label"><span>置信度</span><b>${fmtNum(m.confidence)}</b></div>
          <div class="meter-track"><div class="meter-fill" style="width:${Math.round(m.confidence * 100)}%"></div></div>
        </div>
      </div>
      <div class="mem-meta">
        <span title="使用次数">${m.use_count || 0} 次使用</span><span class="dot-sep">·</span>
        <span title="创建时间">创建 ${fmtTime(m.created_at)}</span><span class="dot-sep">·</span>
        <span title="最近使用">${fmtAgo(m.last_used_at)}用过</span>
        ${m.source_history_uid === "manual" ? '<span class="dot-sep">·</span><span title="来源">手动</span>' : ""}
        <span class="mem-actions">
          <button class="btn btn-ghost btn-sm" data-act="edit" title="编辑">编辑</button>
          <button class="btn btn-ghost btn-sm" data-act="toggle" title="${m.status === "active" ? "废弃（保留历史）" : "恢复为生效"}">${m.status === "active" ? "废弃" : "恢复"}</button>
          <button class="btn btn-ghost btn-sm btn-danger" data-act="del" title="彻底删除">删除</button>
        </span>
      </div>
      ${hist}
    </div>
  </div>`;
}

function bindMemCardEvents() {
  document.querySelectorAll(".mem-card").forEach((card) => {
    const mid = card.dataset.mid;
    const m = S.memories.find((x) => x.memory_id === mid);
    if (!m) return;
    card.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const act = btn.dataset.act;
        if (act === "edit") openMemModal(m);
        else if (act === "del") {
          askConfirm("删除记忆", `确定彻底删除这条记忆吗？\n\n「${m.content}」\n\n此操作不可恢复（废弃可保留历史，删除则彻底移除）。`, async () => {
            try {
              await api(`${API.memories}/${mid}?conf_uid=${encodeURIComponent(S.confUid)}`, { method: "DELETE" });
              toast("记忆已删除", "success");
              viewMemories();
            } catch (e) { toast(e.message, "error"); }
          });
        } else if (act === "toggle") {
          const newStatus = m.status === "active" ? "deprecated" : "active";
          try {
            await api(`${API.memories}/${mid}`, { method: "PUT", body: { conf_uid: S.confUid, status: newStatus } });
            toast(newStatus === "deprecated" ? "已废弃（历史保留，可在筛选中查看）" : "已恢复为生效", "success");
            viewMemories();
          } catch (e) { toast(e.message, "error"); }
        }
      });
    });
  });
}

// ---------------- memory add/edit modal ----------------

function fillSelectOptions(selectEl, pairs, selected) {
  selectEl.innerHTML = pairs.map(([v, l]) => `<option value="${v}"${v === selected ? " selected" : ""}>${l}（${v}）</option>`).join("");
}

function openMemModal(m) {
  S.editingId = m ? m.memory_id : null;
  el("mem-modal-title").textContent = m ? "编辑记忆" : "添加记忆";
  el("f-content").value = m ? m.content : "";
  fillSelectOptions(el("f-type"), MEMORY_TYPES, m ? m.memory_type : "preference");
  el("f-status").value = m ? m.status : "active";
  el("f-importance").value = m ? m.importance : 0.6;
  el("f-confidence").value = m ? m.confidence : 0.9;
  el("v-importance").textContent = fmtNum(parseFloat(el("f-importance").value));
  el("v-confidence").textContent = fmtNum(parseFloat(el("f-confidence").value));
  el("f-keywords").value = m ? (m.keywords || []).join(", ") : "";
  openModal("mem-modal");
  setTimeout(() => el("f-content").focus(), 50);
}

async function saveMemModal() {
  const content = el("f-content").value.trim();
  if (content.length < 2) { toast("记忆内容太短", "warn"); return; }
  const body = {
    conf_uid: S.confUid,
    memory_type: el("f-type").value,
    content,
    keywords: el("f-keywords").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    importance: parseFloat(el("f-importance").value),
    confidence: parseFloat(el("f-confidence").value),
    status: el("f-status").value,
  };
  try {
    if (S.editingId) {
      await api(`${API.memories}/${S.editingId}`, { method: "PUT", body });
      toast("记忆已更新", "success");
    } else {
      await api(API.memories, { method: "POST", body });
      toast("记忆已添加", "success");
    }
    closeModal("mem-modal");
    viewMemories();
  } catch (e) { toast(e.message, "error"); }
}

// ---------------- keywords view ----------------

async function viewKeywords() {
  setPage("关键词", `角色 ${S.confUid} · 提升检索命中的实体词表`);
  el("content").innerHTML = `<div class="loading-state"><div class="spinner"></div><p>加载关键词中…</p></div>`;
  let data;
  try {
    data = await api(`${API.keywords}?conf_uid=${encodeURIComponent(S.confUid)}`);
  } catch (e) {
    el("content").innerHTML = `<div class="empty-state"><div class="es-icon">⚠️</div><p>加载失败：${esc(e.message)}</p></div>`;
    return;
  }
  S.keywords = data.keywords || [];

  const catCounts = {};
  S.keywords.forEach((k) => { catCounts[k.category] = (catCounts[k.category] || 0) + 1; });

  el("content").innerHTML = `
    <div class="stats-strip">
      <div class="stat-card accent"><div class="stat-value">${S.keywords.length}</div><div class="stat-label">关键词总数</div></div>
      <div class="stat-card purple"><div class="stat-value">${Object.keys(catCounts).length}</div><div class="stat-label">覆盖分类数</div></div>
      <div class="stat-card green"><div class="stat-value">${S.keywords.reduce((a, k) => a + (k.hit_count || 0), 0)}</div><div class="stat-label">累计命中次数</div></div>
    </div>
    <div class="filter-bar">
      <button class="btn btn-primary" id="btn-add-kw">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        添加关键词
      </button>
      <span style="font-size:12px;color:var(--text-muted);">鼠标悬停关键词，点 × 可删除。</span>
    </div>
    <div class="card"><div class="card-body"><div class="kw-grid" id="kw-grid"></div></div></div>
  `;
  el("btn-add-kw").addEventListener("click", () => {
    el("f-kw").value = "";
    fillSelectOptions(el("f-kw-cat"), KEYWORD_CATEGORIES.map((c) => [c, CAT_LABEL_ZH[c] || c]), "topic");
    openModal("kw-modal");
    setTimeout(() => el("f-kw").focus(), 50);
  });

  const grid = el("kw-grid");
  if (!S.keywords.length) {
    grid.innerHTML = `<div class="empty-state" style="padding:20px 0;"><div class="es-icon">🏷️</div><p>暂无关键词 — 对话中会自动提取</p></div>`;
    return;
  }
  grid.innerHTML = S.keywords.map((k) => `
    <span class="kw-item" data-kw="${esc(k.keyword)}" data-cat="${esc(k.category)}">
      <span>${esc(k.keyword)}</span>
      <span class="kw-cat">${CAT_LABEL_ZH[k.category] || esc(k.category)}</span>
      <span class="kw-hits" title="命中次数">${k.hit_count || 1}</span>
      <button class="kw-del" title="删除">×</button>
    </span>`).join("");
  grid.querySelectorAll(".kw-del").forEach((d) => {
    d.addEventListener("click", async () => {
      const item = d.closest(".kw-item");
      const kw = item.dataset.kw, cat = item.dataset.cat;
      try {
        const qs = new URLSearchParams({ conf_uid: S.confUid, keyword: kw, category: cat });
        await api(`${API.keywords}?${qs}`, { method: "DELETE" });
        toast(`已删除关键词「${kw}」`, "success");
        viewKeywords();
      } catch (e) { toast(e.message, "error"); }
    });
  });
}

async function saveKwModal() {
  const kw = el("f-kw").value.trim();
  if (!kw) { toast("关键词不能为空", "warn"); return; }
  try {
    await api(API.keywords, { method: "POST", body: { conf_uid: S.confUid, keyword: kw, category: el("f-kw-cat").value } });
    toast("关键词已添加", "success");
    closeModal("kw-modal");
    viewKeywords();
  } catch (e) { toast(e.message, "error"); }
}

// ---------------- state view ----------------

async function viewState() {
  setPage("用户状态", `角色 ${S.confUid} · 对话中实时维护的情绪 / 精力 / 话题快照`);
  el("content").innerHTML = `<div class="loading-state"><div class="spinner"></div><p>加载状态中…</p></div>`;
  let data;
  try {
    data = await api(`${API.state}?conf_uid=${encodeURIComponent(S.confUid)}`);
  } catch (e) {
    el("content").innerHTML = `<div class="empty-state"><div class="es-icon">⚠️</div><p>加载失败：${esc(e.message)}</p></div>`;
    return;
  }
  const st = data.state;

  if (!st) {
    el("content").innerHTML = `<div class="empty-state"><div class="es-icon">🌱</div><p>尚无用户状态记录</p><p style="font-size:12px;margin-top:6px;color:var(--text-secondary);">对话进行几轮后，系统会开始维护情绪 / 精力 / 压力 / 话题快照。</p></div>`;
    return;
  }

  const emoji = EMOTION_EMOJI[st.emotion] || "🙂";
  el("content").innerHTML = `
    <div class="stats-strip">
      <div class="stat-card"><div class="stat-value state-emoji">${emoji}</div><div class="stat-label">情绪 ${esc(st.emotion)}</div></div>
      <div class="stat-card green"><div class="stat-value">${data.turn_count || 0}</div><div class="stat-label">累计对话轮数</div></div>
      <div class="stat-card purple"><div class="stat-value" style="font-size:15px;">${esc(st.intent || "chat")}</div><div class="stat-label">最近意图</div></div>
      <div class="stat-card accent"><div class="stat-value" style="font-size:15px;">${esc(st.current_topic || "—")}</div><div class="stat-label">当前话题</div></div>
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(340px,1fr));">
      <div class="card">
        <div class="card-header"><h3>状态调节</h3><button class="btn btn-primary btn-sm" id="btn-save-state">保存修改</button></div>
        <div class="card-body">
          <div class="field">
            <label>情绪 emotion</label>
            <input type="text" class="input" id="st-emotion" value="${esc(st.emotion)}" placeholder="happy / sad / neutral ..." />
          </div>
          <div class="field">
            <label>意图 intent</label>
            <input type="text" class="input" id="st-intent" value="${esc(st.intent)}" placeholder="chat / ask / share ..." />
          </div>
          <div class="field">
            <label>当前话题 current_topic</label>
            <input type="text" class="input" id="st-topic" value="${esc(st.current_topic)}" placeholder="例如：周末去哪玩" />
          </div>
          <div class="field">
            <label>精力 energy：<span class="range-value" id="v-energy" style="color:var(--green);">${fmtNum(st.energy)}</span></label>
            <input type="range" class="input" id="st-energy" min="0" max="1" step="0.05" value="${st.energy}" />
          </div>
          <div class="field">
            <label>压力 stress：<span class="range-value" id="v-stress" style="color:var(--red);">${fmtNum(st.stress)}</span></label>
            <input type="range" class="input" id="st-stress" min="0" max="1" step="0.05" value="${st.stress}" />
          </div>
          <p style="font-size:11px;color:var(--text-muted);">最近更新：${fmtTime(st.updated_at)}（${fmtAgo(st.updated_at)}）</p>
        </div>
      </div>
      <div class="card">
        <div class="card-header"><h3>对话滚动摘要</h3></div>
        <div class="card-body">
          ${data.summary
            ? `<div class="summary-box">${esc(data.summary)}</div>`
            : `<div class="empty-state" style="padding:24px 0;"><div class="es-icon">📝</div><p>暂无摘要 — 每约 20 轮对话自动生成一次</p></div>`}
        </div>
      </div>
    </div>
  `;

  el("st-energy").addEventListener("input", () => { el("v-energy").textContent = fmtNum(parseFloat(el("st-energy").value)); });
  el("st-stress").addEventListener("input", () => { el("v-stress").textContent = fmtNum(parseFloat(el("st-stress").value)); });

  el("btn-save-state").addEventListener("click", async () => {
    try {
      await api(API.state, {
        method: "PUT",
        body: {
          conf_uid: S.confUid,
          emotion: el("st-emotion").value.trim(),
          intent: el("st-intent").value.trim(),
          current_topic: el("st-topic").value.trim(),
          energy: parseFloat(el("st-energy").value),
          stress: parseFloat(el("st-stress").value),
        },
      });
      toast("用户状态已更新", "success");
    } catch (e) { toast(e.message, "error"); }
  });
}

// ---------------- debug view ----------------

async function viewDebug() {
  setPage("调试工具", `角色 ${S.confUid} · 检索管线实测 + 系统内部状态`);
  el("content").innerHTML = `<div class="loading-state"><div class="spinner"></div><p>加载调试信息中…</p></div>`;
  let dbg;
  try {
    dbg = await api(`${API.debug}?conf_uid=${encodeURIComponent(S.confUid)}`);
  } catch (e) {
    el("content").innerHTML = `<div class="empty-state"><div class="es-icon">⚠️</div><p>加载失败：${esc(e.message)}</p></div>`;
    return;
  }

  const st = dbg.stats || {};
  const totalMem = (st.active_memories || 0) + (st.deprecated_memories || 0);
  el("content").innerHTML = `
    <div class="stats-strip">
      <div class="stat-card accent"><div class="stat-value">${totalMem}</div><div class="stat-label">记忆总数</div></div>
      <div class="stat-card green"><div class="stat-value">${st.active_memories != null ? st.active_memories : "—"}</div><div class="stat-label">生效中</div></div>
      <div class="stat-card yellow"><div class="stat-value">${st.deprecated_memories != null ? st.deprecated_memories : "—"}</div><div class="stat-label">已废弃</div></div>
      <div class="stat-card purple"><div class="stat-value">${st.keywords != null ? st.keywords : "—"}</div><div class="stat-label">关键词数</div></div>
      <div class="stat-card"><div class="stat-value" style="font-size:15px;">${dbg.llm_attached ? "已接入" : "未接入"}</div><div class="stat-label">抽取 LLM</div></div>
    </div>
    <div class="grid" style="grid-template-columns:1fr;">
      <div class="card">
        <div class="card-header"><h3>检索测试</h3><button class="btn btn-primary btn-sm" id="btn-open-rt">打开测试</button></div>
        <div class="card-body">
          <p style="font-size:12.5px;color:var(--text-secondary);">输入一句模拟用户发言，查看检索管线会注入哪些记忆、得分构成如何。不会写入或修改任何数据。</p>
        </div>
      </div>
      <div class="card">
        <div class="card-header"><h3>系统内部状态（as_debug_dict）</h3></div>
        <div class="card-body">
          <div class="json-box">${esc(JSON.stringify(dbg, null, 2))}</div>
        </div>
      </div>
    </div>
  `;
  el("btn-open-rt").addEventListener("click", () => {
    el("f-rt-text").value = "";
    el("rt-results").innerHTML = "";
    openModal("retrieve-modal");
    setTimeout(() => el("f-rt-text").focus(), 50);
  });
}

async function runRetrieveTest() {
  const text = el("f-rt-text").value.trim();
  if (!text) { toast("请输入模拟用户输入", "warn"); return; }
  const out = el("rt-results");
  out.innerHTML = `<div class="loading-state" style="padding:24px 0;"><div class="spinner"></div><p>检索中…</p></div>`;
  let data;
  try {
    data = await api(API.testRetrieve, { method: "POST", body: { conf_uid: S.confUid, text } });
  } catch (e) {
    out.innerHTML = `<div class="empty-state" style="padding:20px 0;"><p>检索失败：${esc(e.message)}</p></div>`;
    return;
  }
  const results = data.results || [];
  const inj = data.injection_preview || {};
  let html = "";
  if (!results.length) {
    html += `<div class="empty-state" style="padding:20px 0;"><div class="es-icon">🔍</div><p>没有命中任何记忆（不会注入长期记忆）</p></div>`;
  } else {
    html += `<p style="font-size:12px;color:var(--text-muted);margin-bottom:10px;">命中 ${results.length} 条：</p>`;
    html += results.map((r) => {
      const comps = r.components || {};
      const compChips = Object.entries(comps).map(([k, v]) =>
        `<span class="comp">${esc(k)} ${fmtNum(Number(v) || 0)}</span>`).join("");
      return `
      <div class="retrieve-result">
        <div class="rr-head">
          <span class="rr-score">${fmtNum(r.score)}</span>
          <span class="type-badge t-${esc(r.memory.memory_type)}">${TYPE_LABEL_ZH[r.memory.memory_type] || esc(r.memory.memory_type)}</span>
        </div>
        <div class="rr-content">${esc(r.memory.content)}</div>
        <div class="rr-components">${compChips}</div>
      </div>`;
    }).join("");
  }
  const injText = inj.injection_text || "（无注入 — 本次对话不带长期记忆参考）";
  html += `
    <p style="font-size:12px;font-weight:600;color:var(--purple);margin:14px 0 6px;">实际注入到 Prompt 的文本：</p>
    <div class="injection-preview">${esc(injText)}</div>`;
  out.innerHTML = html;
}

// ---------------- config view ----------------

const CONFIG_SCHEMA = [
  { key: "enabled", type: "switch", label: "启用记忆系统", desc: "总开关。关闭后不检索、不注入、不抽取，聊天完全恢复原样。" },
  { key: "max_injected_memories", type: "number", label: "注入记忆上限", desc: "每次对话最多注入的长期记忆条数（默认 5）。" },
  { key: "min_total_score", type: "number", label: "注入分数阈值", desc: "综合得分低于该值的记忆不注入（默认 0.30）。" },
  { key: "max_injection_chars", type: "number", label: "注入字符上限", desc: "注入块的最大字符数（默认 600）。" },
  { key: "extraction_min_chars", type: "number", label: "抽取最小输入长度", desc: "用户输入短于该字符数时跳过抽取（默认 4）。" },
  { key: "min_importance_to_store", type: "number", label: "入库最小重要度", desc: "抽取出的记忆重要度低于该值时不入库（默认 0.35）。" },
  { key: "extraction_timeout", type: "number", label: "抽取超时（秒）", desc: "LLM 抽取调用的超时时间（默认 60）。" },
  { key: "extraction_cooldown", type: "number", label: "抽取冷却（秒）", desc: "两次抽取之间的最小间隔，防止频繁打断（默认 1）。" },
  { key: "summary_interval", type: "number", label: "摘要间隔（轮）", desc: "每 N 轮对话更新一次滚动摘要（默认 20）。" },
  { key: "state_update_interval", type: "number", label: "状态更新间隔（轮）", desc: "每 N 轮对话更新一次用户状态（默认 5）。" },
  { key: "memory_debug", type: "switch", label: "调试日志", desc: "在服务端日志中输出记忆系统的详细运行信息（MEMORY_DEBUG）。" },
];

async function viewConfig() {
  setPage("系统设置", "记忆系统运行参数 · 保存后立即对新对话生效");
  el("content").innerHTML = `<div class="loading-state"><div class="spinner"></div><p>加载配置中…</p></div>`;
  let cfg, llmInfo;
  try {
    cfg = await api(API.config);
  } catch (e) {
    el("content").innerHTML = `<div class="empty-state"><div class="es-icon">⚠️</div><p>加载失败：${esc(e.message)}</p></div>`;
    return;
  }
  try {
    llmInfo = await api(API.llmInfo);
  } catch (e) { llmInfo = null; }
  const llm = cfg.llm || {};
  const llmMode = llm.mode === "custom" ? "custom" : "chat";

  const chatInfoHtml = !llmInfo
    ? `<div class="llm-status warn">无法获取聊天 LLM 信息：${esc("接口不可用")}</div>`
    : llmInfo.source === "conf.yaml"
      ? `<div class="llm-status ok">当前跟随聊天 LLM：<b>${esc(llmInfo.model)}</b> @ ${esc(llmInfo.base_url)}　Key：<code>${esc(llmInfo.api_key_masked)}</code></div>`
      : `<div class="llm-status warn">conf.yaml 中未解析到独立 LLM 配置，抽取将复用运行中的对话 Agent 实例（重启后若 Agent 可用即正常）。</div>`;

  el("content").innerHTML = `
    <div class="grid" style="grid-template-columns:1fr;max-width:820px;">
      <div class="card">
        <div class="card-header"><h3>运行参数</h3><button class="btn btn-primary btn-sm" id="btn-save-cfg">保存修改</button></div>
        <div class="card-body" id="cfg-rows"></div>
      </div>
      <div class="card">
        <div class="card-header"><h3>抽取 LLM</h3><div style="display:flex;gap:8px;"><button class="btn btn-ghost btn-sm" id="btn-llm-test">测试连接</button><button class="btn btn-primary btn-sm" id="btn-save-llm">保存修改</button></div></div>
        <div class="card-body">
          <div class="form-row">
            <div class="row-info">
              <div class="row-label">LLM 来源 <code>llm.mode</code></div>
              <div class="row-desc">记忆抽取（含摘要生成）使用的模型。默认跟随聊天 LLM；也可切换为自定义的 OpenAI 兼容接口，与聊天互不影响。</div>
            </div>
            <div class="row-control">
              <select class="input" id="llm-mode" style="min-width:180px;">
                <option value="chat"${llmMode === "chat" ? " selected" : ""}>跟随聊天 LLM（默认）</option>
                <option value="custom"${llmMode === "custom" ? " selected" : ""}>自定义独立接口</option>
              </select>
            </div>
          </div>
          <div id="llm-chat-box"${llmMode === "chat" ? "" : ' class="hidden"'}>
            ${chatInfoHtml}
          </div>
          <div id="llm-custom-box"${llmMode === "custom" ? "" : ' class="hidden"'}>
            <div class="field">
              <label>API 地址 <code>base_url</code></label>
              <input type="text" class="input" id="llm-base-url" value="${esc(llm.base_url || "")}" placeholder="https://api.example.com/v1" />
            </div>
            <div class="field">
              <label>API Key <code>api_key</code></label>
              <input type="password" class="input" id="llm-api-key" value="${esc(llm.api_key || "")}" placeholder="sk-…（本地模型可留空）" autocomplete="new-password" />
            </div>
            <div class="field">
              <label>模型名称 <code>model</code></label>
              <input type="text" class="input" id="llm-model" value="${esc(llm.model || "")}" placeholder="例如 deepseek-v4-1-flash" />
            </div>
          </div>
          <div id="llm-test-result" class="llm-test-result"></div>
        </div>
      </div>
      <div class="card">
        <div class="card-header"><h3>排序权重（只读参考）</h3></div>
        <div class="card-body">
          <div class="mem-meters" style="grid-template-columns:1fr;gap:10px;">
            ${weightRow("相关性 relevance", cfg.w_relevance)}
            ${weightRow("重要度 importance", cfg.w_importance)}
            ${weightRow("时近性 recency", cfg.w_recency)}
            ${weightRow("置信度 confidence", cfg.w_confidence)}
            ${weightRow("使用频次 usage", cfg.w_usage)}
          </div>
          <p style="font-size:11px;color:var(--text-muted);margin-top:10px;">时近性按 7 天半衰期计算；使用分 = min(1, use_count/5)。权重在配置文件中调整。</p>
        </div>
      </div>
    </div>
  `;

  const rows = el("cfg-rows");
  rows.innerHTML = "";
  CONFIG_SCHEMA.forEach((f) => {
    const row = document.createElement("div");
    row.className = "form-row";
    const val = cfg[f.key];
    let control;
    if (f.type === "switch") {
      control = `
        <label class="switch">
          <input type="checkbox" data-cfg="${f.key}"${val ? " checked" : ""} />
          <span class="track"><span class="thumb"></span></span>
        </label>`;
    } else {
      control = `<input type="number" class="input" data-cfg="${f.key}" value="${val != null ? esc(val) : ""}" step="${f.key.startsWith("min_") || f.key.includes("score") ? "0.05" : "1"}" style="min-width:110px;" />`;
    }
    row.innerHTML = `
      <div class="row-info">
        <div class="row-label">${f.label} <code>${f.key}</code></div>
        <div class="row-desc">${f.desc}</div>
      </div>
      <div class="row-control">${control}</div>`;
    rows.appendChild(row);
  });

  // --- extraction LLM card wiring ---
  const modeSel = el("llm-mode");
  modeSel.addEventListener("change", () => {
    const custom = modeSel.value === "custom";
    el("llm-chat-box").classList.toggle("hidden", custom);
    el("llm-custom-box").classList.toggle("hidden", !custom);
    el("llm-test-result").innerHTML = "";
  });

  function llmFormValues() {
    return {
      mode: modeSel.value,
      base_url: el("llm-base-url").value.trim(),
      api_key: el("llm-api-key").value.trim(),
      model: el("llm-model").value.trim(),
    };
  }

  el("btn-save-llm").addEventListener("click", async () => {
    const v = llmFormValues();
    if (v.mode === "custom" && (!v.base_url || !v.model)) {
      toast("自定义模式需要填写 API 地址和模型名称", "warn");
      return;
    }
    try {
      await api(API.config, { method: "POST", body: { updates: { llm: v } } });
      toast("抽取 LLM 配置已保存，下轮对话抽取即生效", "success");
      viewConfig();
    } catch (e) { toast(e.message, "error"); }
  });

  el("btn-llm-test").addEventListener("click", async () => {
    const btn = el("btn-llm-test");
    const out = el("llm-test-result");
    const v = llmFormValues();
    if (v.mode === "custom" && (!v.base_url || !v.model)) {
      toast("自定义模式需要填写 API 地址和模型名称", "warn");
      return;
    }
    btn.disabled = true;
    btn.textContent = "测试中…";
    out.innerHTML = `<span style="color:var(--text-muted);font-size:12px;">正在调用 LLM，请稍候…</span>`;
    try {
      const r = await api(API.llmTest, { method: "POST", body: v });
      out.innerHTML = `<span style="color:var(--green);font-size:12.5px;">✅ 连接成功 · ${esc(r.model)} · 延迟 ${r.latency_ms}ms · 回复「${esc(r.reply_preview)}」</span>`;
    } catch (e) {
      out.innerHTML = `<span style="color:var(--red);font-size:12.5px;">❌ 连接失败：${esc(e.message)}</span>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "测试连接";
    }
  });

  el("btn-save-cfg").addEventListener("click", async () => {
    const updates = {};
    rows.querySelectorAll("[data-cfg]").forEach((input) => {
      const key = input.dataset.cfg;
      if (input.type === "checkbox") updates[key] = input.checked;
      else {
        const n = parseFloat(input.value);
        if (!isNaN(n)) updates[key] = n;
      }
    });
    try {
      await api(API.config, { method: "POST", body: { updates } });
      toast("配置已保存，对新对话生效", "success");
    } catch (e) { toast(e.message, "error"); }
  });
}

function weightRow(label, v) {
  const pct = Math.round((Number(v) || 0) * 100);
  return `
  <div class="meter">
    <div class="meter-label"><span>${label}</span><b>${pct}%</b></div>
    <div class="meter-track"><div class="meter-fill" style="width:${pct}%;background:linear-gradient(90deg,var(--accent-dim),var(--accent));"></div></div>
  </div>`;
}

// ---------------- wipe ----------------

function openWipeModal() {
  if (!S.confUid) { toast("请先选择一个角色", "warn"); return; }
  el("wipe-target").textContent = S.confUid;
  el("f-wipe-confirm").value = "";
  openModal("wipe-modal");
  setTimeout(() => el("f-wipe-confirm").focus(), 50);
}

async function confirmWipe() {
  const c = el("f-wipe-confirm").value.trim();
  if (c !== "DELETE") { toast("请输入 DELETE 确认", "warn"); return; }
  try {
    const r = await api(API.wipe, { method: "POST", body: { conf_uid: S.confUid, confirm: c } });
    toast(`已清空：删除 ${r.deleted_memories} 条记忆、${r.deleted_keywords} 个关键词`, "success");
    closeModal("wipe-modal");
    renderView();
  } catch (e) { toast(e.message, "error"); }
}

// ---------------- misc ----------------

function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

// ---------------- wire up ----------------

function init() {
  // fill static selects
  fillSelectOptions(el("f-type"), MEMORY_TYPES, "preference");

  el("conf-select").addEventListener("change", () => {
    S.confUid = el("conf-select").value;
    S.filter = { status: "active", type: "", q: "" };
    renderView();
  });

  el("btn-reload").addEventListener("click", () => renderView());
  el("btn-refresh-sessions").addEventListener("click", async () => {
    if (await refreshSessions(true)) { toast("角色列表已刷新", "success"); renderView(); }
  });
  el("btn-add").addEventListener("click", () => {
    if (!S.confUid) { toast("请先选择一个角色", "warn"); return; }
    openMemModal(null);
  });
  el("btn-wipe").addEventListener("click", openWipeModal);

  el("btn-save-mem").addEventListener("click", saveMemModal);
  el("btn-save-kw").addEventListener("click", saveKwModal);
  el("btn-run-rt").addEventListener("click", runRetrieveTest);
  el("btn-confirm-wipe").addEventListener("click", confirmWipe);

  el("f-importance").addEventListener("input", () => { el("v-importance").textContent = fmtNum(parseFloat(el("f-importance").value)); });
  el("f-confidence").addEventListener("input", () => { el("v-confidence").textContent = fmtNum(parseFloat(el("f-confidence").value)); });

  el("btn-confirm-ok").addEventListener("click", () => {
    closeModal("confirm-modal");
    if (S.confirmCb) { const cb = S.confirmCb; S.confirmCb = null; cb(); }
  });

  renderNav();
  refreshSessions(false).then((ok) => { if (ok) renderView(); });
}

document.addEventListener("DOMContentLoaded", init);
