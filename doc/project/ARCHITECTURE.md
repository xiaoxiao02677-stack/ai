# Open-LLM-VTuber 部署与运行说明

> AI 女友 / AI 伴侣系统（Open-LLM-VTuber v1.2.x）
> 服务器：`10.1.1.10`（局域网） · 项目路径：`/home/uu/桌面/Open-LLM-VTuber`
> 文档生成：2026-09-29

---

## 1. 一句话说明

浏览器通过 **`https://10.1.1.10:12393/`** 打开网页，即可与 AI 女友实时文字/语音聊天。
对外只有一个 HTTPS 端口 **12393**，它由纯 Python 的 TLS 代理接管，转发到内部只监听回环地址的后端 `127.0.0.1:12395`。

**为什么必须是 HTTPS：** 浏览器的麦克风（`getUserMedia`）和屏幕共享（`getDisplayMedia`）只在「安全上下文」下可用。局域网 IP + HTTP 会被浏览器直接禁用这两个 API，必须走 HTTPS（自签证书）。同时 HTTPS 也顺带解决 WebSocket 的 mixed-content 问题。

---

## 2. 架构总览

```
                     局域网（10.1.1.10）
   ┌──────────────┐
   │  浏览器 / 手机 │
   └──────┬───────┘
          │  https://10.1.1.10:12393   （自签证书，首次需点「高级 → 继续前往」）
          ▼
   ┌─────────────────────────────────────────────┐
   │  config/tls_proxy.py   ← TLS 终止代理        │
   │  监听 0.0.0.0:12393                          │
   │  · 首字节 0x16(TLS) → 字节级透传后端          │
   │  · 明文 http 请求  → 301 跳 https            │
   │  · HTTP + WebSocket 通吃（含 Upgrade）        │
   └──────┬──────────────────────────────────────┘
          │  内部明文转发（回环，不暴露局域网）
          ▼
   ┌─────────────────────────────────────────────┐
   │  FastAPI / Uvicorn  127.0.0.1:12395          │
   │  （conf.yaml: system_config.host/port）       │
   │                                              │
   │  ├─ /                 前端页面（frontend/）   │
   │  ├─ /client-ws        WebSocket 聊天主通道    │
   │  ├─ /config/          conf.yaml 可视化面板    │
   │  ├─ /memory/          长期记忆管理面板        │
   │  ├─ /api/memory       LTM REST 别名          │
   │  ├─ /web-tool/        ASR / TTS 测试页        │
   │  ├─ /cache /live2d-models /bg /avatars        │
   │  └─ /proxy-ws         代理转发                │
   └─────────────────────────────────────────────┘
          │
          ▼
   LLM（火山方舟云端 API） · ASR · TTS · VAD
```

### 2.1 关键设计点

| 项 | 值 | 说明 |
|---|---|---|
| 对外端口 | **12393** | 仅此处对局域网开放，TLS 加密 |
| 后端端口 | 127.0.0.1:**12395** | 仅回环，局域网无法直连 |
| 证书 | `config/tls/`，CN=10.1.1.10，10 年 | 自签，非 CA 签发 |
| 旧端口 12394 | **已退役** | 曾经的 HTTPS 端口，已废弃 |
| TLS 代理进程 | `python3 config/tls_proxy.py` | 常驻后台，与后端独立 |

---

## 3. 怎么访问

### 3.1 打开聊天页面

1. 浏览器访问 **`https://10.1.1.10:12393/`**
2. 首次会提示证书不受信任 → 点 **「高级」→「继续前往 10.1.1.10（不安全）」**
   （自签证书的正常现象，点一次以后该浏览器不再提示）
3. 进入主界面即可打字聊天；点麦克风图标可以说话（HTTPS 已解锁麦克风权限）

> 前端会自动把连接地址按当前页面 origin 覆盖成 `wss://10.1.1.10:12393`，无需手工改设置。

### 3.2 三个入口一览

