"""Privacy filter + prompt-injection defense for the memory system.

- privacy_filter: rejects content containing secrets (API keys, passwords,
  bank info, tokens...) — spec: never store sensitive data.
- sanitize_memory_text: strips user attempts to smuggle system-prompt-style
  instructions into memory content (anti-injection, spec section on
  prompt security). Memory content is always re-stated by the system, never
  replayed verbatim as instructions.
"""

import re
from typing import Dict, List, Any

_API_KEY_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"sk-[A-Za-z0-9]{8,}",
        r"ghp_[A-Za-z0-9]{10,}",
        r"gho_[A-Za-z0-9]{10,}",
        r"xox[bpars]-[A-Za-z0-9-]{10,}",
        r"AKIA[0-9A-Z]{16}",
        r"AIza[0-9A-Za-z_\-]{20,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    ]
]

_PRIVACY_KEYWORDS_ZH = [
    "密码", "口令", "验证码", "银行卡", "卡号", "身份证号",
    "支付密码", "私钥", "密钥",
]

_PRIVACY_KEYWORDS_EN = [
    "password", "passwd", "api key", "apikey", "api_key",
    "secret", "token", "cookie", "session id", "private key",
    "credit card", "bank account",
]

# instruction-smuggling markers inside user-derived memory text
_INJECTION_MARKERS = [
    "system prompt", "系统提示", "忽略之前", "ignore previous",
    "ignore all", "disregard", "you must now", "new instructions",
    "【系统】", "[system]", "<system>", "override", "开发者模式",
    "developer mode", "jailbreak", "DAN模式",
]


def privacy_check(text: str) -> Dict[str, Any]:
    """Return {"ok": bool, "reason": str}. ok=False means: do NOT store."""
    if not text:
        return {"ok": True, "reason": ""}
    low = text.lower()
    for rx in _API_KEY_PATTERNS:
        if rx.search(text):
            return {"ok": False, "reason": f"matches secret pattern {rx.pattern[:24]}"}
    for kw in _PRIVACY_KEYWORDS_ZH:
        if kw in text:
            return {"ok": False, "reason": f"contains privacy keyword '{kw}'"}
    for kw in _PRIVACY_KEYWORDS_EN:
        if kw in low:
            return {"ok": False, "reason": f"contains privacy keyword '{kw}'"}
    return {"ok": True, "reason": ""}


def sanitize_memory_text(text: str, max_len: int = 200) -> str:
    """Clean a memory candidate before storing.

    - strips bracketed pseudo-system markers and injection phrases
    - removes newlines (memories are single-line statements)
    - clamps length
    """
    if not text:
        return ""
    cleaned = text.replace("\n", " ").replace("\r", " ").strip()
    for marker in _INJECTION_MARKERS:
        if marker.lower() in cleaned.lower():
            # drop the marker itself, keep the rest
            cleaned = re.sub(re.escape(marker), "", cleaned, flags=re.IGNORECASE)
    # collapse whitespace
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rsplit(" ", 1)[0] if " " in cleaned[-20:] else cleaned[:max_len]
    return cleaned


def is_probably_injection(text: str) -> bool:
    """Heuristic: does this text try to give the AI system-level instructions
    rather than describe the user? Used to reject storing it as a memory."""
    if not text:
        return False
    low = text.lower()
    strong_markers = [
        "忽略之前", "ignore previous", "ignore all previous",
        "system prompt", "系统提示词", "你的指令", "你的设定",
        "you must now", "from now on you", "act as", "扮演",
        "开发者模式", "developer mode", "jailbreak",
    ]
    hits = sum(1 for m in strong_markers if m in low)
    return hits >= 1


def sanitize_for_prompt(block_text: str) -> str:
    """Final defense before injection into the chat prompt: strip any
    bracket-like control characters that could confuse message structure."""
    if not block_text:
        return ""
    cleaned = block_text.replace("```", "'''")
    return cleaned
