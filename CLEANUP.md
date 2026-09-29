# CLEANUP.md — 疑似死代码 / 潜在缺陷记录

> 模块化重构期间的扫描发现。按重构规范：**一律不删除、不修改**，仅登记备案，留待项目所有者决策。
> 每条记录：位置、问题描述、影响、建议处理方式、发现日期。

---

## 1. 空文件：mem0_llm.py

- **位置**：`src/open_llm_vtuber/agent/agents/mem0_llm.py`
- **问题**：0 行空文件，疑似占位或残留。
- **影响**：无运行时影响（无代码）；但会误导阅读者以为存在 mem0 Agent 实现。
- **建议**：确认后删除，或补一个模块级 docstring 说明"规划中"。
- **发现**：2026-09-30（Phase 2 全量扫描）

## 2. 潜在 AttributeError：`tool_manager.disable()` 方法不存在

- **位置**：`src/open_llm_vtuber/agent/agents/basic_memory_agent_components/tool_interaction.py`（原 `basic_memory_agent.py` L508 附近，openai_loop 内）
- **问题**：当 LLM 返回 `__API_NOT_SUPPORT_TOOLS__` 时，代码调用 `tool_manager.disable()`，但 `mcpp/tool_manager.py` 的 `ToolManager` 类只有 `__init__` / `get_tool` / `get_formatted_tools` 三个方法，**没有 `disable()`**。
- **影响**：一旦触发该分支（LLM 明确表示不支持工具时）会抛 `AttributeError`。当前主用 LLM（火山方舟 deepseek）未触发过，属于**潜伏缺陷**。
- **现状**：本次重构按"不改业务逻辑"原则**原样搬运**（token 级等价验证通过），未修复。
- **建议**：在 `ToolManager` 补 `disable()` 方法（置空工具列表），或改用已有机制。修复前不要在生产环境启用会返回 `__API_NOT_SUPPORT_TOOLS__` 的模型。
- **发现**：2026-09-30（模块 #1 拆分时逐行核对）

## 3. 冗余逻辑：handle_interrupt 的 if/else 分支完全相同

- **位置**：`src/open_llm_vtuber/agent/agents/basic_memory_agent_components/session_memory.py`（原 `basic_memory_agent.py` handle_interrupt 方法）
- **问题**：按 `interrupt_role`（"system" / "user"）分支，但两个分支的代码**逐字节相同**——本应根据角色改写消息前缀，实际什么都没区分。
- **影响**：无功能错误（行为=单分支），但属于死逻辑，误导维护者以为有角色区分。
- **现状**：原样搬运，未修复。
- **建议**：确认上游设计意图后，要么删掉分支要么补上真正的人称改写。
- **发现**：2026-09-30（token 级等价对比时发现）

## 4. 性能观察：每次 WS 连接全量重建 MCP 组件（~4 秒）

- **位置**：`src/open_llm_vtuber/service_context.py` `_init_mcp_components` / `websocket_handler.py` `handle_new_connection`
- **问题**：每个客户端 WS 连接都完整重跑 MCP 动态工具构建（ServerRegistry → ToolAdapter → 两次 MCP server 连接 → ToolManager → MCPClient → ToolExecutor → StreamJSONDetector），实测从 WS accept 到 `set-model-and-conf` 发出约 **4 秒**。
- **影响**：前端连接后 ~4s 内收不到模型信息；不等待该消息的客户端会误判初始化失败（本地 E2E harness 即因此误报，已修复 harness 改为等待消息而非固定 1s）。服务端启动日志同样显示 `_init_mcp_components` 在启动时已跑过一遍——即**重复初始化**。
- **建议**：将 MCP 组件初始化提升为进程级单例（registry/adapter/tools 已有共享机制，ToolManager/MCPClient/ToolExecutor 可评估共享），或至少缓存 dynamic prompt string。
- **发现**：2026-09-30（E2E 误报排查，服务器日志时间戳 02:18:30 accept → 02:18:34 established）

## 5. 幽灵会话：`conf_uid=None` 的 LTM 数据库

- **位置**：`long_term_memory_data/None.db`
- **问题**：任何携带 `conf_uid=None` 的 API 查询都会让 LTM manager 惰性建库，产生 `None.db`。本次 E2E harness 误报期间产生过一次。
- **影响**：污染数据目录；若有人直接查询可能读到过期/错误记忆。
- **现状**：已删除 `None.db` 并 wipe "None" 会话（2026-09-30）。
- **建议**：`get_manager()` 对 `conf_uid` 为 None/空时拒绝建库并告警，属防御性修复（未实施，超出本次"不改逻辑"范围）。
- **发现**：2026-09-30（E2E 误报排查）

---

## 6. 【安全事件·已处置】2026-09-30 快照提交泄露密钥至公开 gitee

- **事件**：项目所有者 02:48 的全量快照提交（原 3fcd063）把 17 个敏感文件推送到**公开**的 gitee 仓库（mm-Super-Nices/ai），已用无登录 curl 验证可匿名拉取。
- **泄露内容**：
  1. 火山方舟 API 密钥 `ark-3473dfcf-****`（**生产在用**）——存在于 `conf.yaml.broken_bak` + 全部 12 个 `config/backups/*.yaml`；
  2. TLS 私钥 `config/tls/key.pem`（内网自签名，CN=10.1.1.10）；
  3. 用户对话记忆库 `long_term_memory_data/mao_pro_001.db`。
- **处置（本次完成）**：
  1. `git filter-branch` 重写全部 3 个受染提交，敏感文件已从 git 历史移除（新链 bd1277f→4406c7f→0f1b2f7，旧链留在本地分支 `backup-pre-rewrite`）；
  2. 磁盘文件已从备份分支恢复（服务零中断，TLS/后端健康检查 200）；
  3. `.gitignore` 补 5 类敏感路径并提交（ebfd82b）；
  4. 重构文档 4 件套入库（6e65c64）；
  5. **强推 gitee 待项目所有者执行**（服务器非交互会话无 gitee 凭证）：`git push --force origin master`。
- **遗留风险（密钥轮换前持续存在）**：gitee 页面/CDN 可能缓存旧提交；泄露窗口 02:48–03:20（推送到重写完成）约 32 分钟，无法确认未被第三方拉取。
- **建议**：① 立即在火山方舟控制台**作废并重建 API Key**（唯一彻底止损）；② 强推后在 gitee 仓库设置确认 Private；③ gitee 若有 fork/缓存提醒功能，关注 fork 数。

## 7. 备份残件：`server.py.bak-panel`

- **位置**：`src/open_llm_vtuber/server.py.bak-panel`
- **问题**：配置面板开发期留下的 server.py 备份文件，躺在包根级目录；已被 git 跟踪（含在 02:48 快照提交内）。
- **影响**：无运行时影响（非 .py 扩展名不会被打包器导入）；但污染包目录、误导阅读者，且 `.bak*` 命名会被一些备份/清理工具误处理。
- **现状**：按"不改业务逻辑"原则未动。
- **建议**：确认配置面板功能已稳定后删除（或移到 git 历史里留存即可——它已在一个提交中留档）。
- **发现**：2026-09-30（模块 #3 proxy 归位时目录扫描）

## 待续

后续模块逐个拆分时如有新发现，按同格式追加。
