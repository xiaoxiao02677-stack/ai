/* 屿禾工坊 — device console frontend logic (vanilla JS, no framework) */
"use strict";

const API = {
  overview: "/workshop/api/overview",
  devices: "/workshop/api/devices",
  connect: "/workshop/api/connect",
  detail: (id) => `/workshop/api/devices/${encodeURIComponent(id)}`,
  led: (id) => `/workshop/api/devices/${encodeURIComponent(id)}/led`,
  diagnostics: (id) => `/workshop/api/devices/${encodeURIComponent(id)}/diagnostics`,
  p19r: (id) => `/workshop/api/devices/${encodeURIComponent(id)}/p19r`,
  events: (id) => `/workshop/api/devices/${encodeURIComponent(id)}/events`,
  logs: (id) => `/workshop/api/devices/${encodeURIComponent(id)}/logs`,
  security: (id) => `/workshop/api/devices/${encodeURIComponent(id)}/security`,
  protocol: (id) => `/workshop/api/devices/${encodeURIComponent(id)}/protocol`,
  capabilities: (id) => `/workshop/api/devices/${encodeURIComponent(id)}/capabilities`,
  health: (id) => `/workshop/api/devices/${encodeURIComponent(id)}/health`,
  commands: (id) => `/workshop/api/devices/${encodeURIComponent(id)}/commands`,
};

const el = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

