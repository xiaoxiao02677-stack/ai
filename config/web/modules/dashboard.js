/* dashboard.js —— Control Center 仪表盘与工具视图（Phase 0.3 从 app.js 拆出）
   依赖注入：initDashboard({ $, esc, get, jfetch, apiStatus, getConfigTree })
   图标 / 模块 schema / 导航跳转直接复用 modules/navigation.js 的导出 */

import { ICONS, MODULE_SCHEMA, NAV_SCHEMA, openConfigPage, openMemoryWeb } from "./navigation.js";

let deps = null;

export function initDashboard(ctx) {
  deps = ctx;
}

/* ---------------- 仪表盘视图（dashboard / tools 占位） ---------------- */

/* 从 configTree 读当前配置摘要（只读展示，不发起写操作） */
function getConfigSummary() {
  const tree = deps.getConfigTree();
  if (!tree) return null;
  const agentChoice = deps.get(tree, "character_config.agent_config.conversation_agent_choice");
  const provider = deps.get(tree, `character_config.agent_config.agent_settings.${agentChoice}.llm_provider`);
  const model = deps.get(tree, `character_config.agent_config.llm_configs.${provider}.model`);
  return {
    agent: agentChoice,
    provider,
    model,
    asr: deps.get(tree, "character_config.asr_config.asr_model"),
    tts: deps.get(tree, "character_config.tts_config.tts_config.tts_model"),
    characterName: deps.get(tree, "character_config.character_name"),
    live2d: deps.get(tree, "character_config.live2d_model_name"),
  };
}

function dashboardStatusLine(label, value) {
  const has = value !== undefined && value !== null && String(value).trim() !== "";
  return `
    <div class="dash-status-row">
      <span class="dash-status-label">${deps.esc(label)}</span>
      <span class="dash-status-value ${has ? "" : "unset"}">${has ? deps.esc(String(value)) : "未配置"}</span>
    </div>`;
}

function dashboardModuleCard(mod, icon, summaryHtml) {
  const card = document.createElement("div");
  card.className = "dash-card";
  card.innerHTML = `
    <div class="dash-card-head">
      <span class="dash-card-icon">${ICONS[icon]}</span>
      <div class="dash-card-title">
        <h3>${deps.esc(mod.label)}</h3>
        <p>${deps.esc(mod.desc)}</p>
      </div>
    </div>
    ${summaryHtml}
    <div class="dash-card-actions"></div>`;
  const actions = card.querySelector(".dash-card-actions");
  if (mod.pages) {
    mod.pages.forEach((pid) => {
      const p = NAV_SCHEMA.find((x) => x.id === pid);
      if (!p) return;
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-ghost btn-sm";
      b.textContent = p.label;
      b.addEventListener("click", () => openConfigPage(pid));
      actions.appendChild(b);
    });
  }
  return card;
}

export function renderDashboard() {
  const content = deps.$("#content");
  deps.$("#page-title").textContent = "仪表盘";
  deps.$("#page-desc").textContent = "各模块状态总览与快捷入口";
  content.innerHTML = "";

  // 服务状态行（调 /config/api/status；失败显示"暂无状态"）
  const serviceCard = document.createElement("div");
  serviceCard.className = "dash-service";
  serviceCard.innerHTML = `
    <div class="dash-service-left">
      <span class="dash-service-dot pending"></span>
      <div>
        <div class="dash-service-title">主服务</div>
        <div class="dash-service-sub">Open-LLM-VTuber 运行状态</div>
      </div>
    </div>
    <div class="dash-service-status" id="dash-service-status">检查中…</div>`;
  content.appendChild(serviceCard);

  const s = getConfigSummary();

  const grid = document.createElement("div");
  grid.className = "dash-grid";

  grid.appendChild(dashboardModuleCard(
    MODULE_SCHEMA.find((m) => m.id === "ai"), "cpu",
    dashboardStatusLine("对话 Agent", s ? (s.agent || "未配置") : "加载失败") +
    dashboardStatusLine("LLM 提供方", s ? (s.provider || "未配置") : "") +
    dashboardStatusLine("模型", s ? (s.model || "未配置") : "")
  ));

  grid.appendChild(dashboardModuleCard(
    MODULE_SCHEMA.find((m) => m.id === "voice"), "audio",
    dashboardStatusLine("ASR 引擎", s ? (s.asr || "未配置") : "") +
    dashboardStatusLine("TTS 引擎", s ? (s.tts || "未配置") : "")
  ));

  grid.appendChild(dashboardModuleCard(
    MODULE_SCHEMA.find((m) => m.id === "character"), "user",
    dashboardStatusLine("角色名", s ? (s.characterName || "未配置") : "") +
    dashboardStatusLine("Live2D 模型", s ? (s.live2d || "未配置") : "")
  ));

  // 记忆：入口卡片，点击打开独立记忆面板
  const memoryCard = document.createElement("div");
  memoryCard.className = "dash-card";
  const memMod = MODULE_SCHEMA.find((m) => m.id === "memory");
  memoryCard.innerHTML = `
    <div class="dash-card-head">
      <span class="dash-card-icon">${ICONS.book}</span>
      <div class="dash-card-title">
        <h3>${deps.esc(memMod.label)}</h3>
        <p>${deps.esc(memMod.desc)}</p>
      </div>
    </div>
    <div class="dash-card-actions"><button type="button" class="btn btn-primary btn-sm">打开记忆面板</button></div>`;
  memoryCard.querySelector("button").addEventListener("click", openMemoryWeb);
  grid.appendChild(memoryCard);

  content.appendChild(grid);

  // 异步刷新服务状态
  refreshDashboardService(serviceCard);
}

async function refreshDashboardService(card) {
  const dot = card.querySelector(".dash-service-dot");
  const statusEl = card.querySelector(".dash-service-status");
  try {
    const st = await deps.jfetch(deps.apiStatus);
    statusEl.textContent = st.running ? "运行中" : "未运行";
    statusEl.className = `dash-service-status ${st.running ? "ok" : "down"}`;
    dot.className = `dash-service-dot ${st.running ? "ok" : "down"}`;
  } catch (e) {
    statusEl.textContent = "暂无状态";
    statusEl.className = "dash-service-status";
    dot.className = "dash-service-dot";
  }
}

export function renderTools() {
  const content = deps.$("#content");
  const mod = MODULE_SCHEMA.find((m) => m.id === "tools");
  deps.$("#page-title").textContent = "工具";
  deps.$("#page-desc").textContent = mod.desc;
  content.innerHTML = `
    <div class="dash-card dash-tools-placeholder">
      <div class="dash-card-head">
        <span class="dash-card-icon">${ICONS.wrench}</span>
        <div class="dash-card-title">
          <h3>工具（MCP）</h3>
          <p>外接工具调用能力，当前阶段暂未接入</p>
        </div>
      </div>
      <div class="dash-placeholder-body">
        <p>暂未配置 —— 工具模块将在后续阶段接入 MCP 服务器管理。</p>
      </div>
    </div>`;
}
