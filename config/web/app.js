/* ============================================================
   Config Panel — app.js
   zashboard-style conf.yaml editor mounted at /config/
   Phase 0.3 拆分：本文件是入口/协调器，持有全部全局状态；
   导航渲染在 modules/navigation.js，仪表盘在 modules/dashboard.js，
   配置编辑器在 modules/config-editor.js。依赖方向 app.js → 模块。
   ============================================================ */

import {
  ICONS,
  initNavigation,
  buildNav,
  handleModuleNavigation,
  openMemoryWeb,
  focusSearchToModule,
} from "./modules/navigation.js";
import { initDashboard, renderDashboard, renderTools } from "./modules/dashboard.js";
import { initConfigEditor, renderPage, updateSaveBtn, bindInputs } from "./modules/config-editor.js";

const API = {
  state: "/config/api/state",
  raw: "/config/api/raw",
  backups: "/config/api/backups",
  save: "/config/api/save",
  apply: "/config/api/apply",
  restore: "/config/api/restore",
  preview: "/config/api/preview",
  restart: "/config/api/restart",
  status: "/config/api/status",
};

/* ---------------- 中文备注词典（ZH_NOTES / ZH_LEAF_NOTES / ZH_CARD_NOTES / zhNote，已迁至 modules/config-editor.js） ---------------- */

/* ---------------- state ---------------- */

let configTree = null;
let comments = {};
let rawYaml = "";
let backupsList = [];
let currentModule = "dashboard"; // Control Center 一级模块 id
let currentPage = null;          // 当前 NAV_SCHEMA 配置页 id（仅配置编辑器视图）
const modulePageChoice = {};     // 模块 -> 上次停留的子页 id
let searchQuery = "";
let dirtyPaths = new Map(); // path -> new value
let savingLock = false;

/* ---------------- utils ---------------- */

const $ = (sel) => document.querySelector(sel);
const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

function get(obj, path) {
  const segs = path.split(".");
  let node = obj;
  for (const s of segs) {
    if (node == null || typeof node !== "object" || !(s in node)) return undefined;
    node = node[s];
  }
  return node;
}

function toast(kind, title, msg = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `<div><div class="t-title">${esc(title)}</div>${
    msg ? `<div class="t-msg">${esc(msg)}</div>` : ""
  }</div>`;
  $("#toasts").appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity 0.3s";
    setTimeout(() => el.remove(), 320);
  }, 3800);
}

async function jfetch(url, opts) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail || body.error || `HTTP ${res.status}`);
  }
  return body;
}

/* ---------------- data load ---------------- */

async function loadState() {
  const st = await jfetch(API.state);
  configTree = st.tree;
  comments = st.comments || {};
  rawYaml = st.raw;
  backupsList = st.backups || [];
}

/* ---------------- navigation（渲染与分发在 modules/navigation.js） ---------------- */

/* ---------------- 配置页渲染（valueType / renderRow / collectRows / groupIntoCards / renderPage，渲染在 modules/config-editor.js） ---------------- */

/* ---------------- Control Center 视图（仪表盘 / 工具占位，渲染在 modules/dashboard.js） ---------------- */

/* 总渲染入口：dashboard / tools 为产品视图，其余走配置编辑器 */
function renderMain() {
  if (currentModule === "dashboard") {
    renderDashboard();
  } else if (currentModule === "tools") {
    renderTools();
  } else if (currentPage) {
    renderPage();
  } else {
    // 兜底：模块无映射页时回到仪表盘
    currentModule = "dashboard";
    renderDashboard();
  }
  updateSaveBtn();
}

function render() {
  buildNav();
  renderMain();
  if (currentPage) bindInputs();
}

/* ---------------- dirty tracking（markDirty / updateSaveBtn / bindInputs，在 modules/config-editor.js） ---------------- */

/* ---------------- save flow ---------------- */

