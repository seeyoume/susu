"""服务器公告拉取（启动时自动调）"""
import json
import urllib.error
import urllib.request

import license_mgr as lm
import settings_mgr as sm


def fetch_new():
    """ 返回比本地 last_announce_id 新的公告列表（按时间倒序），不影响主流程 """
    server = lm.get_server_url()
    if not server:
        return []
    since = int(sm.get("last_announce_id", 0) or 0)
    url = server.rstrip("/") + f"/announce/latest?since={since}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError):
        return []
    if not data.get("ok"):
        return []
    return data.get("list") or []


def mark_read(announce_id):
    sm.set_value("last_announce_id", int(announce_id))