| 地址 | 用途 |
|---|---|
| `https://10.1.1.10:12393/` | 聊天主界面（前端 + WebSocket） |
| `https://10.1.1.10:12393/config/` | **配置面板**：改 `conf.yaml` 的全部参数（含中文备注），支持备份/回滚/一键重启 |
| `https://10.1.1.10:12393/memory/` | **长期记忆面板**：查看/编辑 AI 记住的关于你的信息、关键词、用户状态 |

---

## 4. 启动 / 重启 / 停止

### 4.1 一键重启（推荐）

```bash
# 在服务器上执行
cd /home/uu/桌面/Open-LLM-VTuber
bash config/restart_server.sh
```

它按正确顺序做三件事：
1. `pkill -f run_server.py`（先温和，再 `-9`）
2. `setsid /root/.local/bin/uv run run_server.py > /tmp/ollvm.log 2>&1 < /dev/null &` —— **脱离 SSH 会话**，防止退出登录把服务带走
3. 轮询 `http://127.0.0.1:12395/` 就绪后，重启 TLS 代理

> 也可以在配置面板右上角点 **「重启服务」** 按钮，等价效果。

### 4.2 手工启动后端

```bash
cd /home/uu/桌面/Open-LLM-VTuber
setsid /root/.local/bin/uv run run_server.py > /tmp/ollvm.log 2>&1 < /dev/null &
```

**⚠️ 铁律：** 必须用 `setsid ... < /dev/null &` 完全脱离 SSH 会话。否则 SSH 断开 → 进程被杀 → 服务掉线。
`uv` 的绝对路径是 `/root/.local/bin/uv`（不在非交互 shell 的 PATH 里，必须用全路径）。

### 4.3 TLS 代理控制

```bash
cd /home/uu/桌面/Open-LLM-VTuber
bash config/tlsctl.sh status     # 查看状态（进程号 + 12393 监听）
bash config/tlsctl.sh start      # 启动
bash config/tlsctl.sh stop       # 停止
bash config/tlsctl.sh restart    # 重启
```

日志：`/tmp/tls_proxy.log`

### 4.4 查看运行状态

```bash
# 端口监听
ss -tlnp | grep -E ':(12393|12395)'
#   期望：12393 = python3（TLS 代理，0.0.0.0）
#        12395 = python/uvicorn（后端，127.0.0.1）

# 进程
ps aux | grep -E 'run_server|tls_proxy' | grep -v grep

# 后端日志（实时）
tail -f /tmp/ollvm.log
```

---

## 5. 目录结构与模块划分

```
/home/uu/桌面/Open-LLM-VTuber/
├── conf.yaml                  # 主配置（LLM / ASR / TTS / VAD / 人格 / 端口…）
├── config_templates/
│   └── conf.default.yaml      # 默认模板基线（config_sync 依据它合并）
├── run_server.py              # 启动入口
│
├── config/                    # ★ 本项目自建：面板 + HTTPS 基础设施
│   ├── panel.py               #   配置面板后端 API（/config/api）
│   ├── memory_panel.py        #   记忆面板后端 API（/memory/api + /api/memory）
│   ├── web/                   #   配置面板前端（index.html / style.css / app.js）
│   ├── memory_web/            #   记忆面板前端（index.html / style.css / app.js）
│   ├── tls_proxy.py           #   ★ HTTPS 单端口 TLS 代理
│   ├── tlsctl.sh              #   代理控制脚本
│   ├── restart_server.sh      #   服务重启脚本
│   ├── tls/                   #   自签证书（cert.pem / key.pem）
│   └── backups/               #   conf.yaml 自动备份（保留最近 30 份）
│
├── long_term_memory_data/     # ★ 长期记忆数据（每角色一个 SQLite 文件）
│   └── mao_pro_001.db
│
├── frontend/                  # 前端页面（构建产物）
├── web_tool/                  # ASR / TTS 测试页
├── live2d-models/  backgrounds/  avatars/  cache/
│
├── open_llm_vtuber/           # 后端主包
│   ├── server.py              #   FastAPI 装配（挂载面板路由，须在 / catch-all 之前）
│   ├── websocket_handler.py   #   WebSocket 连接管理
│   ├── message_handler.py     #   消息分发
│   ├── service_context.py     #   服务上下文（LLM/ASR/TTS/VAD 实例）
│   ├── routes.py              #   路由注册
│   ├── conversations/         #   会话逻辑（含 LTM 检索/抽取钩子）
│   ├── agent/                 #   Agent 与 LLM 抽象层
│   │   ├── agents/            #     basic_memory_agent 等
│   │   └── stateless_llm/     #     LLM Provider 抽象（openai_compatible / ollama / …）
│   ├── long_term_memory/      # ★ 长期记忆子系统（详见第 7 节）
│   ├── asr/  tts/  vad/       #   语音识别 / 合成 / 端点检测
│   ├── mcpp/                  #   MCP 工具执行器
│   ├── config_manager/        #   配置加载与同步
│   └── live/  utils/  translate/
│
└── tools/                     # 运维小工具
    ├── set_vision_logo.py     #   按 conf.yaml 判定视觉模型、给相机图标染红
    ├── migrate_https_12393.py #   12394→12393 单端口迁移脚本
    └── ltm_e2e.py             #   长期记忆端到端测试
```