async function doApply() {
  if (!dirtyPaths.size || savingLock) return;
  savingLock = true;
  try {
    const changes = Object.fromEntries(dirtyPaths);
    const res = await jfetch(API.apply, {
      method: "POST",
      body: JSON.stringify({ changes }),
    });
    toast("success", "已保存", `${res.applied} 项修改已写入 conf.yaml（已自动备份）`);
    dirtyPaths.clear();
    await loadState();
    render();
  } catch (e) {
    toast("error", "保存失败", e.message);
  } finally {
    savingLock = false;
  }
}

/* ---------------- YAML modal ---------------- */

function openYaml() {
  $("#yaml-editor").value = rawYaml;
  $("#yaml-modal").classList.remove("hidden");
}

async function saveYaml() {
  const text = $("#yaml-editor").value;
  try {
    const res = await jfetch(API.preview, {
      method: "POST",
      body: JSON.stringify({ yaml_text: text }),
    });
    if (!res.ok) {
      toast("error", "YAML 校验失败", res.error);
      return;
    }
    const saved = await jfetch(API.save, {
      method: "POST",
      body: JSON.stringify({ yaml_text: text }),
    });
    toast("success", "已保存", `备份: ${saved.backup}`);
    $("#yaml-modal").classList.add("hidden");
    await loadState();
    render();
  } catch (e) {
    toast("error", "保存失败", e.message);
  }
}

/* ---------------- backups ---------------- */

async function openBackups() {
  await renderBackups();
  $("#backups-modal").classList.remove("hidden");
}

async function renderBackups() {
  const data = await jfetch(API.backups);
  const list = $("#backup-list");
  if (!data.backups.length) {
    list.innerHTML = `<div class="empty-state">暂无备份</div>`;
    return;
  }
  list.innerHTML = "";
  data.backups.forEach((b) => {
    const el = document.createElement("div");
    el.className = "backup-item";
    el.innerHTML = `
      <div class="b-icon">${ICONS.server}</div>
      <div class="b-info">
        <div class="b-name">${esc(b.name)}</div>
        <div class="b-meta">${esc(b.mtime)} · ${(b.size / 1024).toFixed(1)} KB</div>
      </div>
      <button class="btn btn-danger btn-sm">恢复</button>`;
    el.querySelector("button").addEventListener("click", async () => {
      if (!confirm(`恢复到 ${b.name}？当前配置会先备份。`)) return;
      try {
        await jfetch(API.restore, {
          method: "POST",
          body: JSON.stringify({ backup: b.name }),
        });
        toast("success", "已恢复", b.name);
        await loadState();
        render();
        $("#backups-modal").classList.add("hidden");
      } catch (e) {
        toast("error", "恢复失败", e.message);
      }
    });
    list.appendChild(el);
  });
}

/* ---------------- restart flow ---------------- */

async function doRestart() {
  if (!confirm("重启服务？进行中的对话会断开，页面会自动等待重连。")) return;
  try {
    await jfetch(API.restart, { method: "POST" });
    showRestartBanner();
    pollUntilUp();
  } catch (e) {
    toast("error", "重启失败", e.message);
  }
}

function showRestartBanner() {
  if ($("#restart-banner")) return;
  const el = document.createElement("div");
  el.id = "restart-banner";
  el.className = "restart-banner";
  el.innerHTML = `<div class="spinner"></div><span>服务重启中，等待恢复…</span>`;
  document.body.appendChild(el);
}

function hideRestartBanner() {
  const el = $("#restart-banner");
  if (el) el.remove();
}

async function pollUntilUp() {
  const started = Date.now();
  const poll = async () => {
    try {
      const st = await jfetch(API.status);
      if (st.running) {
        hideRestartBanner();
        toast("success", "服务已恢复", `PID: ${st.processes[0] || "ok"}`);
        await loadState();
        render();
        return;
      }
    } catch (e) {
      /* still down */
    }
    if (Date.now() - started > 120000) {
      hideRestartBanner();
      toast("error", "重启超时", "请手动检查服务状态（/tmp/ollvm.log）");
      return;
    }
    setTimeout(poll, 3000);
  };
  setTimeout(poll, 4000);
}