function toast(msg, type = "info") {
  const t = document.createElement("div");
  t.className = `ws-toast ${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3800);
}

async function jfetch(url, opts = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  let data = null;
  try { data = await res.json(); } catch (e) { /* ignore */ }
  if (!res.ok) {
    const detail = data && (data.detail || data.error) ? (data.detail || data.error) : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return data;
}

const STATE_DOT = { ONLINE: "online", STALE: "stale", DISCONNECTED: "off", REJECTED: "rejected" };
const STATE_ZH = { ONLINE: "在线", STALE: "STALE", DISCONNECTED: "离线", REJECTED: "异常" };
const CMD_STATUS_CLASS = {
  CREATED: "st-CREATED", SENT: "st-SENT", ACKED: "st-ACKED",
  NACKED: "st-NACKED", REJECTED: "st-REJECTED", TIMEOUT: "st-TIMEOUT", FAILED: "st-FAILED",
};

/* ---------------- overview ---------------- */

async function loadOverview() {
  try {
    const d = await jfetch(API.overview);
    const t = d.totals;
    el("st-total").textContent = t.devices;
    el("st-online").textContent = t.online;
    el("st-stale").textContent = t.stale;
    el("st-disc").textContent = t.disconnected;
    renderDevices(d.devices);
    renderCommands(d.recent_commands);
  } catch (e) {
    el("device-list").innerHTML = `<div class="ws-empty">加载失败：${esc(e.message)}</div>`;
  }
}

function renderDevices(devices) {
  const box = el("device-list");
  if (!devices.length) {
    box.innerHTML = `<div class="ws-empty">暂无在线设备 —— 连接设备或运行 P19-R 验收后显示</div>`;
    return;
  }
  box.innerHTML = devices.map((d) => {
    const st = d.connection_state || "DISCONNECTED";
    const dot = STATE_DOT[st] || "off";
    const ops = (d.capabilities || []).map((o) => `<span class="chip">${esc(o)}</span>`).join("");
    const lastSeen = d.last_seen ? new Date(d.last_seen * 1000).toLocaleTimeString() : "—";
    const originBadge = d.origin === "gateway-inbound"
      ? '<span class="chip" style="color:var(--green);border-color:var(--green)">REAL</span>'
      : '<span class="chip" style="color:var(--yellow);border-color:var(--yellow)">${d.origin === "probe-outbound" ? "PROBE" : "?"}</span>';
    return `
    <div class="ws-card">
      <div class="d-name"><span class="dot ${dot}"></span>${esc(d.display_name || d.device_id)} ${originBadge}</div>
      <div class="d-row">${esc(d.device_id)}</div>
      <div class="d-row">${STATE_ZH[st] || st} · ${esc(d.firmware_version || "—")} · v${esc(d.protocol_version ?? "?")}</div>
      <div class="d-row">Session: ${esc((d.session_id || "").slice(0, 16))}</div>
      <div class="d-row">IP: ${esc(d.ip || "未连接")} · Last seen: ${esc(lastSeen)}</div>
      <div class="d-ops">${ops || '<span class="ws-hint">无声明能力</span>'}</div>
      <div class="d-actions">
        <button class="ws-btn" data-action="detail" data-id="${esc(d.device_id)}">设备详情</button>
        <button class="ws-btn" data-action="control" data-id="${esc(d.device_id)}">身体控制</button>
        <button class="ws-btn" data-action="diag" data-id="${esc(d.device_id)}">诊断</button>
      </div>
    </div>`;
  }).join("");
  box.querySelectorAll("button[data-action]").forEach((b) => {
    b.addEventListener("click", () => {
      const id = b.dataset.id;
      if (b.dataset.action === "detail") openDetail(id, "detail");
      if (b.dataset.action === "control") openDetail(id, "control");
      if (b.dataset.action === "diag") openDetail(id, "diag");
    });
  });
}

function renderCommands(cmds) {
  const box = el("cmd-table");
  if (!cmds.length) {
    box.innerHTML = `<div class="ws-empty">暂无命令记录</div>`;
    return;
  }
  box.innerHTML = `
  <table class="ws-cmd-table">
    <thead><tr><th>时间</th><th>Command ID</th><th>Operation</th><th>状态</th><th>Error</th></tr></thead>
    <tbody>
      ${cmds.map((c) => `
      <tr>
        <td>${esc(c.created_at ? new Date(c.created_at * 1000).toLocaleTimeString() : "—")}</td>
        <td>${esc((c.command_id || "").slice(0, 16))}</td>
        <td>${esc(c.operation)}</td>
        <td class="${CMD_STATUS_CLASS[c.status] || ""}">${esc(c.status)}</td>
        <td>${esc(c.error_code || "")}</td>
      </tr>`).join("")}
    </tbody>
  </table>`;
}

/* ---------------- connect ---------------- */

el("conn-btn").addEventListener("click", async () => {
  const host = el("conn-host").value.trim();
  const port = parseInt(el("conn-port").value, 10) || 3333;
  const st = el("conn-status");
  if (!host) { st.className = "ws-inline-status err"; st.textContent = "请输入设备 IP"; return; }
  st.className = "ws-inline-status"; st.textContent = "连接中…";
  try {
    const d = await jfetch(API.connect, { method: "POST", body: JSON.stringify({ host, port }) });
    st.className = "ws-inline-status ok";
    st.textContent = `已接入 ${d.device_id}（能力: ${(d.operations || []).join(", ") || "无"}）`;
    loadOverview();
  } catch (e) {
    st.className = "ws-inline-status err"; st.textContent = e.message;
  }
});

el("cmd-refresh").addEventListener("click", loadOverview);

/* ---------------- detail drawer ---------------- */

const overlay = el("detail-overlay");

overlay.addEventListener("mousedown", (e) => {
  if (e.target === overlay) overlay.classList.add("hidden");
});

async function openDetail(deviceId, tab) {
  overlay.classList.remove("hidden");
  const body = el("detail-body");
  body.innerHTML = `<div class="ws-empty">加载设备详情…</div>`;
  try {
    const d = await jfetch(API.detail(deviceId));
    renderDetail(body, d, tab, deviceId);
  } catch (e) {
    body.innerHTML = `<h2>加载失败</h2><div class="ws-empty">${esc(e.message)}</div>`;
  }
}

function renderDetail(body, d, tab, deviceId) {
  const st = d.connection_state || "DISCONNECTED";
  const dot = STATE_DOT[st] || "off";
  const health = d.health || {};
  const ready = health.readiness || {};
  body.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <h2><span class="dot ${dot}"></span> ${esc(d.device_id)}</h2>
      <button class="ws-btn ghost" id="detail-close">关闭 ✕</button>
    </div>

    <div class="ws-section"><h3>基本信息</h3>
      <div class="ws-kv">
        <span class="k">Device ID</span><span class="v">${esc(d.device_id)}</span>
        <span class="k">Device Type</span><span class="v">${esc(d.device_type || "—")}</span>
        <span class="k">Firmware</span><span class="v">${esc(d.firmware_version || "—")}</span>
        <span class="k">Protocol Version</span><span class="v">v${esc(d.protocol_version ?? "—")}</span>
        <span class="k">Session ID</span><span class="v">${esc(d.session_id || "—")}</span>
        <span class="k">IP</span><span class="v">${esc(d.ip || "未连接")}${d.port ? ":" + d.port : ""}</span>
        <span class="k">Connected At</span><span class="v">${esc(d.connected_at ? new Date(d.connected_at * 1000).toLocaleString() : "—")}</span>
        <span class="k">Last Seen</span><span class="v">${esc(d.last_seen ? new Date(d.last_seen * 1000).toLocaleString() : "—")}</span>
      </div>
    </div>

    <div class="ws-section"><h3>连接与健康（P18 DeviceHealth）</h3>
      <div class="ws-kv">
        <span class="k">Connection</span><span class="v">${esc(st)}（${STATE_ZH[st] || "—"}）</span>
        <span class="k">Heartbeat Age</span><span class="v">${esc(health.heartbeat_age ?? "—")}s</span>
        <span class="k">Protocol Match</span><span class="v">${health.protocol_compatible ? "✓ 匹配" : "✕ 不匹配"}</span>
        <span class="k">Readiness</span><span class="v">${ready.ready ? `✓ 就绪（${esc(ready.gate_code)}）` : `✕ 未就绪（${esc(ready.gate_code)}）`}</span>
      </div>
      <div class="ws-hint" style="margin-top:6px;">Readiness ≠ Authorization——就绪表示门禁通过，执行仍须经 Policy/Gateway 授权。</div>
    </div>

    <div class="ws-section"><h3>身体控制（当前唯一动作：SET_LED）</h3>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
        <button class="ws-btn primary" id="led-on">点亮</button>
        <button class="ws-btn" id="led-off">熄灭</button>
        <span id="led-status" class="ws-inline-status"></span>
      </div>
      <div id="led-result" class="ws-step-list" style="margin-top:8px;"></div>
      <div class="ws-hint" style="margin-top:6px;">命令经完整链路：Intent → Capability → Policy → Gateway → Adapter → TCP → 设备 → ACK → ExecutionResult（前端不直连设备）。</div>
    </div>

    <div class="ws-section"><h3>设备诊断（只读）</h3>
      <button class="ws-btn" id="diag-run">运行诊断</button>
      <div id="diag-result" class="ws-step-list" style="margin-top:8px;"></div>
    </div>

    <div class="ws-section"><h3>P19-R 实机验收</h3>
      <div class="ws-hint">完整验收序列（TCP → HELLO → ADVERTISEMENT → Session → Gate → 六项失败探针 → SET_LED ON/OFF → 幂等 → Late ACK），执行到 LED 时需人工确认物理灯状态。</div>
      <button class="ws-btn primary" id="p19r-run" style="margin-top:8px;">开始 P19-R 验收</button>
      <div id="p19r-result" class="ws-step-list" style="margin-top:8px;"></div>
    </div>

    <div class="ws-section"><h3>安全状态（只读）</h3>
      <div id="security-box" class="ws-kv"><span class="k">加载中…</span></div>
    </div>

    <div class="ws-section"><h3>协议（Protocol）</h3>
      <div id="protocol-box" class="ws-kv"><span class="k">加载中…</span></div>
    </div>

    <div class="ws-section"><h3>设备日志</h3>
      <div id="log-box" class="ws-log">加载中…</div>
    </div>
  `;

  body.querySelector("#detail-close").addEventListener("click", () => overlay.classList.add("hidden"));
  body.querySelector("#led-on").addEventListener("click", () => runLed(deviceId, true));
  body.querySelector("#led-off").addEventListener("click", () => runLed(deviceId, false));
  body.querySelector("#diag-run").addEventListener("click", () => runDiagnostics(deviceId));
  body.querySelector("#p19r-run").addEventListener("click", () => runP19R(deviceId));
  loadSecurity(deviceId);
  loadProtocol(deviceId);
  loadLogs(deviceId);
}

