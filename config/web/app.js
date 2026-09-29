/* ============================================================
   Config Panel — app.js
   zashboard-style conf.yaml editor mounted at /config/
   ============================================================ */

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

/* ---------------- taxonomy: which subtree renders as which page ---------------- */

const NAV_SCHEMA = [
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

const ICONS = {
  server: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>',
  user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
  cpu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/></svg>',
  mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>',
  audio: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>',
  filter: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>',
  activity: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
  radio: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49M7.76 16.24a6 6 0 0 1 0-8.49M19.07 4.93a10 10 0 0 1 0 14.14M4.93 19.07a10 10 0 0 1 0-14.14"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>',
};

/* ---------------- 中文备注词典 ----------------
   精确路径优先；未命中时按末段键名（leaf）回退，
   leaf 回退仅用于跨提供方语义一致的通用键（api_key / model / temperature 等）。 */

const ZH_NOTES = {
  // ---- system_config ----
  "system_config.conf_version": "配置文件版本号（勿手动修改）",
  "system_config.host": "服务监听地址：0.0.0.0 允许其他设备访问，127.0.0.1 仅本机",
  "system_config.port": "服务监听端口（浏览器访问的端口，改后需重启）",
  "system_config.config_alts_dir": "多角色配置文件夹（存放可切换的角色 yaml）",
  "system_config.tool_prompts.live2d_expression_prompt": "Live2D 表情控制提示词标识：自动把 emomap 关键词注入系统提示词，让 LLM 输出表情关键词",
  "system_config.tool_prompts.group_conversation_prompt": "群聊模式提示词标识：群聊时追加到每个 AI 参与者的记忆",
  "system_config.tool_prompts.mcp_prompt": "MCP 工具调用提示词标识（由 Agent 自动决定是否使用）",

  // ---- character_config ----
  "character_config.conf_name": "角色配置文件名（characters 目录下的 yaml）",
  "character_config.conf_uid": "角色配置唯一标识 ID",
  "character_config.live2d_model_name": "Live2D 模型名，必须与 model_dict.json 中的名称一致",
  "character_config.character_name": "角色显示名（群聊与界面展示用）",
  "character_config.avatar": "头像文件名（avatars 目录下，建议正方形；留空用角色名首字母）",
  "character_config.human_name": "用户（人类）显示名",
  "character_config.persona_prompt": "人设提示词：角色的性格、语气、背景设定",

  // ---- agent_config ----
  "character_config.agent_config.conversation_agent_choice": "当前启用的对话 Agent（须为 agent_settings 下的某个键名）",

  "character_config.agent_config.agent_settings.basic_memory_agent.llm_provider": "LLM 提供方（须为 llm_configs 下的某个键名）",
  "character_config.agent_config.agent_settings.basic_memory_agent.faster_first_response": "首句收到逗号即开始播报，降低首响延迟",
  "character_config.agent_config.agent_settings.basic_memory_agent.segment_method": "分句方法：regex（快）或 pysbd（更准）",
  "character_config.agent_config.agent_settings.basic_memory_agent.use_mcpp": "启用 MCP Plus 工具调用（OpenAI function calling 方式）",
  "character_config.agent_config.agent_settings.basic_memory_agent.mcp_enabled_servers": "启用的 MCP 服务器列表（逗号分隔）",

  "character_config.agent_config.agent_settings.letta_agent.host": "Letta 服务器地址",
  "character_config.agent_config.agent_settings.letta_agent.port": "Letta 服务器端口",
  "character_config.agent_config.agent_settings.letta_agent.id": "Letta 上运行的 Agent ID",
  "character_config.agent_config.agent_settings.letta_agent.faster_first_response": "首句收到逗号即开始播报，降低首响延迟",
  "character_config.agent_config.agent_settings.letta_agent.segment_method": "分句方法：regex 或 pysbd",

  "character_config.agent_config.agent_settings.hume_ai_agent.host": "Hume API 地址（一般无需修改）",
  "character_config.agent_config.agent_settings.hume_ai_agent.config_id": "可选的配置 ID",
  "character_config.agent_config.agent_settings.hume_ai_agent.idle_timeout": "空闲多少秒后断开连接",

  // llm_configs —— 通用键走 leaf 回退，这里只写特有项
  "character_config.agent_config.llm_configs.stateless_llm_with_template.template": "提示词模板（如 CHATML，用于不支持 ChatML 的模型）",
  "character_config.agent_config.llm_configs.openai_compatible_llm.interrupt_method": "打断信号注入方式：提供方支持任意位置插入 system 则用 system，否则用 user（一般不改）",
  "character_config.agent_config.llm_configs.ollama_llm.keep_alive": "模型闲置后在内存保留的秒数；-1 表示常驻内存（退出也不卸载）",
  "character_config.agent_config.llm_configs.ollama_llm.unload_at_exit": "退出程序时从内存卸载模型",
  "character_config.agent_config.llm_configs.llama_cpp_llm.model_path": "本地 GGUF 模型文件路径",

  // ---- asr_config ----
  "character_config.asr_config.asr_model": "语音识别引擎：faster_whisper / whisper_cpp / whisper / azure_asr / fun_asr / groq_whisper_asr / sherpa_onnx_asr",
  "character_config.asr_config.azure_asr.languages": "识别语言列表（如 en-US, zh-CN）",
  "character_config.asr_config.faster_whisper.model_path": "模型名称 / HF Hub ID / 本地路径",
  "character_config.asr_config.faster_whisper.download_root": "模型下载保存目录",
  "character_config.asr_config.faster_whisper.language": "识别语言（en / zh，留空自动检测）",
  "character_config.asr_config.faster_whisper.device": "cpu / cuda / auto（不支持 macOS mps）",
  "character_config.asr_config.faster_whisper.compute_type": "量化精度（如 int8，更省显存）",
  "character_config.asr_config.faster_whisper.prompt": "引导提示词，帮助模型理解上下文（可选）",
  "character_config.asr_config.whisper_cpp.model_name": "模型名（可用列表见 pywhispercpp 文档）",
  "character_config.asr_config.whisper_cpp.model_dir": "模型存放目录",
  "character_config.asr_config.whisper_cpp.print_realtime": "实时打印识别结果",
  "character_config.asr_config.whisper_cpp.print_progress": "打印进度",
  "character_config.asr_config.whisper_cpp.language": "识别语言（en / zh / auto）",
  "character_config.asr_config.whisper.name": "OpenAI Whisper 官方模型名（如 medium）",
  "character_config.asr_config.whisper.download_root": "模型下载保存目录",
  "character_config.asr_config.whisper.device": "运行设备：cpu / cuda",
  "character_config.asr_config.whisper.prompt": "引导提示词，帮助模型理解上下文（可选）",
  "character_config.asr_config.fun_asr.model_name": "FunASR 模型（iic/SenseVoiceSmall 或 paraformer-zh）",
  "character_config.asr_config.fun_asr.vad_model": "VAD 模型（音频超过 30 秒时必需）",
  "character_config.asr_config.fun_asr.punc_model": "标点恢复模型",
  "character_config.asr_config.fun_asr.device": "运行设备：cpu / cuda",
  "character_config.asr_config.fun_asr.disable_update": "启动时是否检查 FunASR 更新",
  "character_config.asr_config.fun_asr.ncpu": "CPU 运算线程数",
  "character_config.asr_config.fun_asr.hub": "模型下载源：ms（ModelScope，默认）或 hf（HuggingFace）",
  "character_config.asr_config.fun_asr.use_itn": "启用逆文本正则化（数字/日期转文字）",
  "character_config.asr_config.fun_asr.language": "识别语言（zh / en / auto）",
  "character_config.asr_config.sherpa_onnx_asr.model_type": "模型类型：transducer / paraformer / nemo_ctc / wenet_ctc / whisper / tdnn_ctc / sense_voice / fire_red_asr",
  "character_config.asr_config.sherpa_onnx_asr.sense_voice": "SenseVoice 模型文件路径（会自动下载）",
  "character_config.asr_config.sherpa_onnx_asr.tokens": "tokens.txt 路径（所有模型类型必需）",
  "character_config.asr_config.sherpa_onnx_asr.num_threads": "推理线程数",
  "character_config.asr_config.sherpa_onnx_asr.use_itn": "SenseVoice 启用逆文本正则化；非 SenseVoice 模型应设为 false",
  "character_config.asr_config.sherpa_onnx_asr.provider": "推理后端：cpu 或 cuda（cuda 需额外配置）",
  "character_config.asr_config.groq_whisper_asr.model": "Groq Whisper 模型（whisper-large-v3-turbo 或 whisper-large-v3）",
  "character_config.asr_config.groq_whisper_asr.lang": "识别语言，留空自动检测",

  // ---- tts_config ----
  "character_config.tts_config.tts_model": "语音合成引擎：edge_tts / azure_tts / piper_tts / sherpa_onnx_tts / openai_tts / minimax_tts / elevenlabs_tts 等",
  "character_config.tts_config.azure_tts.pitch": "音调调整百分比",
  "character_config.tts_config.azure_tts.rate": "语速（1 为正常）",
  "character_config.tts_config.bark_tts.voice": "Bark 音色（如 v2/en_speaker_1）",
  "character_config.tts_config.edge_tts.voice": "Edge TTS 音色；命令 edge-tts --list-voices 查看全部（如 zh-CN-XiaoxiaoNeural）",
  "character_config.tts_config.piper_tts.model_path": "Piper 模型文件路径（.onnx）",
  "character_config.tts_config.piper_tts.speaker_id": "说话人 ID（多说话人模型用，单说话人保持 0）",
  "character_config.tts_config.piper_tts.length_scale": "语速：0.5 = 2 倍速，1.0 正常，2.0 半速",
  "character_config.tts_config.piper_tts.noise_scale": "音频变化度 0–1，越高越丰富（推荐 0.667）",
  "character_config.tts_config.piper_tts.noise_w": "说话风格变化度 0–1（推荐 0.8）",
  "character_config.tts_config.piper_tts.volume": "音量 0.0–1.0（1.0 正常）",
  "character_config.tts_config.piper_tts.normalize_audio": "音频归一化（推荐开启，音量更一致）",
  "character_config.tts_config.piper_tts.use_cuda": "GPU 加速（需安装 onnxruntime-gpu）",
  "character_config.tts_config.cosyvoice_tts.client_url": "CosyVoice gradio webui 地址",
  "character_config.tts_config.cosyvoice_tts.mode_checkbox_group": "推理模式（预训练音色 / 3s极速复刻等）",
  "character_config.tts_config.cosyvoice_tts.sft_dropdown": "预训练音色下拉框值（如 中文女）",
  "character_config.tts_config.cosyvoice_tts.prompt_text": "参考音频对应的提示文本",
  "character_config.tts_config.cosyvoice_tts.prompt_wav_upload_url": "参考音频 URL（复刻模式用）",
  "character_config.tts_config.cosyvoice_tts.prompt_wav_record_url": "录制的参考音频 URL",
  "character_config.tts_config.cosyvoice_tts.instruct_text": "指令文本",
  "character_config.tts_config.cosyvoice_tts.api_name": "gradio 接口名（/generate_audio）",
  "character_config.tts_config.cosyvoice2_tts.client_url": "CosyVoice2 gradio webui 地址",
  "character_config.tts_config.cosyvoice2_tts.stream": "流式合成",
  "character_config.tts_config.cosyvoice2_tts.api_name": "gradio 接口名（/generate_audio）",
  "character_config.tts_config.melo_tts.speaker": "说话人（EN-Default / ZH）",
  "character_config.tts_config.melo_tts.language": "语言（EN / ZH）",
  "character_config.tts_config.melo_tts.device": "auto / cpu / cuda / cuda:0 / mps",
  "character_config.tts_config.melo_tts.speed": "语速（1.0 为正常）",
  "character_config.tts_config.x_tts.api_url": "XTTS 服务接口地址",
  "character_config.tts_config.x_tts.speaker_wav": "说话人参考音频（如 female）",
  "character_config.tts_config.x_tts.language": "合成语言",
  "character_config.tts_config.gpt_sovits_tts.api_url": "GPT-SoVITS API 地址",
  "character_config.tts_config.gpt_sovits_tts.text_lang": "合成文本语言",
  "character_config.tts_config.gpt_sovits_tts.ref_audio_path": "参考音频路径（留空用项目根目录默认音频）",
  "character_config.tts_config.gpt_sovits_tts.prompt_lang": "参考音频的提示语言",
  "character_config.tts_config.gpt_sovits_tts.text_split_method": "文本切分方法（如 cut5）",
  "character_config.tts_config.gpt_sovits_tts.batch_size": "批处理大小",
  "character_config.tts_config.gpt_sovits_tts.media_type": "输出音频格式（wav 等）",
  "character_config.tts_config.gpt_sovits_tts.streaming_mode": "流式模式（字符串 'true' / 'false'）",
  "character_config.tts_config.fish_api_tts.reference_id": "音色参考 ID（在 fish.audio 网站获取）",
  "character_config.tts_config.fish_api_tts.latency": "balanced 更快但音质略低，normal 音质优先",
  "character_config.tts_config.fish_api_tts.base_url": "Fish Audio API 地址",
  "character_config.tts_config.coqui_tts.model_name": "Coqui 模型名，命令 tts --list_models 查看全部",
  "character_config.tts_config.coqui_tts.speaker_wav": "说话人参考音频（多说话人模型用）",
  "character_config.tts_config.coqui_tts.language": "合成语言",
  "character_config.tts_config.siliconflow_tts.api_url": "硅基流动 TTS 接口地址",
  "character_config.tts_config.siliconflow_tts.default_model": "默认模型（如 FunAudioLLM/CosyVoice2-0.5B）",
  "character_config.tts_config.siliconflow_tts.default_voice": "音色，格式 speech:模型名:音色ID:音色名",
  "character_config.tts_config.siliconflow_tts.sample_rate": "采样率：opus 48000；wav/pcm 8000–44100；mp3 32000/44100",
  "character_config.tts_config.siliconflow_tts.response_format": "音频格式：mp3 / opus / wav / pcm",
  "character_config.tts_config.siliconflow_tts.stream": "流式返回",
  "character_config.tts_config.siliconflow_tts.gain": "增益（dB）",
  "character_config.tts_config.sherpa_onnx_tts.vits_model": "VITS 模型文件路径（.onnx）",
  "character_config.tts_config.sherpa_onnx_tts.vits_lexicon": "词典文件路径（可选）",
  "character_config.tts_config.sherpa_onnx_tts.vits_tokens": "tokens 文件路径",
  "character_config.tts_config.sherpa_onnx_tts.vits_data_dir": "espeak-ng 数据目录（英文模型用，可选）",
  "character_config.tts_config.sherpa_onnx_tts.vits_dict_dir": "结巴词典目录（中文用，可选）",
  "character_config.tts_config.sherpa_onnx_tts.tts_rule_fsts": "数字/日期等规则 FST 文件（逗号分隔，可选）",
  "character_config.tts_config.sherpa_onnx_tts.max_num_sentences": "每批最大句数（-1 为全部）",
  "character_config.tts_config.sherpa_onnx_tts.sid": "说话人 ID（多说话人模型用）",
  "character_config.tts_config.sherpa_onnx_tts.provider": "cpu / cuda / coreml（Apple）",
  "character_config.tts_config.sherpa_onnx_tts.num_threads": "推理线程数",
  "character_config.tts_config.sherpa_onnx_tts.speed": "语速（1.0 为正常）",
  "character_config.tts_config.sherpa_onnx_tts.debug": "调试模式",
  "character_config.tts_config.spark_tts.api_url": "Spark-TTS gradio 服务地址",
  "character_config.tts_config.spark_tts.api_name": "接口名：voice_clone（声音克隆）或 voice_creation（声音创造）",
  "character_config.tts_config.spark_tts.prompt_wav_upload": "参考音频 URL（voice_clone 模式用）",
  "character_config.tts_config.spark_tts.gender": "声音性别（voice_creation 模式用）",
  "character_config.tts_config.spark_tts.pitch": "音高偏移（半音），范围 1–5，默认 3",
  "character_config.tts_config.spark_tts.speed": "语速，范围 1–5，默认 3",
  "character_config.tts_config.openai_tts.model": "服务端期望的模型名（如 tts-1、kokoro）",
  "character_config.tts_config.openai_tts.voice": "音色名（如 alloy、af_sky+af_bella）",
  "character_config.tts_config.openai_tts.base_url": "TTS 服务地址（本地服务一般无需密钥）",
  "character_config.tts_config.openai_tts.file_extension": "音频格式（mp3 或 wav）",
  "character_config.tts_config.minimax_tts.group_id": "MiniMax 用户组 ID",
  "character_config.tts_config.minimax_tts.model": "模型：speech-02-hd 或 speech-02-turbo（推荐 turbo）",
  "character_config.tts_config.minimax_tts.voice_id": "音色 ID（默认 female-shaonv 少女音）",
  "character_config.tts_config.minimax_tts.pronunciation_dict": "自定义发音词典（JSON 字符串，默认空）",
  "character_config.tts_config.elevenlabs_tts.voice_id": "ElevenLabs 音色 ID",
  "character_config.tts_config.elevenlabs_tts.model_id": "模型 ID（如 eleven_multilingual_v2）",
  "character_config.tts_config.elevenlabs_tts.output_format": "输出格式（如 mp3_44100_128）",
  "character_config.tts_config.elevenlabs_tts.stability": "音色稳定性 0.0–1.0",
  "character_config.tts_config.elevenlabs_tts.similarity_boost": "音色相似度 0.0–1.0",
  "character_config.tts_config.elevenlabs_tts.style": "风格夸张度 0.0–1.0",
  "character_config.tts_config.elevenlabs_tts.use_speaker_boost": "启用说话人增强，提升音质",
  "character_config.tts_config.cartesia_tts.model_id": "模型 ID（如 sonic-3）",
  "character_config.tts_config.cartesia_tts.output_format": "输出格式（如 wav）",
  "character_config.tts_config.cartesia_tts.emotion": "情感引导（如 neutral）",
  "character_config.tts_config.cartesia_tts.volume": "音量 0.5–2.0",
  "character_config.tts_config.cartesia_tts.speed": "语速 0.6–1.5",
  "character_config.tts_config.cartesia_tts.language": "输出语言（如 en）",

  // ---- vad_config ----
  "character_config.vad_config.vad_model": "VAD 模型选择（留空使用 silero）",
  "character_config.vad_config.silero_vad.orig_sr": "输入音频原始采样率",
  "character_config.vad_config.silero_vad.target_sr": "目标采样率",
  "character_config.vad_config.silero_vad.prob_threshold": "语音概率阈值：越低越灵敏（易误触发），越高越迟钝",
  "character_config.vad_config.silero_vad.db_threshold": "音量阈值（dB）",
  "character_config.vad_config.silero_vad.required_hits": "连续命中多少帧判定为「开始说话」",
  "character_config.vad_config.silero_vad.required_misses": "连续缺失多少帧判定为「说话结束」",
  "character_config.vad_config.silero_vad.smoothing_window": "平滑窗口大小（帧）",

  // ---- tts_preprocessor_config ----
  "character_config.tts_preprocessor_config.remove_special_char": "朗读前移除表情符号等特殊字符",
  "character_config.tts_preprocessor_config.ignore_brackets": "忽略中括号 [ ] /【】内的内容",
  "character_config.tts_preprocessor_config.ignore_parentheses": "忽略圆括号 ( ) 内的内容（如内心戏、动作）",
  "character_config.tts_preprocessor_config.ignore_asterisks": "忽略星号 * * 包裹的内容（如动作描写）",
  "character_config.tts_preprocessor_config.ignore_angle_brackets": "忽略尖括号 < > 包裹的内容",
  "character_config.tts_preprocessor_config.translator_config.translate_audio": "启用语音翻译（需先部署 DeepLX，否则会崩溃）",
  "character_config.tts_preprocessor_config.translator_config.translate_provider": "翻译服务：deeplx 或 tencent",
  "character_config.tts_preprocessor_config.translator_config.deeplx.deeplx_target_lang": "DeepLX 目标语言（如 JA）",
  "character_config.tts_preprocessor_config.translator_config.deeplx.deeplx_api_endpoint": "DeepLX 接口地址",
  "character_config.tts_preprocessor_config.translator_config.tencent.secret_id": "腾讯云 SecretId（每月免费 500 万字符）",
  "character_config.tts_preprocessor_config.translator_config.tencent.secret_key": "腾讯云 SecretKey（控制台「访问管理」获取）",
  "character_config.tts_preprocessor_config.translator_config.tencent.region": "服务区域（如 ap-guangzhou）",
  "character_config.tts_preprocessor_config.translator_config.tencent.source_lang": "源语言",
  "character_config.tts_preprocessor_config.translator_config.tencent.target_lang": "目标语言",

  // ---- live_config ----
  "live_config.bilibili_live.room_ids": "要监听的 B 站直播间房间号列表",
  "live_config.bilibili_live.sessdata": "B 站登录 Cookie（可选，用于带身份的请求）",

  // ---- 补充：cosyvoice2_tts ----
  "character_config.tts_config.cosyvoice2_tts.mode_checkbox_group": "推理模式：预训练音色（sft）/ 零样本复刻（zero_shot）/ 跨语种（cross_lingual）/ 指令控制（instruct）",
  "character_config.tts_config.cosyvoice2_tts.sft_dropdown": "预训练音色选择（仅 sft 模式生效）",
  "character_config.tts_config.cosyvoice2_tts.prompt_wav_upload_url": "参考音频上传接口地址（gradio webui 提供）",
  "character_config.tts_config.cosyvoice2_tts.prompt_wav_record_url": "参考音频录制接口地址（gradio webui 提供）",
  "character_config.tts_config.cosyvoice2_tts.instruct_text": "指令文本：如「用四川话说」「开心地说」（仅 instruct 模式生效）",

  // ---- 补充：其它 ----
  "system_config.tool_prompts.proactive_speak_prompt": "主动搭话提示词标识：空闲一段时间后让角色主动发起话题",
  "character_config.agent_config.llm_configs.stateless_llm_with_template.interrupt_method": "打断方式：cancel（取消请求）/ hear（听到即打断，默认更灵敏）",
  "character_config.agent_config.llm_configs.llama_cpp_llm.verbose": "输出详细日志（调试用，正式运行建议关闭）",
};

/* 末段键名回退：跨提供方含义一致的通用键 */
const ZH_LEAF_NOTES = {
  api_key: "API 密钥",
  base_url: "API 基础地址（通常以 /v1 结尾）",
  llm_api_key: "LLM API 密钥",
  organization_id: "组织 ID（可选）",
  project_id: "项目 ID（可选）",
  model: "模型名称",
  temperature: "采样温度：0–2，越高越随机（DeepSeek 限 0–1）",
  language: "语言设置",
  device: "运行设备：cpu / cuda / auto",
  region: "服务区域",
  voice: "音色名称",
  seed: "随机种子（0 为随机）",
  speed: "语速（1.0 为正常）",
  prompt: "提示词 / 引导文本",
  prompt_text: "提示文本",
  api_url: "API 服务地址",
};

/* 卡片（配置分区）级中文说明 */
const ZH_CARD_NOTES = {
  "system_config.tool_prompts": "工具类提示词：命中的标识会拼接进系统提示词",
  "character_config.agent_config.agent_settings.basic_memory_agent": "基础记忆 Agent：自带对话历史，当前主力方案",
  "character_config.agent_config.agent_settings.letta_agent": "Letta Agent：LLM 在 Letta 服务端配置，需自行部署",
  "character_config.agent_config.agent_settings.hume_ai_agent": "Hume AI 情感语音 Agent",
  "character_config.agent_config.llm_configs.stateless_llm_with_template": "带模板的无状态 LLM（不支持 ChatML 的模型用）",
  "character_config.agent_config.llm_configs.openai_compatible_llm": "OpenAI 兼容接口：火山方舟 / DeepSeek / OneAPI / vLLM 等均可",
  "character_config.agent_config.llm_configs.claude_llm": "Anthropic Claude 官方 API",
  "character_config.agent_config.llm_configs.llama_cpp_llm": "llama.cpp 本地推理（GGUF 模型）",
  "character_config.agent_config.llm_configs.ollama_llm": "Ollama 本地推理",
  "character_config.agent_config.llm_configs.lmstudio_llm": "LM Studio 本地推理",
  "character_config.agent_config.llm_configs.openai_llm": "OpenAI 官方 API",
  "character_config.agent_config.llm_configs.gemini_llm": "Google Gemini API",
  "character_config.agent_config.llm_configs.zhipu_llm": "智谱 AI API（glm-4-flash 免费）",
  "character_config.agent_config.llm_configs.deepseek_llm": "DeepSeek 官方 API",
  "character_config.agent_config.llm_configs.mistral_llm": "Mistral API",
  "character_config.agent_config.llm_configs.groq_llm": "Groq API（推理速度快）",
  "character_config.asr_config.azure_asr": "Azure 语音识别（需订阅）",
  "character_config.asr_config.faster_whisper": "Faster-Whisper 本地识别（推荐，支持 CUDA）",
  "character_config.asr_config.whisper_cpp": "whisper.cpp 本地识别（轻量）",
  "character_config.asr_config.whisper": "OpenAI Whisper 官方实现",
  "character_config.asr_config.fun_asr": "阿里 FunASR（SenseVoice 中文效果好，启动需联网）",
  "character_config.asr_config.sherpa_onnx_asr": "sherpa-onnx 本地识别（完全离线）",
  "character_config.asr_config.groq_whisper_asr": "Groq 云端 Whisper（速度快）",
  "character_config.tts_config.azure_tts": "Azure 语音合成（需订阅）",
  "character_config.tts_config.bark_tts": "Bark 本地合成",
  "character_config.tts_config.edge_tts": "微软 Edge TTS（免费、无需密钥，当前使用）",
  "character_config.tts_config.piper_tts": "Piper 本地合成（轻量离线）",
  "character_config.tts_config.cosyvoice_tts": "CosyVoice（连接 gradio webui）",
  "character_config.tts_config.cosyvoice2_tts": "CosyVoice2（连接 gradio webui，支持 3s 复刻）",
  "character_config.tts_config.melo_tts": "MeloTTS 本地合成",
  "character_config.tts_config.x_tts": "XTTS v2（连接本地服务）",
  "character_config.tts_config.gpt_sovits_tts": "GPT-SoVITS（连接本地服务，支持声音克隆）",
  "character_config.tts_config.fish_api_tts": "Fish Audio 云端合成（支持声音克隆）",
  "character_config.tts_config.coqui_tts": "Coqui TTS 本地合成",
  "character_config.tts_config.siliconflow_tts": "硅基流动云端合成（CosyVoice2）",
  "character_config.tts_config.sherpa_onnx_tts": "sherpa-onnx 本地合成（完全离线）",
  "character_config.tts_config.spark_tts": "Spark-TTS（连接 gradio 服务）",
  "character_config.tts_config.openai_tts": "OpenAI 兼容 TTS 端点（kokoro 等）",
  "character_config.tts_config.minimax_tts": "MiniMax 云端合成",
  "character_config.tts_config.elevenlabs_tts": "ElevenLabs 云端合成（音质好，收费）",
  "character_config.tts_config.cartesia_tts": "Cartesia 云端合成",
  "character_config.vad_config.silero_vad": "Silero VAD 参数（当前使用）",
  "character_config.tts_preprocessor_config.translator_config": "语音翻译：识别语言与合成语言不同时使用",
  "character_config.tts_preprocessor_config.translator_config.deeplx": "DeepLX 自建翻译服务",
  "character_config.tts_preprocessor_config.translator_config.tencent": "腾讯云机器翻译（每月免费 500 万字符，记得关闭后付费）",
  "live_config.bilibili_live": "B 站直播间弹幕接入",
};

function zhNote(path) {
  if (Object.prototype.hasOwnProperty.call(ZH_NOTES, path)) return ZH_NOTES[path];
  const leaf = path.split(".").slice(-1)[0];
  return ZH_LEAF_NOTES[leaf] || "";
}

/* ---------------- state ---------------- */

let configTree = null;
let comments = {};
let rawYaml = "";
let backupsList = [];
let currentPage = "system";
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

/* ---------------- navigation ---------------- */

function buildNav() {
  const nav = $("#nav");
  nav.innerHTML = "";
  NAV_SCHEMA.forEach((page) => {
    const item = document.createElement("div");
    item.className = `nav-item${page.id === currentPage ? " active" : ""}`;
    item.dataset.page = page.id;
    item.innerHTML = `
      <span class="nav-icon">${ICONS[page.icon]}</span>
      <span class="nav-label">${esc(page.label)}</span>`;
    item.addEventListener("click", () => {
      currentPage = page.id;
      searchQuery = "";
      $("#search").value = "";
      render();
    });
    nav.appendChild(item);
  });
}

/* ---------------- rendering ---------------- */

function valueType(v) {
  if (v === null || v === undefined) return "null";
  if (Array.isArray(v)) return "list";
  if (typeof v === "boolean") return "bool";
  if (typeof v === "number") return Number.isInteger(v) ? "int" : "float";
  return "str";
}

function renderRow(path, key, value, desc, depth) {
  const t = valueType(value);
  const row = document.createElement("div");
  row.className = "form-row";
  row.dataset.rowPath = path;

  const labelHtml = `${esc(key)} <span class="type-badge ${t}">${t}</span><br/><code>${esc(path)}</code>`;
  const descHtml = desc ? `<div class="row-desc">${esc(desc)}</div>` : "";

  let control = "";
  if (t === "bool") {
    const checked = (dirtyPaths.has(path) ? dirtyPaths.get(path) : value) ? "checked" : "";
    control = `
      <label class="switch">
        <input type="checkbox" data-path="${esc(path)}" ${checked}/>
        <span class="track"><span class="thumb"></span></span>
      </label>`;
  } else if (t === "null") {
    control = `<input class="input mono" type="text" data-path="${esc(path)}" data-empty="1" value="" placeholder="(空)"/>`;
  } else if (t === "int" || t === "float") {
    const v = dirtyPaths.has(path) ? dirtyPaths.get(path) : value;
    control = `<input class="input" type="number" step="${t === "float" ? "0.1" : "1"}" data-path="${esc(path)}" value="${esc(String(v))}"/>`;
  } else if (t === "list") {
    const v = dirtyPaths.has(path) ? dirtyPaths.get(path) : value;
    control = `<input class="input mono" type="text" data-path="${esc(path)}" data-list="1" value="${esc((Array.isArray(v) ? v : []).join(", "))}" placeholder="逗号分隔"/>`;
  } else {
    const v = dirtyPaths.has(path) ? dirtyPaths.get(path) : value;
    const longText = String(v).length > 60 || String(v).includes("\n");
    if (longText) {
      control = `<textarea class="input mono" data-path="${esc(path)}" rows="4">${esc(String(v))}</textarea>`;
    } else {
      control = `<input class="input mono" type="text" data-path="${esc(path)}" value="${esc(String(v))}"/>`;
    }
  }

  row.innerHTML = `
    <div class="row-info">
      <div class="row-label">${labelHtml}</div>
      ${descHtml}
    </div>
    <div class="row-control">${control}</div>`;
  return row;
}

function collectRows(rootPath, skipKeys = []) {
  const node = get(configTree, rootPath);
  if (node === undefined) return [];

  const rows = [];
  const walk = (subtree, prefix, depth) => {
    if (!subtree || typeof subtree !== "object") return;
    Object.entries(subtree).forEach(([key, value]) => {
      const path = prefix ? `${prefix}.${key}` : key;
      if (depth === 1 && skipKeys.includes(key)) return;
      if (value && typeof value === "object" && !Array.isArray(value)) {
        walk(value, path, depth + 1);
      } else if (Array.isArray(value) && value.some((i) => i && typeof i === "object")) {
        walk(value, path, depth + 1);
      } else {
        rows.push({ path, key, value, desc: zhNote(path) || comments[path] || "", depth });
      }
    });
  };
  walk(node, rootPath, 1);
  return rows;
}

/* group rows into cards by their parent path (siblings share a card) */

function groupIntoCards(rows, rootPath) {
  const cards = new Map();
  const rootDepth = rootPath.split(".").length;
  for (const r of rows) {
    const parts = r.path.split(".");
    // card key = parent path (all segments except the leaf key)
    const parentSegs = parts.slice(0, -1);
    const cardKey = parentSegs.join(".");
    if (!cards.has(cardKey)) cards.set(cardKey, []);
    cards.get(cardKey).push(r);
  }
  return cards;
}

function renderPage() {
  const page = NAV_SCHEMA.find((p) => p.id === currentPage);
  const content = $("#content");
  $("#page-title").textContent = page.label;
  $("#page-desc").textContent = page.desc;

  let rows = collectRows(page.root, page.skip || []);
  if (searchQuery) {
    const q = searchQuery.toLowerCase();
    rows = rows.filter(
      (r) =>
        r.path.toLowerCase().includes(q) ||
        String(r.value).toLowerCase().includes(q) ||
        (r.desc || "").toLowerCase().includes(q)
    );
  }

  if (!rows.length) {
    content.innerHTML = `<div class="empty-state">${
      searchQuery ? "没有匹配的配置项" : "该分区没有配置项"
    }</div>`;
    return;
  }

  const grid = document.createElement("div");
  grid.className = "grid";

  const cards = groupIntoCards(rows, page.root);
  cards.forEach((cardRows, cardKey) => {
    const card = document.createElement("div");
    card.className = "card";
    const title = cardKey === page.root ? page.label : cardKey.split(".").slice(-1)[0];
    const desc = ZH_CARD_NOTES[cardKey] || comments[cardKey] || "";
    card.innerHTML = `
      <div class="card-header">
        <h3><span class="dot"></span>${esc(title)}</h3>
        <span class="card-hint">${cardRows.length} 项</span>
      </div>
      ${desc ? `<div class="card-desc">${esc(desc)}</div>` : ""}
      <div class="card-body"></div>`;
    const body = card.querySelector(".card-body");
    cardRows.forEach((r) => body.appendChild(renderRow(r.path, r.key, r.value, r.desc, r.depth)));
    grid.appendChild(card);
  });

  content.innerHTML = "";
  content.appendChild(grid);
}

function render() {
  buildNav();
  renderPage();
  bindInputs();
  updateSaveBtn();
}

/* ---------------- dirty tracking ---------------- */

function markDirty(path, value) {
  dirtyPaths.set(path, value);
  updateSaveBtn();
}

function updateSaveBtn() {
  const btn = $("#btn-save");
  const n = dirtyPaths.size;
  if (n > 0) {
    btn.innerHTML = `保存 <span class="badge" style="background:#2563eb;color:#fff;border:none;">${n}</span>`;
    btn.disabled = false;
  } else {
    btn.innerHTML = `保存`;
    btn.disabled = false;
  }
}

function bindInputs() {
  document.querySelectorAll("[data-path]").forEach((el) => {
    const path = el.dataset.path;
    if (el.type === "checkbox") {
      el.addEventListener("change", () => markDirty(path, el.checked));
    } else {
      el.addEventListener("input", () => {
        el.classList.add("modified");
        if (el.dataset.list) {
          markDirty(path, el.value.split(",").map((s) => s.trim()).filter(Boolean));
        } else if (el.dataset.empty) {
          markDirty(path, el.value === "" ? null : el.value);
        } else {
          markDirty(path, el.value);
        }
      });
    }
  });
}

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
      renderPage();
      bindInputs();
    }, 200);
  });
}

/* ---------------- init ---------------- */

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