/* ---------------- search ---------------- */

function bindSearch() {
  let timer;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      searchQuery = e.target.value.trim();
      if (!currentPage) {
        focusSearchToModule(); // 仪表盘 / 工具视图下搜索不可用
        return;
      }
      renderPage();
      bindInputs();
    }, 200);
  });
}

/* ---------------- init ---------------- */

/* 向 navigation 模块注入状态访问器与渲染回调（app.js 持有全局状态） */
initNavigation({
  $,
  esc,
  toast,
  render,
  getModule: () => currentModule,
  setModule: (v) => { currentModule = v; },
  getPage: () => currentPage,
  setPage: (v) => { currentPage = v; },
  getModulePageChoice: () => modulePageChoice,
  getSearchQuery: () => searchQuery,
  setSearchQuery: (v) => { searchQuery = v; },
});

/* 向 dashboard 模块注入只读工具与状态访问器 */
initDashboard({
  $,
  esc,
  get,
  jfetch,
  apiStatus: API.status,
  getConfigTree: () => configTree,
});
/* 向 config-editor 模块注入工具与状态访问器（dirtyPaths 为共享 Map 引用） */
initConfigEditor({
  $,
  esc,
  get,
  getConfigTree: () => configTree,
  getComments: () => comments,
  getPage: () => currentPage,
  getModule: () => currentModule,
  getSearchQuery: () => searchQuery,
  getDirtyPaths: () => dirtyPaths,
});

async function init() {
  try {
    await loadState();
    render();
  } catch (e) {
    $("#content").innerHTML = `<div class="empty-state">加载失败: ${esc(e.message)}</div>`;
  }

  $("#btn-reload").addEventListener("click", async () => {
    await loadState();
    render();
    toast("success", "已重载");
  });
  $("#btn-yaml").addEventListener("click", openYaml);
  $("#btn-save").addEventListener("click", () => {
    if (!dirtyPaths.size) {
      toast("warn", "没有修改", "先修改表单或用 YAML 模式编辑");
      return;
    }
    doApply();
  });
  $("#btn-backups").addEventListener("click", openBackups);
  $("#btn-close-backups").addEventListener("click", () => $("#backups-modal").classList.add("hidden"));
  $("#btn-close-yaml").addEventListener("click", () => $("#yaml-modal").classList.add("hidden"));
  $("#btn-save-yaml").addEventListener("click", saveYaml);
  $("#btn-format").addEventListener("click", async () => {
    const text = $("#yaml-editor").value;
    try {
      const res = await jfetch(API.preview, {
        method: "POST",
        body: JSON.stringify({ yaml_text: text }),
      });
      if (res.ok) {
        $("#yaml-editor").value = res.roundtrip;
        toast("success", "已格式化");
      } else {
        toast("error", "YAML 错误", res.error);
      }
    } catch (e) {
      toast("error", "格式化失败", e.message);
    }
  });

  // restart button in topbar (append next to save)
  const restartBtn = document.createElement("button");
  restartBtn.className = "btn btn-ghost";
  restartBtn.id = "btn-restart";
  restartBtn.title = "重启服务";
  restartBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4"/><path d="M18.4 2.6 21 5.2"/><path d="M6.4 2.6 3.8 5.2"/><rect x="5" y="8" width="14" height="13" rx="2"/><path d="M9 12h.01M15 12h.01M9 16h.01M15 16h.01"/></svg> 重启`;
  restartBtn.addEventListener("click", doRestart);
  $(".topbar-actions").insertBefore(restartBtn, $("#btn-save"));

  bindSearch();
}

document.addEventListener("DOMContentLoaded", init);