/* ---------------- LED (full chain) ---------------- */

async function runLed(deviceId, on) {
  const st = el("led-status");
  const box = el("led-result");
  st.className = "ws-inline-status"; st.textContent = "正在发送…";
  box.innerHTML = "";
  const line = (cls, txt) => {
    const div = document.createElement("div");
    div.className = cls; div.textContent = txt;
    box.appendChild(div);
  };
  try {
    const r = await jfetch(API.led(deviceId), { method: "POST", body: JSON.stringify({ on }) });
    st.className = "ws-inline-status ok"; st.textContent = "完成";
    line(r.status === "simulated" ? "ok" : r.status === "rejected" ? "bad" : "info",
      `ExecutionResult: ${r.status}`);
    line("info", `操作: ${r.operation} → capability: ${r.capability}`);
    line("info", `原因: ${r.reason}`);
    if (r.status === "simulated") {
      line("ok", `✓ LED 已${on ? "开启" : "熄灭"}（CommandID: ${r.command_id}）`);
    } else if (r.status === "rejected") {
      line("bad", `✕ 被拒绝（Policy/Kill Switch 拦截——真实设备动作仅经 P19-R 验收链）`);
    }
    line("info", `命令生命周期: CREATED → SENT → ${r.status === "simulated" ? "ACKED" : "REJECTED"}（见命令历史表）`);
  } catch (e) {
    st.className = "ws-inline-status err"; st.textContent = "失败";
    line("bad", `✕ 操作失败: ${e.message}`);
  }
  loadOverview();
}

