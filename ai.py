"""DeepSeek AI 评论改写 + 笔记生成"""
import json
import re
import time
import urllib.request
from pathlib import Path

AI_CONFIG_FILE = Path(__file__).parent / "accounts" / "_ai.json"

# 服务器默认配置缓存（30 分钟刷一次，避免每次都打服务器）
_SERVER_CACHE = {"data": None, "ts": 0}
_SERVER_CACHE_TTL = 1800

DEFAULT_SYSTEM = """你是小红书评论助手，基于用户提供的"参考模板"和"笔记上下文"生成一条评论。
要求:
- 中文口语化、自然，像普通用户而非营销号
- 长度 10-25 字
- 最多 1 个 emoji（可省略）
- 紧扣笔记主题，可以提问或表达共鸣
- 不要"求链接""可以加吗""加微信"等营销话术
- 直接输出评论文本，不要任何前缀、引号或解释"""


def load_ai_config():
    if not AI_CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(AI_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_ai_config(cfg):
    AI_CONFIG_FILE.parent.mkdir(exist_ok=True)
    AI_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_server_default():
    """ 从服务器拉默认 AI 配置（30min 缓存） """
    now = time.time()
    if _SERVER_CACHE["data"] and (now - _SERVER_CACHE["ts"]) < _SERVER_CACHE_TTL:
        return _SERVER_CACHE["data"]
    try:
        import license_mgr as lm
        data = lm.fetch_default_ai()
        _SERVER_CACHE["data"] = data
        _SERVER_CACHE["ts"] = now
        return data
    except Exception:
        return None


def effective_config():
    """ 返回实际使用的 AI 配置：
        优先级 = 用户自配 > 服务器默认（管理员配置）
        返回 dict 或 None
    """
    user = load_ai_config()
    if user.get("api_key"):
        return user
    srv = _get_server_default()
    if srv and srv.get("api_key"):
        # 把服务器默认合并到用户配置上下文（保留 prompt 等用户设置）
        merged = dict(user) if user else {}
        merged.update({
            "api_key": srv["api_key"],
            "model": srv.get("model", "deepseek-chat"),
            "base_url": srv.get("base_url",
                                 "https://api.deepseek.com/v1/chat/completions"),
            "_is_server_default": True,
        })
        return merged
    return None


def is_enabled():
    cfg = effective_config()
    return bool(cfg and cfg.get("api_key"))


def current_source():
    """ 显示用：当前 AI key 来源 'user' / 'server' / 'none' """
    user = load_ai_config()
    if user.get("api_key"):
        return "user"
    if _get_server_default():
        return "server"
    return "none"


def _clean(text):
    text = text.strip()
    # 去引号
    for q in ('"', "'", "「", "」", "【", "】", "“", "”", "‘", "’"):
        text = text.strip(q)
    # 去前缀
    for prefix in ("评论：", "评论:", "回复：", "回复:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text


REPLY_SYSTEM = """你是小红书博主，需要回复评论区里的"意向客户"留言（这些用户在问"求链接""怎么买""可以加吗"等）。
要求:
- 简短自然（8-20 字）
- 引导客户私下联系，但绝不能写具体微信号/QQ号（平台会过滤）
- 可以说"私我""扣1 我私""主页有详情""dd 我"之类
- 友好亲切，不要硬营销
- 中文口语，最多 1 个 emoji
- 直接输出回复内容，不要任何前缀、引号、解释"""


def reply_intent(template, customer_comment, note_meta=None, timeout=30):
    """ 针对意向客户评论生成个性化回复 """
    cfg = effective_config() or {}
    api_key = cfg.get("api_key", "")
    if not api_key:
        raise RuntimeError("未配置 DeepSeek API Key（且服务器未下发默认 Key）")
    model = cfg.get("model", "deepseek-chat")
    base_url = cfg.get("base_url", "https://api.deepseek.com/v1/chat/completions")
    note_meta = note_meta or {}

    user_prompt = (
        f"客户在评论区的留言: 「{customer_comment}」\n"
        f"笔记标题: {note_meta.get('title','')}\n"
        f"参考话术模板: {template}\n"
        f"基于客户提问的具体内容，生成一条贴合语境的回复。"
    )
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": REPLY_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.9,
        "max_tokens": 80,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base_url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return _clean(data["choices"][0]["message"]["content"])


NOTE_GEN_SYSTEM = """你是一个真实的小红书用户，正在分享自己的亲身经历和感受。
生成规则（非常重要，违反就失败）:
- 标题: 15字以内，像朋友发微信那样随意，禁止用"揭秘/必看/绝了"这类营销词
- 正文: 口语化，有具体细节（时间/地点/感受），禁止分点列表，禁止"总结一下"式结尾
- emoji: 全文最多 2 个，且只能放在句子末尾或段落末尾，禁止每段都加
- 禁止出现的词: 宝子/集美/姐妹/友友/绝绝子/yyds/爆款/干货/攻略/必收藏/强烈推荐
- 标签: 4-6 个，混合大词和小众长尾词，不要每个都用"#"开头（输出时不加#）
- 语气: 有一点口语化的不完美（比如"然后就...""感觉挺..."）

只输出 JSON，格式:
{"title": "标题", "body": "正文", "tags": ["标签1", "标签2"]}"""

DEFAULT_NOTE_USER_TEMPLATE = """我在经历: {topic}
类型: {type_hint}
字数: {words_min}-{words_max} 字
标签数量: {tags_min}-{tags_max} 个
风格参考: {style}
输出 JSON。"""

HUMANIZE_SYSTEM = """你是一个中文文字润色助手。把 AI 风格的小红书笔记改成普通真实用户写的样子。
改写规则:
1. 去掉所有"✨🌟💫📍🎯🔥💡"这类固定套路 emoji，最多保留 1 个最自然的
2. 把"总结一下/最后想说/强烈推荐"等 AI 结尾语换成自然收尾
3. 把过于整齐的三段式打乱，加一些口语衔接词（"其实""不过""然后吧"）
4. 标题去掉感叹号和爆款套路词，改短一点
5. 保持原意，不要删减关键信息
6. 不要解释你改了什么，直接输出改好的 JSON

输出格式和输入一致（JSON）: {"title":"...","body":"...","tags":[...]}"""


def _parse_range(text, default_min, default_max):
    """Parse '150-300' or '150~300' or '150,300' style ranges."""
    if text is None:
        return default_min, default_max
    s = str(text).strip()
    if not s:
        return default_min, default_max
    m = re.findall(r"\\d+", s)
    if len(m) >= 2:
        a, b = int(m[0]), int(m[1])
        if a > b:
            a, b = b, a
        return a, b
    if len(m) == 1:
        v = int(m[0])
        return v, v
    return default_min, default_max


def generate_note(topic, style="", note_type="image", timeout=60):
    """ 用 DeepSeek 生成笔记内容
        topic:     用户给的主题/赛道（如"减脂餐"）
        style:     可选风格描述（如"温柔治愈风"）
        note_type: 'image' / 'video' / 'longtext'
    """
    cfg = effective_config() or {}
    api_key = cfg.get("api_key", "")
    if not api_key:
        raise RuntimeError("未配置 DeepSeek API Key（且服务器未下发默认 Key）")
    model = cfg.get("model", "deepseek-chat")
    base_url = cfg.get("base_url", "https://api.deepseek.com/v1/chat/completions")

    # Note generation prompt settings (persisted in accounts/_ai.json)
    note_system = cfg.get("note_gen_system", NOTE_GEN_SYSTEM)
    note_user_tpl = cfg.get("note_gen_user_template", DEFAULT_NOTE_USER_TEMPLATE)
    tags_min, tags_max = _parse_range(cfg.get("note_gen_tags", "5-8"), 5, 8)

    type_hint = {
        "image": "图文笔记（多图配文）",
        "video": "视频笔记的封面文案和描述",
        "longtext": "长文笔记（500-1500 字深度内容）",
    }.get(note_type, "图文笔记")

    if note_type == "longtext":
        words_min, words_max = _parse_range(cfg.get("note_gen_words_longtext", "900-1500"), 900, 1500)
    else:
        words_min, words_max = _parse_range(cfg.get("note_gen_words_image", "150-300"), 150, 300)

    # Placeholders: topic, type_hint, style, words_min, words_max, tags_min, tags_max
    user_prompt = note_user_tpl.format(
        topic=topic,
        type_hint=type_hint,
        style=style or "自然口语",
        words_min=words_min,
        words_max=words_max,
        tags_min=tags_min,
        tags_max=tags_max,
    )

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": note_system},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.95,
        "max_tokens": 1500 if note_type == "longtext" else 600,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base_url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["choices"][0]["message"]["content"].strip()
    # 去掉 ```json``` 包装
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        obj = json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m: raise RuntimeError(f"AI 返回不是 JSON: {text[:200]}")
        obj = json.loads(m.group(0))

    first_pass = {
        "title": str(obj.get("title", "")).strip()[:30],
        "body": str(obj.get("body", "")).strip(),
        "tags": [str(t).strip().lstrip("#") for t in (obj.get("tags") or [])][:8],
    }

    # 二次 humanize：把 AI 味道再洗一遍，降低 AIGC 检测命中率
    try:
        first_pass = _humanize(first_pass, cfg, model, base_url, api_key,
                               timeout=min(timeout, 30))
    except Exception:
        pass  # humanize 失败不影响主流程，返回第一次结果

    return first_pass


def _humanize(note_obj, cfg, model, base_url, api_key, timeout=30):
    """ 用 DeepSeek 对 AI 生成的笔记做去 AI 味改写 """
    humanize_system = cfg.get("humanize_system", HUMANIZE_SYSTEM)
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": humanize_system},
            {"role": "user", "content": json.dumps(note_obj, ensure_ascii=False)},
        ],
        "temperature": 1.0,
        "max_tokens": 800,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base_url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        obj = json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return note_obj  # 解析失败回退原版
        obj = json.loads(m.group(0))
    return {
        "title": str(obj.get("title", note_obj["title"])).strip()[:30],
        "body": str(obj.get("body", note_obj["body"])).strip(),
        "tags": [str(t).strip().lstrip("#")
                 for t in (obj.get("tags") or note_obj["tags"])][:8],
    }


def rewrite(template, note_meta=None, timeout=30):
    cfg = effective_config() or {}
    api_key = cfg.get("api_key", "")
    if not api_key:
        raise RuntimeError("未配置 DeepSeek API Key（且服务器未下发默认 Key）")
    model = cfg.get("model", "deepseek-chat")
    base_url = cfg.get("base_url", "https://api.deepseek.com/v1/chat/completions")
    system_prompt = cfg.get("system_prompt", DEFAULT_SYSTEM)
    note_meta = note_meta or {}

    user_prompt = (
        f"参考模板: {template}\n"
        f"笔记标题: {note_meta.get('title','')}\n"
        f"作者: {note_meta.get('author','')}\n"
        f"笔记类型: {note_meta.get('type','')}\n"
        f"请生成一条评论。"
    )
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.95,
        "max_tokens": 100,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base_url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["choices"][0]["message"]["content"]
    return _clean(text)