---

## 6. 配置说明

### 6.1 改配置的两种方式

| 方式 | 做法 | 生效 |
|---|---|---|
| **Web 面板（推荐）** | `https://10.1.1.10:12393/config/` 改完点保存 → 点「重启服务」 | 需重启 |
| 直接改 `conf.yaml` | 编辑后执行 `bash config/restart_server.sh` | 需重启 |

面板特性：
- 左侧 8 个分区导航 + 搜索框
- **每个参数下方都有中文小字备注**（当前覆盖 8 分区 / 424 个参数，100%）
- switch / 数字 / 文本 / 逗号列表等自适应控件
- YAML 源码模态框、备份管理、预览、回滚
- 写入保留原注释（ruamel round-trip），改前自动备份到 `config/backups/`

> ⚠️ **`conf.yaml` 启动时会被 `config_sync` 规范化**：合并模板默认值、删除多余 key。
> 所以加自定义字段无效——长期记忆的配置因此单独放在 `long_term_memory/memory_config.json`，不走 conf.yaml。

### 6.2 当前生效的关键配置

| 分类 | 配置项 | 当前值 |
|---|---|---|
| 服务 | `system_config.host` | `127.0.0.1` |
| 服务 | `system_config.port` | `12395` |
| LLM | `llm_provider` | `openai_compatible_llm` |
| LLM | 接口地址 | `https://ark.cn-beijing.volces.com/api/v3`（火山方舟） |
| LLM | 模型 | `deepseek-v4-1-flash-260910` |
| TTS | `tts_model` | `edge_tts`（微软 Edge，免费、无需密钥） |
| ASR | `asr_model` | `groq_whisper_asr`（云端，当前未配 key → 语音转文字不可用） |
| 视觉 | — | 当前 LLM 非视觉模型 → 前端相机/屏幕图标显示为**红色** |

> **⚠️ 千万不要把 `system_config.port` 改回 12393** —— 会和 TLS 代理抢端口。它指的是后端内部监听端口。

### 6.3 关于语音转文字（ASR）

当前 `asr_model: 'groq_whisper_asr'` 是**云端 API**，没有 key 就转写失败——这就是「没有语音转文字接口」的现象。

**本地离线方案（已就绪，改一行即可）：**

- 模型已下载：`models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.onnx`（895 MB，中/英/日/韩/粤）
- 依赖已安装：`sherpa-onnx 1.10.46` + `onnxruntime 1.23.2`
- 操作：面板里把 `asr_model` 改成 **`sherpa_onnx_asr`** → 重启服务 → 全离线转写

> ⚠️ **内存权衡**：该机 2.4 GB 内存 / 空闲约 1 GB + 2 GB swap。加载 895 MB int8 模型会比较吃紧，可能出现 swap 抖动。如果卡顿，可以考虑换更小的 ASR 模型或加内存。

麦克风本身没问题：`https://` 入口已经解锁了 `getUserMedia`。

---

## 7. 长期记忆系统（LTM）