/* ---------------- diagnostics (read-only) ---------------- */

async function runDiagnostics(deviceId) {
  const box = el("diag-result");
  box.innerHTML = `<div class="info">诊断中…</div>`;
  try {
    const d = await jfetch(API.diagnostics(deviceId), { method: "POST", body: JSON.stringify({}) });
    box.innerHTML = d.checks.map((c) =>
      `<div class="${c.ok ? "ok" : "bad"}">${c.ok ? "✓" : "✕"} ${esc(c.name)}：${esc(c.detail)}</div>`
    ).join("") + `<div class="${d.all_ok ? "ok" : "bad"}">${d.all_ok ? "全部通过" : "存在异常"}</div>`;
  } catch (e) {
    box.innerHTML = `<div class="bad">✕ ${esc(e.message)}</div>`;
  }
}

/* ---------------- security / protocol / logs ---------------- */

async function loadSecurity(deviceId) {
  const box = el("security-box");
  try {
    const s = await jfetch(API.security(deviceId));
    box.innerHTML = `
      <span class="k">Global Kill Switch</span>
      <span class="v">${s.global_execution_enabled ? "ENABLED" : "DISABLED（代码级，Web 只读）"}</span>
      <span class="k">Execution Policy</span><span class="v">${esc(s.execution_policy)}</span>
      <span class="k">Gateway</span><span class="v">${esc(s.gateway)}</span>
      <span class="k">Device Gate</span>
      <span class="v">${s.device_gate.allowed ? "✓ 通过" : "✕ 拒绝"}（${esc(s.device_gate.code)}）</span>`;
  } catch (e) {
    box.innerHTML = `<span class="k">加载失败</span><span class="v">${esc(e.message)}</span>`;
  }
}

