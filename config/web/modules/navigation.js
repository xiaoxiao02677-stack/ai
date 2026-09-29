/* ============================================================
   navigation.js — 侧栏导航与模块分发（Control Center）
   从 app.js 拆出（Phase 0.3 解耦）。全局状态仍由 app.js 持有，
   本模块通过 initNavigation(ctx) 注入的访问器读写状态并回调渲染，
   依赖方向：app.js → navigation.js（单向，无循环依赖）。
   ============================================================ */

/* ---------------- taxonomy: which subtree renders as which page ---------------- */

export const NAV_SCHEMA = [
  {
    id: "system",
    label: "系统",
    icon: "server",
    desc: "服务主机 / 端口 / 工具提示词等系统级设置",
    root: "system_config",
  },
  {
    id: "character",
    label: "角色",
    icon: "user",
    desc: "角色名称 / 头像 / 人设提示词",
    root: "character_config",
    skip: ["agent_config"],
  },
  {
    id: "agent",
    label: "Agent 与模型",
    icon: "cpu",
    desc: "对话 Agent 选择、LLM 提供方与凭据",
    root: "character_config.agent_config",
  },
  {
    id: "asr",
    label: "语音识别 ASR",
    icon: "mic",
    desc: "语音转文字模型配置",
    root: "character_config.asr_config",
  },
  {
    id: "tts",
    label: "语音合成 TTS",
    icon: "audio",
    desc: "文字转语音模型配置",
    root: "character_config.tts_config",
  },
  {
    id: "preprocess",
    label: "TTS 预处理",
    icon: "filter",
    desc: "朗读前的文本清洗与翻译设置",
    root: "character_config.tts_preprocessor_config",
  },
  {
    id: "vad",
    label: "活动检测 VAD",
    icon: "activity",
    desc: "语音活动检测阈值",
    root: "character_config.vad_config",
  },
  {
    id: "live",
    label: "直播",
    icon: "radio",
    desc: "Bilibili 直播接入",
    root: "live_config",
  },
];

/* ---------------- Control Center 模块层 ----------------
   产品级一级导航。pages 仅做导航映射（指向 NAV_SCHEMA 的页 id），
   不改变任何 YAML / API / 数据结构。
   memory = 独立记忆面板入口（/memory/）；tools = 占位（暂未接入）。 */

export const MODULE_SCHEMA = [
  {
    id: "dashboard",
    label: "仪表盘",
    icon: "home",
    desc: "状态总览与快捷入口",
  },
  {
    id: "ai",
    label: "AI 与模型",
    icon: "cpu",
    desc: "对话 Agent 选择、LLM 提供方与凭据",
    pages: ["agent"],
  },
  {
    id: "voice",
    label: "语音",
    icon: "audio",
    desc: "语音识别 / 语音合成 / 预处理 / 活动检测",
    pages: ["asr", "tts", "preprocess", "vad"],
  },
  {
    id: "character",
    label: "角色",
    icon: "user",
    desc: "角色名称 / 头像 / 人设提示词",
    pages: ["character"],
  },
  {
    id: "memory",
    label: "记忆",
    icon: "book",
    desc: "对话记忆管理（独立面板）",
  },
  {
    id: "tools",
    label: "工具",
    icon: "wrench",
    desc: "MCP 工具（暂未接入）",
  },
  {
    id: "system",
    label: "系统",
    icon: "server",
    desc: "服务设置与直播接入",
    pages: ["system", "live"],
  },
];

export const ICONS = {
  server: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>',
  user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
  cpu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/></svg>',
  mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>',
  audio: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>',
  filter: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>',
  activity: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
  radio: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49M7.76 16.24a6 6 0 0 1 0-8.49M19.07 4.93a10 10 0 0 1 0 14.14M4.93 19.07a10 10 0 0 1 0-14.14"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>',
  home: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>',
  book: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
  wrench: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
};

/* app.js 注入的状态访问与渲染回调（initNavigation 在 app.js 启动时调用一次） */
let deps = null;

export function initNavigation(ctx) {
  deps = ctx;
}

/* ---------------- navigation ---------------- */

/* 渲染侧栏一级模块导航。配置页（NAV_SCHEMA）作为模块的二级入口，
   不再直接出现在侧栏；点击模块后由 handleModuleNavigation 分发。 */
export function buildNav() {
  const nav = deps.$("#nav");
  nav.innerHTML = "";
  MODULE_SCHEMA.forEach((mod) => {
    const item = document.createElement("div");
    item.className = `nav-item${mod.id === deps.getModule() ? " active" : ""}`;
    item.dataset.module = mod.id;
    let extra = "";
    if (mod.id === "memory") extra = `<span class="nav-link-icon">${ICONS.chevron}</span>`;
    if (mod.id === "tools") extra = `<span class="nav-badge">即将上线</span>`;
    item.innerHTML = `
      <span class="nav-icon">${ICONS[mod.icon]}</span>
      <span class="nav-label">${deps.esc(mod.label)}</span>${extra}`;
    item.addEventListener("click", () => handleModuleNavigation(mod.id));
    nav.appendChild(item);
  });
}

/* 模块导航分发：dashboard / memory / tools 为独立视图，
   其余模块进入映射的 NAV_SCHEMA 配置页。 */
export function handleModuleNavigation(moduleId) {
  const mod = MODULE_SCHEMA.find((m) => m.id === moduleId);
  if (!mod) return;
  if (moduleId === "memory") {
    openMemoryWeb(); // 记忆是独立面板：新标签页打开，不切换当前视图
    return;
  }
  deps.setModule(moduleId);
  deps.setSearchQuery("");
  deps.$("#search").value = "";
  if (!mod.pages) {
    deps.setPage(null);
  } else {
    const choice = deps.getModulePageChoice();
    deps.setPage(choice[moduleId] || mod.pages[0]);
  }
  deps.render();
}

/* 进入某个 NAV_SCHEMA 配置页（由模块视图 / 仪表盘快捷入口调用） */
export function openConfigPage(pageId) {
  const mod = MODULE_SCHEMA.find((m) => m.pages && m.pages.includes(pageId));
  if (mod) {
    deps.setModule(mod.id);
    deps.getModulePageChoice()[mod.id] = pageId;
  } else {
    deps.setModule("system");
  }
  deps.setPage(pageId);
  deps.setSearchQuery("");
  deps.$("#search").value = "";
  deps.render();
}

/* 打开独立记忆面板（server.py 将 config/memory_web 挂载在 /memory/） */
export function openMemoryWeb() {
  window.open("/memory/", "_blank");
}

/* 侧栏搜索在模块视图下不可用：提示用户先进入具体模块 */
export function focusSearchToModule() {
  if (!deps.getSearchQuery()) return; // 清空输入时不提示
  const mod = MODULE_SCHEMA.find((m) => m.id === deps.getModule());
  if (mod && mod.pages) return; // 配置编辑器视图，搜索可用
  deps.toast("warn", "请先进入具体模块", "搜索仅作用于配置项（如 AI 与模型 / 语音 / 系统）");
}