一个「最小侵入式」的长期记忆引擎，夹在对话层和 Prompt 构建层之间，**任何一步失败都只是降级，不影响聊天**。

### 7.1 数据流

```
用户发言
   │
   ├─① 检索（同步、低延迟、纯关键词打分）
   │    single_conversation.py → build_retrieval_context()
   │    → batch_input.metadata["ltm_context"]
   │
   ├─② 注入（user 角色方括号块，放在用户文本之前）
   │    basic_memory_agent.py::_to_messages()
   │
   ├─③ 生成回复 → TTS → 推给前端
   │
   └─④ 抽取（异步 fire-and-forget，回复完成后才跑）
        single_conversation.py → asyncio.create_task(extract_from_turn())
        → LLM 抽取结构化记忆 → 去重/冲突处理 → 落 SQLite
```

### 7.2 记忆类型（8 类）

| 类型 | 含义 | 类型 | 含义 |
|---|---|---|---|
| `identity` | 身份信息 | `fact` | 客观事实（工作/学校/宠物） |
| `preference` | 偏好（食物/喜好） | `event` | 事件（预约/日程） |
| `habit` | 习惯（作息/行为） | `relationship` | 关系（称呼/纪念日） |
| `experience` | 经历 | `goal` | 目标/计划 |

关键词分类 13 类：`interest` / `skill` / `technology` / `person` / `place` / `organization` / `food` / `hobby` / `goal` / `project` / `event` / `preference` / `topic`

### 7.3 排序公式

```
总分 = 0.40×相关度 + 0.25×重要性 + 0.15×新鲜度 + 0.15×置信度 + 0.05×使用次数
```

命中阈值 `min_total_score = 0.30`，最多注入 5 条、最多 600 字符。

### 7.4 模块文件

```
open_llm_vtuber/long_term_memory/
├── __init__.py            # 模块入口：get_manager / build_retrieval_context / extract_from_turn / 配置读写
├── schemas.py             # 数据结构：MemoryRecord / KeywordRecord / UserState / 类型枚举
├── store.py               # SQLite 存储层（stdlib sqlite3，每 conf_uid 一个 .db）
├── manager.py             # 编排：检索→注入→抽取→摘要→用户状态
├── retriever.py           # 打分与检索、Prompt 块构建
├── extractor.py           # LLM 结构化抽取（含规则兜底）
├── keyword_extractor.py   # 关键词识别与分类
├── deduplicator.py        # 去重
├── privacy.py             # 隐私过滤（密钥/密码/银行卡等绝不入库）
├── prompt_builder.py      # 注入文本拼装
├── memory_config.json     # 模块独立配置（不受 config_sync 影响）
└── run_tests.py           # 单元测试
```

### 7.5 隐私保护

以下内容**永不写入记忆**：
- 密钥样式：`sk-…` / `ghp_…` / `xox…` / `AKIA…` / `AIza…`
- 敏感关键词：密码 / password / api key / token / secret / cookie / 银行卡 / 卡号 / 验证码

### 7.6 记忆面板

`https://10.1.1.10:12393/memory/` 可以：
- 切换角色（conf_uid）
- 浏览 / 搜索 / 新增 / 编辑 / 删除记忆
- 管理关键词（含分类）
- 查看与修改用户状态（情绪 / 精力 / 压力 / 话题 / 意图）
- 跑检索测试（`test-retrieve`，不聊天也能看命中效果）
- 「彻底清空」某角色的全部记忆（需输入 `DELETE` 确认）

### 7.7 端到端自测

```bash
cd /home/uu/桌面/Open-LLM-VTuber
/root/.local/bin/uv run python tools/ltm_e2e.py
```

覆盖 6 个验收场景：偏好记忆（火锅）、宠物事实（小白）、冲突更新（咖啡→不喝咖啡，旧记忆 deprecated 且保留历史）、关键词分类（Python→technology）、闲聊不入库（下雨）、昵称召回（小雪）。

---

## 8. 远程运维

工作区（Windows 侧）自带 paramiko 免密工具，无需 sshpass：