async function loadProtocol(deviceId) {
  const box = el("protocol-box");
  try {
    const p = await jfetch(API.protocol(deviceId));
    box.innerHTML = `
      <span class="k">Protocol Version</span><span class="v">v${esc(p.protocol_version)}</span>
      <span class="k">Connection</span><span class="v">${esc(p.connection_state)}</span>
      <span class="k">Transport</span><span class="v">${esc(p.transport.type)} : ${esc(p.transport.port)}</span>
      <span class="k">Last HELLO</span><span class="v">${p.last_hello.received ? "已接收（firmware: " + esc(p.last_hello.firmware) + "）" : "未接收"}</span>
      <span class="k">Last ADVERTISEMENT</span><span class="v">${esc((p.last_advertisement.operations || []).join(", ") || "无")}</span>
      <span class="k">Session</span><span class="v">${esc((p.session_id || "").slice(0, 16))}</span>`;
  } catch (e) {
    box.innerHTML = `<span class="k">加载失败</span><span class="v">${esc(e.message)}</span>`;
  }
}

async function loadLogs(deviceId) {
  const box = el("log-box");
  try {
    const d = await jfetch(API.logs(deviceId));
    box.innerHTML = d.logs.length
      ? d.logs.map((l) => `<div class="L-${l.level}">${esc(l.ts)} [${esc(l.level)}] ${esc(l.event)}</div>`).join("")
      : "暂无日志";
    box.scrollTop = box.scrollHeight;
  } catch (e) {
    box.textContent = `加载失败: ${e.message}`;
  }
}

/* ---------------- P19-R flow ---------------- */

async function runP19R(deviceId) {
  const box = el("p19r-result");
  const btn = el("p19r-run");
  btn.disabled = true;
  box.innerHTML = `<div class="info">验收序列启动中…（含 TCP 往返，耗时约 10-40 秒）</div>`;
  const line = (cls, txt) => {
    const div = document.createElement("div");
    div.className = cls; div.textContent = txt;
    box.appendChild(div);
  };

  // ask the human about physical LED twice up front (the API applies
  // these confirmations at the led_confirm steps of the sequence)
  let confirmOn = false, confirmOff = false;
  confirmOn = window.confirm("P19-R 将在验收过程中执行 SET_LED ON。\n\n请准备好观察设备 LED。\n\n【点\"确定\"表示：LED 点亮时你将确认它确实亮了】");
  confirmOff = window.confirm("随后将执行 SET_LED OFF。\n\n【点\"确定\"表示：LED 熄灭时你将确认它确实灭了】");
  if (!confirmOn || !confirmOff) {
    box.innerHTML = `<div class="bad">已取消：物理 LED 确认是 P19-R 验收的必要环节（禁止自动假设）。</div>`;
    btn.disabled = false;
    return;
  }

  try {
    const r = await jfetch(API.p19r(deviceId), {
      method: "POST",
      body: JSON.stringify({ confirm_led_on: confirmOn, confirm_led_off: confirmOff }),
    });
    box.innerHTML = "";
    let curSection = "";
    r.steps.forEach((s) => {
      if (s.kind === "section") {
        curSection = s.title;
        const div = document.createElement("div");
        div.className = "info";
        div.textContent = `── ${s.title} ──`;
        box.appendChild(div);
      } else if (s.kind === "check") {
        line(s.ok ? "ok" : "bad", `${s.ok ? "✓" : "✕"} ${s.name}${s.detail ? "（" + s.detail + "）" : ""}`);
      } else if (s.kind === "led_confirm") {
        line(s.confirmed ? "ok" : "bad", `[人工确认] LED ${s.phase === "on" ? "点亮" : "熄灭"}: ${s.confirmed ? "已确认" : "未确认"}`);
      } else if (s.kind === "fatal") {
        line("bad", `✕ 终止: ${s.reason}`);
      } else if (s.kind === "info") {
        line("info", s.text);
      }
    });
    line("info", `──── 最终结果: ${r.passed} passed / ${r.failed} failed ${r.fatal ? "（FATAL: " + r.fatal + "）" : ""}`);
  } catch (e) {
    box.innerHTML = `<div class="bad">✕ 验收失败: ${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    loadOverview();
  }
}

/* ---------------- boot ---------------- */

loadOverview();
setInterval(loadOverview, 15000);   // polling fallback (spec §11: no new bus)
