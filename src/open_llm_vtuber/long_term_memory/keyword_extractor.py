"""Deterministic keyword & entity extraction (no LLM required).

Phase-1 implementation: rule-based Chinese-aware extraction feeding both the
keyword table (retrieval boosters) and the LLM extractor prompt (candidate
hints). Categories follow the spec list. Pure functions, fully unit-testable.
"""

import re
from typing import Dict, List, Tuple

from .schemas import KEYWORD_CATEGORIES

# (category, pattern) — ordered; a span matches at most one category
_RULES: List[Tuple[str, str]] = [
    # technology: programming languages & frameworks
    ("technology", r"(?:Python|Java(?:Script)?|TypeScript|C\+\+|C#|Go|Rust|Swift|Kotlin|PHP|Ruby|Scala|R语言|Matlab|SQL|HTML|CSS|Vue|React|Angular|Svelte|Node\.?js|Django|Flask|FastAPI|Spring|Flutter|PyTorch|TensorFlow|Keras|Pandas|NumPy|LangChain|Linux|Docker|Kubernetes|Git)"),
    # skill
    ("skill", r"(?:网络安全|信息安全|渗透测试|逆向工程|数据分析|机器学习|深度学习|前端开发|后端开发|全栈开发|算法|运维|测试|UI设计|视频剪辑|摄影|写作|翻译|绘画|做饭|开车|游泳|健身)"),
    # interest
    ("interest", r"(?:喜欢|热爱|讨厌|超爱|最爱|很爱|特别喜欢|对.{1,8}感兴趣)"),
    # goal
    ("goal", r"(?:实习|考研|考公|出国|留学|毕业|找工作|跳槽|升职|加薪|减肥|目标|计划|准备|打算|想学|想成为|想要)"),
    # food
    ("food", r"(?:火锅|麻辣烫|烧烤|烤肉|奶茶|咖啡|茶|可乐|雪碧|啤酒|白酒|红酒|蛋糕|甜品|巧克力|冰淇淋|寿司|刺身|拉面|饺子|包子|馒头|米饭|面条|米粉|螺蛳粉|酸辣粉|川菜|粤菜|湘菜|鲁菜|江浙菜|西餐|日料|韩餐|披萨|汉堡|沙拉|素食|辣|不辣|微辣|特辣)"),
    # person: Chinese names are hard without NER; capture explicit markers
    ("person", r"(?:我爸|我妈|我爸|我妈|我哥|我姐|我弟|我妹|我对象|我女朋友|我男朋友|我老公|我老婆|我孩子|我儿子|我女儿|我老板|我同事|我老师|我同学|室友|朋友|闺蜜|哥们|上司|领导)"),
    # pet names like 小白 (also used for relationship memories)
    ("person", r"(?:小[白黑黄灰花橘狸布妞豆糖球儿笨呆萌糖可爱]{1}|[阿小老]\w{1,2})"),
    # place
    ("place", r"(?:北京|上海|广州|深圳|杭州|成都|重庆|武汉|西安|南京|苏州|天津|长沙|郑州|青岛|大连|厦门|合肥|昆明|贵阳|兰州|乌鲁木齐|拉萨|哈尔滨|沈阳|长春|石家庄|太原|南昌|福州|济南|南宁|海口|西宁|银川|呼和浩特|香港|澳门|台北|老家|公司|学校|宿舍|家里|图书馆|咖啡馆|健身房)"),
    # organization
    ("organization", r"(?:大学|学院|公司|集团|研究所|医院|银行|中学|小学|字节|腾讯|阿里|百度|华为|小米|网易|美团|京东|拼多多|抖音|快手|哔哩哔哩|B站|谷歌|微软|苹果|亚马逊)"),
    # hobby
    ("hobby", r"(?:游戏|打游戏|追剧|看电影|动漫|漫画|小说|听歌|唱歌|跳舞|弹琴|钢琴|吉他|画画|旅行|旅游|露营|爬山|跑步|瑜伽|篮球|足球|羽毛球|乒乓球|网球|游泳|滑雪|滑板|钓鱼|养猫|养狗|撸猫|吸猫|二次元|手办|盲盒|剧本杀|密室逃脱|桌游)"),
    # event
    ("event", r"(?:约会|见面|聚餐|团建|面试|考试|答辩|开会|出差|旅行计划|生日|纪念日|过年|放假|开学|毕业典礼|婚礼)"),
]

_COMPILED = [(cat, re.compile(pat, re.IGNORECASE)) for cat, pat in _RULES]

# topic fallback: interesting content words (2-4 char CJK nouns-ish)
# single-char function words: any chunk containing one of these is glue, not a topic
_FUNC_CHARS = set("的了是在我你他她它们个也就都和与及或被把让给跟比很挺超太嗯哦啊呀吧呢嘛啦哟说看想还要去过着来叫")

# whole-chunk stopwords (matched verbatim)
_TOPIC_STOPWORDS = set(
    """
    因为 所以 但是 可是 然后 不过 其实 真的 有点 比较 非常 特别 现在 今天
    明天 昨天 刚才 什么 怎么 为什么 这个 那个 这样 那样 一下 一直 一些
    可能 应该 必须 需要 进行 开始 结束 觉得 感觉 知道 认为 谢谢 请问
    可以 以后 记得
    """.split()
)


def extract_keywords(text: str, max_keywords: int = 12) -> List[Dict[str, str]]:
    """Extract [{keyword, category}] from user text (rule-based).

    Deduplicates and caps the count. The first matched category wins for
    overlapping spans (rule order = priority).
    """
    if not text or not text.strip():
        return []
    results: List[Dict[str, str]] = []
    seen_spans: List[Tuple[int, int]] = []
    seen_words = set()

    for cat, rx in _COMPILED:
        for m in rx.finditer(text):
            span = m.span()
            # skip if overlapping an already-claimed span
            if any(s < span[1] and span[0] < e for s, e in seen_spans):
                continue
            word = m.group(0).strip()
            if not word or word.lower() in seen_words:
                continue
            seen_spans.append(span)
            seen_words.add(word.lower())
            results.append({"keyword": word, "category": cat})
            if len(results) >= max_keywords:
                return results

    # topic fallback: pull 2-4 char CJK chunks not already captured
    for m in re.finditer(r"[\u4e00-\u9fff]{2,4}", text):
        word = m.group(0)
        if word in _TOPIC_STOPWORDS or any(word in r["keyword"] or r["keyword"] in word for r in results):
            continue
        # skip chunks that are mostly function-word glue (以后你可, 以叫我小...)
        if any(ch in _FUNC_CHARS for ch in word):
            continue
        if len(results) >= max_keywords:
            break
        results.append({"keyword": word, "category": "topic"})

    return results


def extract_search_terms(
    user_text: str, keywords: List[Dict[str, str]]
) -> List[str]:
    """Build retrieval search terms from the current user text.

    Combines rule keywords with generic CJK/latin word chunks; used by the
    retriever's keyword search over stored memories.
    """
    terms: List[str] = []
    for kw in keywords:
        if kw["keyword"] not in terms:
            terms.append(kw["keyword"])
    # add english words
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9+#.]{1,20}", user_text):
        w = m.group(0)
        if w.lower() not in ("the", "and", "for", "with", "you", "are", "was"):
            terms.append(w)
    return terms[:20]


def is_valid_category(category: str) -> bool:
    return category in KEYWORD_CATEGORIES
