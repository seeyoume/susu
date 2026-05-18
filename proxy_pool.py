"""绮绮采集器 - 代理池可视化管理
功能：
- 持久化 IP 池（accounts/_proxy_pool.json）
- 健康检查（多线程 ping + 真实 HTTP 探测）
- 给账号自动绑定可用 IP
- 失效 IP 自动剔除
"""
import json
import socket
import time
import threading
import urllib.request
import urllib.error
from pathlib import Path

POOL_FILE = Path(__file__).parent / "accounts" / "_proxy_pool.json"
PROBE_URL = "https://www.xiaohongshu.com/"  # 探测目标
PROBE_TIMEOUT = 8

_file_lock = threading.Lock()  # 保护 POOL_FILE 的并发读写


def _load_unsafe():
    """不加锁的读取，仅在已持有 _file_lock 时调用。"""
    if not POOL_FILE.exists():
        return {"proxies": [], "binding": {}}
    try:
        d = json.loads(POOL_FILE.read_text(encoding="utf-8"))
        d.setdefault("proxies", [])
        d.setdefault("binding", {})
        return d
    except Exception:
        return {"proxies": [], "binding": {}}


def _save_unsafe(data):
    """不加锁的写入，仅在已持有 _file_lock 时调用。
    写临时文件再 rename，防止写到一半崩溃导致文件损坏。"""
    POOL_FILE.parent.mkdir(exist_ok=True)
    tmp = POOL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(POOL_FILE)


def _load():
    with _file_lock:
        return _load_unsafe()


def _save(data):
    with _file_lock:
        _save_unsafe(data)


def list_proxies():
    return _load()["proxies"]


def list_bindings():
    return _load()["binding"]


def add_proxies(lines):
    """ 批量添加；lines 是 ['http://host:port', 'host:port', ...] 列表
        返回 (added, dup) """
    with _file_lock:
        data = _load_unsafe()
        existing = {p["url"] for p in data["proxies"]}
        added = dup = 0
        for ln in lines:
            u = ln.strip()
            if not u or u.startswith("#"):
                continue
            if not u.startswith(("http://", "https://", "socks5://")):
                u = "http://" + u
            if u in existing:
                dup += 1
                continue
            data["proxies"].append({
                "url": u,
                "status": "未测",
                "latency_ms": 0,
                "last_check": "",
                "fail_count": 0,
                "bound_to": "",
            })
            existing.add(u)
            added += 1
        _save_unsafe(data)
    return added, dup


def remove_proxy(url):
    with _file_lock:
        data = _load_unsafe()
        data["proxies"] = [p for p in data["proxies"] if p["url"] != url]
        data["binding"] = {acc: u for acc, u in data["binding"].items() if u != url}
        _save_unsafe(data)


def clear_dead():
    """ 清除所有 status='失效' 或 fail_count >= 3 """
    with _file_lock:
        data = _load_unsafe()
        keep = [p for p in data["proxies"]
                if p["status"] != "失效" and p.get("fail_count", 0) < 3]
        removed = len(data["proxies"]) - len(keep)
        data["proxies"] = keep
        valid_urls = {p["url"] for p in keep}
        data["binding"] = {acc: u for acc, u in data["binding"].items()
                           if u in valid_urls}
        _save_unsafe(data)
    return removed


def _parse(url):
    """ 'http://user:pwd@host:port' -> (host, port, has_auth) """
    from urllib.parse import urlparse
    u = urlparse(url)
    return u.hostname, u.port or 80, bool(u.username)


def check_one(proxy_dict, timeout=PROBE_TIMEOUT):
    """ 单 proxy 健康检测：TCP + HTTP 探测 xiaohongshu.com """
    url = proxy_dict["url"]
    try:
        host, port, _ = _parse(url)
    except Exception:
        proxy_dict["status"] = "格式错"; proxy_dict["latency_ms"] = 0
        proxy_dict["fail_count"] = proxy_dict.get("fail_count", 0) + 1
        return False
    t0 = time.time()
    # 1) TCP 连接
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
    except Exception:
        proxy_dict["status"] = "失效"
        proxy_dict["latency_ms"] = 0
        proxy_dict["fail_count"] = proxy_dict.get("fail_count", 0) + 1
        proxy_dict["last_check"] = time.strftime("%Y-%m-%d %H:%M")
        return False
    # 2) HTTP 探测
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": url, "https": url}))
        req = urllib.request.Request(
            PROBE_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with opener.open(req, timeout=timeout) as r:
            ms = int((time.time() - t0) * 1000)
            if r.status < 500:
                proxy_dict["status"] = "可用"
                proxy_dict["latency_ms"] = ms
                proxy_dict["fail_count"] = 0
                proxy_dict["last_check"] = time.strftime("%Y-%m-%d %H:%M")
                return True
    except Exception as e:
        proxy_dict["status"] = "HTTP 失败"
        proxy_dict["latency_ms"] = int((time.time() - t0) * 1000)
        proxy_dict["fail_count"] = proxy_dict.get("fail_count", 0) + 1
        proxy_dict["last_check"] = time.strftime("%Y-%m-%d %H:%M")
        return False
    return False


def check_all(progress_cb=None, workers=10):
    """ 并发检测所有代理，progress_cb(done, total) """
    data = _load()
    proxies = data["proxies"]
    total = len(proxies)
    if total == 0:
        return 0, 0
    done = [0]
    lock = threading.Lock()
    ok_count = [0]

    def worker(p):
        ok = check_one(p)
        with lock:
            done[0] += 1
            if ok:
                ok_count[0] += 1
            if progress_cb:
                progress_cb(done[0], total)

    threads = []
    for i, p in enumerate(proxies):
        t = threading.Thread(target=worker, args=(p,), daemon=True)
        t.start()
        threads.append(t)
        if len(threads) >= workers:
            for t in threads: t.join()
            threads = []
    for t in threads: t.join()
    _save(data)
    return ok_count[0], total


def bind(account, proxy_url):
    """ 给账号绑定一个代理 """
    data = _load()
    data["binding"][account] = proxy_url
    for p in data["proxies"]:
        if p["url"] == proxy_url:
            p["bound_to"] = account
    _save(data)


def unbind(account):
    data = _load()
    data["binding"].pop(account, None)
    for p in data["proxies"]:
        if p.get("bound_to") == account:
            p["bound_to"] = ""
    _save(data)


def auto_bind(accounts):
    """ 给账号列表自动分配可用代理（每个账号一个独立 IP） """
    data = _load()
    available = [p for p in data["proxies"]
                 if p["status"] == "可用" and not p.get("bound_to")]
    n = 0
    for acc in accounts:
        if acc in data["binding"]:
            continue
        if not available:
            break
        p = available.pop(0)
        data["binding"][acc] = p["url"]
        p["bound_to"] = acc
        n += 1
    _save(data)
    return n


def get_proxy_for_account(account):
    """ 优先返回池中绑定的代理，找不到则返回 '' """
    data = _load()
    return data["binding"].get(account, "")
