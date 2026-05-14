"""评论意图分析 - 从评论里挖潜客信号"""
import re

# 高意向关键词分组（长词）
INTENT_KEYWORDS = {
    "购买意向": [
        "求链接", "求购", "求同款", "怎么买", "在哪买", "哪里买",
        "怎么定", "多少钱", "价格", "购买方式", "求安利", "求种草",
        "求小黄车", "求店铺", "店名", "链接呢", "求渠道", "求买", "想买",
    ],
    "联系意向": [
        "求私", "私我", "私聊", "可以加吗", "加个微信", "加vx", "加wx",
        "微信号", "联系方式", "怎么联系", "求私信", "互关", "求加好友",
        "扣1", "扣求", "蹲一个", "扣求", "+1", "+一",
    ],
    "教程意向": [
        "求教程", "求方法", "怎么做", "求步骤", "教教我", "求详细",
        "怎么操作", "求经验", "怎么学", "怎么搞", "怎么写", "求指导",
    ],
    "推荐意向": [
        "求推荐", "求介绍", "蹲推荐", "求清单", "求安利",
    ],
}

# 反向过滤（广告/营销号自己发的，不算潜客）
NEGATIVE_KEYWORDS = ["私聊我", "私我有", "我这里", "代理", "招商", "加盟",
                     "找我", "出"]

# 短词意向：评论很短（≤8字）且匹配下列前缀视为意向
SHORT_INTENT_PREFIXES = [
    "求", "蹲", "想要", "需要", "+1", "+一", "扣1", "+1+1",
    "在哪", "怎么", "如何", "想",
]


def _all_keywords():
    return [(k, group) for group, kws in INTENT_KEYWORDS.items() for k in kws]


_KW_LIST = _all_keywords()


def _check_short_intent(content):
    """ 短评论意向识别: 评论简短且以求/蹲/+1等开头 """
    # 去除表情、空格、标点
    s = re.sub(r"[\s\W_]+", "", content)
    if not s or len(s) > 8:
        return None
    # 原始内容也短
    if len(content.strip()) > 12:
        return None
    for p in SHORT_INTENT_PREFIXES:
        if s.startswith(p) or content.strip() == p:
            return f"短意向:{p}"
    return None


def extract_intent_users(comments, note_meta=None):
    """ 从评论列表里筛出包含意向词的，返回潜客列表 """
    out = []
    seen_users = set()
    for c in comments:
        content = c.get("content", "") or ""
        if not content:
            continue
        if any(neg in content for neg in NEGATIVE_KEYWORDS):
            continue
        hits = [(kw, group) for kw, group in _KW_LIST if kw in content]
        short_intent = _check_short_intent(content)
        if not hits and not short_intent:
            continue
        uid = c.get("user_id", "")
        if uid and uid in seen_users:
            continue
        seen_users.add(uid)
        groups = sorted(set(g for _, g in hits))
        kws = sorted(set(k for k, _ in hits))
        if short_intent:
            kws.append(short_intent)
            if not groups:
                groups = ["购买意向"]
        row = {
            "user_nickname": c.get("user_nickname", ""),
            "user_id": uid,
            "user_url": f"https://www.xiaohongshu.com/user/profile/{uid}" if uid else "",
            "intent_type": "|".join(groups),
            "matched_keywords": ",".join(kws),
            "comment": content,
            "comment_like": c.get("like_count", ""),
            "ip_location": c.get("ip_location", ""),
            "create_time": c.get("create_time", ""),
            "comment_id": c.get("comment_id", ""),
        }
        if note_meta:
            row["note_id"] = note_meta.get("note_id", "")
            row["note_title"] = note_meta.get("title", "")
            row["note_url"] = note_meta.get("url", "")
        out.append(row)
    return out


def parse_xhs_count(v):
    """ '1.2万' / '3000+' / 1234 → int """
    if isinstance(v, int):
        return v
    s = str(v or "0").strip().replace("+", "").replace(",", "")
    if not s:
        return 0
    if s.endswith("万"):
        try:
            return int(float(s[:-1]) * 10000)
        except ValueError:
            return 0
    try:
        return int(float(s))
    except ValueError:
        return 0