| 脚本 | 用法 | 说明 |
|---|---|---|
| `ssh_run.py` | `./sshagent/Scripts/python.exe ssh_run.py "<命令>" [超时]` | 执行远端命令。**只读 stdout、丢弃 stderr** |
| `ssh_put.py` | `./sshagent/Scripts/python.exe ssh_put.py <本地> <远端>` | SFTP 直传文件（远端父目录需先 `mkdir -p`） |

SSH 信息：`10.1.1.10:22`，`root` / `123`（见工作区 `1 ssh连接信息.md`）

**踩坑记录：**
- `ssh_run.py` 通过 `exec_command` 传超长 base64 会**静默失败** → 传文件请用 `ssh_put.py`
- `setsid … &` 后紧跟 `sleep`+检查的复合命令会挂住不返回 → 拉起命令单独发，检查另发一条
- GitHub / apt 限流时走代理：`http://10.1.1.1:7890`

---

## 9. 常见问题排查

### Q1：浏览器打不开 `https://10.1.1.10:12393/`

按顺序查：

```bash
# 1) TLS 代理在不在？
bash config/tlsctl.sh status          # 期望 running + 12393 监听
bash config/tlsctl.sh start           # 不在就启动

# 2) 后端在不在？
ss -tlnp | grep 12395

# 3) 本机自测
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1:12393/        # 200
curl -s  -o /dev/null -w '%{http_code}\n' http://127.0.0.1:12393/         # 301
```

### Q2：页面能开但一直「连接中」

WebSocket 没通。检查：
- 是否用了 `http://` 打开（被 301 跳 https，某些客户端不跟随）→ 手动改成 `https://`
- 浏览器控制台是否报 mixed-content（前端应自动用 `wss://`）
- 后端日志：`tail -f /tmp/ollvm.log`

### Q3：麦克风 / 屏幕共享没反应

必须是 `https://` 入口（安全上下文）。在 HTTP 下浏览器禁用这些 API。确认地址栏是 `https://` 且证书警告已通过。

### Q4：改了面板参数不生效

改的是 `conf.yaml`，**必须重启才生效**。点面板上的「重启服务」，或执行 `bash config/restart_server.sh`。

### Q5：SSH 断开后服务掉线

启动时没用 `setsid`。重新用 §4.2 的命令拉起。

### Q6：`uv: command not found`

`uv` 不在非交互 shell 的 PATH。用绝对路径 `/root/.local/bin/uv`。

### Q7：相机 / 屏幕图标是红的

说明当前 LLM 不支持视觉。换视觉模型后，重跑 `tools/set_vision_logo.py` 即恢复。

### Q8：AI 记不住我说的话

1. 检查 `long_term_memory/memory_config.json` 的 `enabled` 是否为 `true`
2. 到 `/memory/` 面板看该 conf_uid 下有没有记忆生成
3. 用 `/memory/` 的「检索测试」验证命中
4. 闲聊内容（如「今天下雨了」）按设计**不入库**，属正常
5. 抽取依赖 LLM，确认 LLM 正常（能正常聊天就说明正常）

---

## 10. 快速命令卡片

```bash
# ── 进入项目 ──
cd /home/uu/桌面/Open-LLM-VTuber

# ── 重启全部（后端 + TLS 代理）──
bash config/restart_server.sh

# ── 单看状态 ──
bash config/tlsctl.sh status
ss -tlnp | grep -E ':(12393|12395)'
ps aux | grep -E 'run_server|tls_proxy' | grep -v grep

# ── 日志 ──
tail -f /tmp/ollvm.log          # 后端
tail -f /tmp/tls_proxy.log      # TLS 代理

# ── 自测 ──
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1:12393/            # 200
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1:12393/config/     # 200
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1:12393/memory/     # 200

# ── 长期记忆自测 ──
/root/.local/bin/uv run python tools/ltm_e2e.py
```

**访问地址汇总**

| 入口 | 地址 |
|---|---|
| 聊天 | `https://10.1.1.10:12393/` |
| 配置面板 | `https://10.1.1.10:12393/config/` |
| 记忆面板 | `https://10.1.1.10:12393/memory/` |
