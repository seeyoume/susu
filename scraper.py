"""小红书核心 - Playwright + 系统 Edge"""
import json
import random
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

import xhs_sign
import fingerprint as _fp

BASE_DIR = Path(__file__).parent
ACCOUNTS_DIR = BASE_DIR / "accounts"
ACCOUNTS_DIR.mkdir(exist_ok=True)
CONFIG_FILE = ACCOUNTS_DIR / "_config.json"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# 旧版状态文件迁移
_OLD = BASE_DIR / "xhs_state.json"
if _OLD.exists() and not (ACCOUNTS_DIR / "default.json").exists():
    try:
        shutil.move(str(_OLD), str(ACCOUNTS_DIR / "default.json"))
    except Exception:
        pass


def list_accounts():
    return sorted([p.stem for p in ACCOUNTS_DIR.glob("*.json")
                   if not p.stem.startswith("_")]) or ["default"]


def load_config():
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_proxy(account):
    return (load_config().get(account) or {}).get("proxy") or ""


def get_account_meta(account):
    """返回账号配置 dict（代理、昵称、user_id 等）"""
    return load_config().get(account) or {}


def set_proxy(account, proxy):
    cfg = load_config()
    cfg.setdefault(account, {})["proxy"] = (proxy or "").strip()
    save_config(cfg)


# ---------------- 代理池 ----------------
_POOL_LOCK = threading.Lock()
_POOL_ASSIGNED = set()
_POOL_CACHE = {"url": None, "ips": [], "ts": 0}


def fetch_proxy_pool(api_url, timeout=15, use_cache=True):
    """ 调用代理提取 API（如青果 share.proxy.qg.net/get），返回 ['ip:port', ...] """
    import urllib.request
    now = time.time()
    # 5 秒内同一 URL 复用上次结果，避免短时间多次拉触发限频
    if use_cache and _POOL_CACHE["url"] == api_url and now - _POOL_CACHE["ts"] < 5:
        return list(_POOL_CACHE["ips"])
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        raise RuntimeError(f"请求代理 API 失败: {e}")
    parts = re.split(r"[\s,]+", body.strip())
    ips = [p for p in parts if re.match(r"^\d{1,3}(\.\d{1,3}){3}:\d+$", p)]
    if not ips:
        raise RuntimeError(f"API 返回无有效 IP（前200字）: {body[:200]}")
    _POOL_CACHE.update({"url": api_url, "ips": ips, "ts": now})
    return ips


def acquire_pool_ip(api_url):
    """ 从代理池拿一个未被占用的 IP """
    ips = fetch_proxy_pool(api_url)
    with _POOL_LOCK:
        free = [i for i in ips if i not in _POOL_ASSIGNED]
        chosen = free[0] if free else (ips[0] if ips else None)
        if chosen:
            _POOL_ASSIGNED.add(chosen)
        return chosen


def release_pool_ip(ip):
    if not ip:
        return
    with _POOL_LOCK:
        _POOL_ASSIGNED.discard(ip)


def parse_proxy(url):
    """ 'http://u:p@host:8080' -> playwright proxy dict 或 None """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if "://" not in url:
        url = "http://" + url
    u = urlparse(url)
    if not u.hostname or not u.port:
        raise ValueError(f"代理格式错误（缺 host/port）: {url}")
    if u.scheme not in ("http", "https", "socks5"):
        raise ValueError(f"不支持的代理协议: {u.scheme}")
    p = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        p["username"] = u.username
    if u.password:
        p["password"] = u.password
    return p


class StoppedException(Exception):
    pass


class XHSScraper:
    def __init__(self, log_cb=None, headless=False, account="default", proxy=None):
        self.log = log_cb or (lambda m: print(m))
        self.headless = headless
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self._api_buffer = []
        self.stop_event = threading.Event()
        self.current_account = account
        self.proxy_raw = proxy or ""
        self.proxy = None
        self.assigned_ip = None
        self.user_nickname = ""
        self.user_id = ""
        meta = get_account_meta(account)
        if meta:
            self.user_nickname = meta.get("nickname", "") or ""
            self.user_id = meta.get("user_id", "") or ""
        if proxy:
            try:
                self.proxy = self._resolve_proxy(proxy)
            except Exception as e:
                self.log(f"⚠ 代理无效，将不使用代理: {e}")

    def _resolve_proxy(self, proxy_str):
        """ 支持两种格式：
            - 'http://user:pass@host:port'  固定代理
            - 'api:<URL>'                   每次启动从 URL 抽一个 IP
        """
        s = (proxy_str or "").strip()
        if not s:
            return None
        if s.startswith("api:"):
            api_url = s[4:].strip()
            self.log(f"从代理池抽 IP: {api_url[:70]}...")
            ip = acquire_pool_ip(api_url)
            if not ip:
                raise RuntimeError("代理池为空")
            self.assigned_ip = ip
            self.log(f"✓ 分配到 IP: {ip}")
            return parse_proxy(f"http://{ip}")
        return parse_proxy(s)

    def _dump_debug(self, tag="debug"):
        """Dump a screenshot + html + key DOM hints to output/ for fast iteration."""
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            base = OUTPUT_DIR / f"{tag}_{self.current_account}_{ts}"
            if self.page:
                try:
                    self.page.screenshot(path=str(base) + ".png", full_page=True)
                except Exception:
                    pass
                try:
                    html = self.page.content()
                    (Path(str(base) + ".html")).write_text(html, encoding="utf-8")
                except Exception:
                    pass
                try:
                    hints = self.page.evaluate("""() => {
                        const visible = (el) => {
                            if (!el || el.offsetParent === null) return false;
                            const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                        };
                        const pick = (el) => {
                            const attr = (k) => (el.getAttribute ? (el.getAttribute(k) || '') : '');
                            return {
                                tag: (el.tagName || '').toLowerCase(),
                                text: ((el.textContent || '').trim() || '').slice(0, 60),
                                placeholder: (el.placeholder || '').slice(0, 60),
                                dp: (attr('data-placeholder') || attr('aria-label') || attr('placeholder')).slice(0, 60),
                                id: (el.id || '').slice(0, 60),
                                cls: (String(el.className || '')).slice(0, 120),
                            };
                        };
                        const out = {url: location.href, title: document.title, titleCandidates: [], editorCandidates: [], buttons: []};

                        for (const el of document.querySelectorAll('input, [contenteditable=\"true\"], textarea, div, p, span')) {
                            if (!visible(el)) continue;
                            const ph = (el.placeholder || '');
                            const txt = (el.textContent || '').trim();
                            const dp = (el.getAttribute && (el.getAttribute('data-placeholder') || el.getAttribute('aria-label') || el.getAttribute('placeholder'))) || '';
                            if (ph.includes('标题') || txt.includes('输入标题') || String(dp).includes('输入标题') || (txt.includes('标题') && txt.length <= 10)) {
                                out.titleCandidates.push(pick(el));
                                if (out.titleCandidates.length >= 30) break;
                            }
                        }
                        for (const el of document.querySelectorAll('[contenteditable=\"true\"], textarea')) {
                            if (!visible(el)) continue;
                            out.editorCandidates.push(pick(el));
                            if (out.editorCandidates.length >= 30) break;
                        }
                        for (const el of document.querySelectorAll('button, a, div, span')) {
                            if (!visible(el)) continue;
                            const t = (el.textContent || '').trim();
                            if (!t) continue;
                            if (t.includes('发布') || t.includes('排版') || t.includes('暂存') || t.includes('新的创作')) {
                                out.buttons.push(pick(el));
                                if (out.buttons.length >= 40) break;
                            }
                        }
                        return out;
                    }""")
                    (Path(str(base) + ".json")).write_text(
                        json.dumps(hints, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            self.log(f"  ⚠ 已保存调试文件: {base}.png/.html/.json")
        except Exception:
            pass

    def _state_path(self):
        return ACCOUNTS_DIR / f"{self.current_account}.json"

    def start(self):
        self.pw = sync_playwright().start()
        launch_kwargs = {"headless": self.headless}
        if self.proxy:
            launch_kwargs["proxy"] = self.proxy
            self.log(f"使用代理: {self.proxy['server']}")
        last_err = None
        for kw in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
            try:
                self.browser = self.pw.chromium.launch(**launch_kwargs, **kw)
                break
            except Exception as e:
                last_err = e
                continue
        if not self.browser:
            # 失败也要把 playwright 释放，避免残留 node 进程
            try: self.pw.stop()
            except Exception: pass
            raise RuntimeError(
                f"无可用浏览器：请安装 Microsoft Edge 或 Chrome (last_err={last_err})"
            )
        self._open_context()
        # 预热 DNS / TCP / TLS：首次访问 xhs 经常 timeout，先打一个轻量请求把链路捂热
        try:
            self._safe_goto("https://www.xiaohongshu.com/explore",
                            retries=2, timeout=30000, wait_until="commit",
                            silent=True)
        except Exception as e:
            self.log(f"⚠ 预热访问失败（可忽略）: {e}")

    def _open_context(self):
        sp = self._state_path()
        storage = str(sp) if sp.exists() else None
        # 加载该账号的独立指纹（每个账号稳定，账号间不同 → 防关联）
        fp = _fp.get_fingerprint(self.current_account)
        ua = fp["user_agent"]

        # 根据 UA 构造与之匹配的 sec-ch-ua 系列请求头
        # UA 和 client hints 不一致是现代反爬的强信号
        import re as _re
        _m = _re.search(r'Chrome/(\d+)', ua)
        ch_ver = _m.group(1) if _m else "131"
        is_edge = "Edg/" in ua
        if is_edge:
            sec_ch_ua = (f'"Microsoft Edge";v="{ch_ver}", '
                         f'"Chromium";v="{ch_ver}", "Not-A.Brand";v="99"')
        else:
            sec_ch_ua = (f'"Google Chrome";v="{ch_ver}", '
                         f'"Chromium";v="{ch_ver}", "Not-A.Brand";v="99"')

        ch_platform = '"macOS"' if "Macintosh" in ua else '"Windows"'

        self.context = self.browser.new_context(
            storage_state=storage,
            user_agent=ua,
            viewport=fp["viewport"],
            locale=fp.get("locale", "zh-CN"),
            timezone_id=fp.get("timezone", "Asia/Shanghai"),
            screen=fp.get("screen", {"width": fp["viewport"]["width"],
                                      "height": fp["viewport"]["height"]}),
            extra_http_headers={
                "sec-ch-ua": sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": ch_platform,
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "none",
                "upgrade-insecure-requests": "1",
            },
        )
        # 注入指纹覆盖脚本
        try:
            self.context.add_init_script(_fp.make_init_script(fp))
        except Exception:
            pass
        self.page = self.context.new_page()
        self.page.on("response", self._on_response)
        self.page.on("request", self._on_request)
        self._api_buffer.clear()
        self.captured_requests = []  # 抓包记录

    # ---------------- 控制 ----------------
    def request_stop(self): self.stop_event.set()
    def clear_stop(self): self.stop_event.clear()

    def is_alive(self):
        try:
            return bool(self.browser and self.browser.is_connected())
        except Exception:
            return False

    def _safe_goto(self, url, retries=2, timeout=30000,
                    wait_until="domcontentloaded", silent=False):
        """ 包一层带重试的 page.goto。
            第一次冷启动 / 冷连接经常超时，重试一次大概率就好。
            wait_until='commit' 比 'domcontentloaded' 更早返回，
            适合首屏预热；正常导航用 'domcontentloaded'。
        """
        last_err = None
        for attempt in range(retries + 1):
            try:
                self._check_stop()
            except Exception:
                raise
            try:
                self.page.goto(url, wait_until=wait_until, timeout=timeout)
                # 软等一下 DOM，超时不报错
                if wait_until == "commit":
                    try:
                        self.page.wait_for_load_state("domcontentloaded",
                                                     timeout=8000)
                    except Exception:
                        pass
                return True
            except Exception as e:
                last_err = e
                if attempt < retries:
                    if not silent:
                        self.log(f"⚠ 打开 {url[:70]} 失败({attempt+1}/{retries+1}): "
                                 f"{str(e)[:80]}，重试中...")
                    try:
                        # 部分网络栈错误下 reload 比 goto 更稳
                        time.sleep(1.0 + attempt * 1.0)
                    except Exception:
                        pass
                    continue
        # 重试用尽
        if not silent:
            self.log(f"✗ 打开 {url[:70]} 最终失败: {last_err}")
        raise last_err

    def _check_stop(self):
        if self.stop_event.is_set():
            raise StoppedException("已停止")

    def _sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self._check_stop()
            time.sleep(min(0.3, end - time.time()))

    # ── 反检测：人类行为模拟 ─────────────────────────────────────
    def _human_move(self, tx, ty):
        """贝塞尔曲线鼠标移动 — 非直线，带加速/减速"""
        import math
        cx = getattr(self, '_mx', tx + random.randint(-200, 200))
        cy = getattr(self, '_my', ty + random.randint(-200, 200))
        dist = math.hypot(tx - cx, ty - cy)
        steps = max(8, min(40, int(dist / 18)))
        # 随机控制点（产生弧线）
        cpx = (cx + tx) / 2 + random.uniform(-dist * 0.4, dist * 0.4)
        cpy = (cy + ty) / 2 + random.uniform(-dist * 0.4, dist * 0.4)
        for i in range(steps + 1):
            t = i / steps
            # ease-in-out: 在 t 附近加速中间减速
            t_ease = t * t * (3 - 2 * t)
            bx = (1 - t_ease)**2 * cx + 2 * (1 - t_ease) * t_ease * cpx + t_ease**2 * tx
            by = (1 - t_ease)**2 * cy + 2 * (1 - t_ease) * t_ease * cpy + t_ease**2 * ty
            self.page.mouse.move(bx, by)
            time.sleep(random.uniform(0.008, 0.022))
        self._mx, self._my = tx, ty

    def _human_click(self, x, y, btn="left"):
        """移动到目标（贝塞尔）后点击"""
        self._human_move(x + random.uniform(-3, 3), y + random.uniform(-3, 3))
        self._sleep(random.uniform(0.05, 0.18))
        self.page.mouse.click(x, y, button=btn)
        self._mx, self._my = x, y

    def _human_type(self, text, base_delay_ms=80):
        """模拟人类打字：变速、偶尔停顿、极低概率误触+退格"""
        for i, ch in enumerate(text):
            # 偶尔停顿（思考/看屏幕）
            if random.random() < 0.05:
                self._sleep(random.uniform(0.3, 1.2))
            # 极低概率误触一个字符再退格
            if random.random() < 0.015 and ch.isascii():
                wrong = random.choice('qwertasdfgzxcvb')
                self.page.keyboard.type(wrong, delay=random.randint(40, 100))
                self._sleep(random.uniform(0.08, 0.25))
                self.page.keyboard.press("Backspace")
                self._sleep(random.uniform(0.05, 0.15))
            delay = int(base_delay_ms * random.uniform(0.4, 2.2))
            self.page.keyboard.type(ch, delay=delay)
        # 短暂停顿表示打字结束
        self._sleep(random.uniform(0.2, 0.5))

    def _on_request(self, req):
        """ 抓取关键写操作的 POST 请求（评论/点赞/关注），用于反向调试 """
        try:
            url = req.url
            if "/api/sns/web/" not in url:
                return
            if req.method != "POST":
                return
            # 只关注写操作
            interest = any(k in url.lower() for k in
                           ("/comment/", "/like", "/follow", "/dislike", "/unfollow"))
            if not interest:
                return
            body = req.post_data or ""
            headers = dict(req.headers)
            # 把关键头单独标出
            sig_headers = {k: v for k, v in headers.items()
                           if k.lower().startswith("x-") or k.lower() in
                           ("authorization", "cookie", "content-type", "referer", "origin")}
            entry = {
                "ts": time.strftime("%H:%M:%S"),
                "method": req.method,
                "url": url,
                "body": body,
                "key_headers": sig_headers,
                "all_header_keys": list(headers.keys()),
            }
            self.captured_requests.append(entry)
            # 只留最近 50 条
            if len(self.captured_requests) > 50:
                self.captured_requests = self.captured_requests[-50:]
            self.log(f"📡 抓到 {req.method} {url[-60:]}")
        except Exception:
            pass

    def _on_response(self, resp):
        url = resp.url
        if "/api/sns/web/" not in url:
            return
        try:
            if "json" not in resp.headers.get("content-type", ""):
                return
            data = resp.json()
            self._api_buffer.append({"url": url, "data": data})
            # 顺手识别当前登录用户
            if "/user/me" in url or "/user/otherinfo" in url:
                self._extract_user_info(data)
        except Exception:
            pass

    def _extract_user_info(self, data):
        try:
            d = data.get("data") or {}
            nick = d.get("nickname") or d.get("nick_name") or ""
            uid = d.get("user_id") or d.get("red_id") or d.get("id") or ""
            changed = False
            if nick and nick != self.user_nickname:
                self.user_nickname = nick; changed = True
            if uid and uid != self.user_id:
                self.user_id = uid; changed = True
            if changed:
                self._save_user_meta()
                self.log(f"识别到账号: {self.user_nickname} ({self.user_id})")
        except Exception:
            pass

    def _save_user_meta(self):
        try:
            cfg = load_config()
            cfg.setdefault(self.current_account, {})
            if self.user_nickname:
                cfg[self.current_account]["nickname"] = self.user_nickname
            if self.user_id:
                cfg[self.current_account]["user_id"] = self.user_id
            save_config(cfg)
        except Exception:
            pass

    def refresh_user_info(self, timeout=6):
        """ 访问 explore 主动触发 /user/me 接口，捕获昵称 """
        try:
            self._safe_goto("https://www.xiaohongshu.com/explore",
                            retries=2, timeout=30000, silent=True)
        except Exception:
            return
        end = time.time() + timeout
        while time.time() < end:
            if self.user_nickname:
                return
            time.sleep(0.3)

    def _drain(self, key):
        hit = [x for x in self._api_buffer if key in x["url"]]
        self._api_buffer = [x for x in self._api_buffer if key not in x["url"]]
        return hit

    def save_state(self):
        try:
            self.context.storage_state(path=str(self._state_path()))
            self.log(f"[{self.current_account}] 状态已保存")
        except Exception as e:
            self.log(f"保存失败: {e}")

    def is_logged_in(self):
        cookies = self.context.cookies() if self.context else []
        return any(c["name"] == "web_session" for c in cookies)

    def login_wait(self, timeout=180):
        try:
            self._safe_goto("https://www.xiaohongshu.com/explore",
                            retries=2, timeout=30000)
        except Exception as e:
            self.log(f"打开主页失败（已重试 2 次）: {e}")
            return False
        time.sleep(1)

        if self.is_logged_in():
            self.log(f"✓ [{self.current_account}] 已是登录状态，跳过扫码")
            self.refresh_user_info()
            self.save_state()
            return True

        self.log(f"[{self.current_account}]：请在浏览器内扫码登录...")
        start = time.time()
        while time.time() - start < timeout:
            self._check_stop()
            if self.is_logged_in():
                self.log("✓ 登录成功")
                try:
                    self.page.reload(wait_until="domcontentloaded", timeout=15000)
                    time.sleep(1)
                except Exception:
                    pass
                self.refresh_user_info()
                # reload 之后再保存：此时 cookies 已被服务端刷新成完整版（更长 TTL）
                self.save_state()
                return True
            time.sleep(2)
        self.log("✗ 登录超时（180 秒）")
        return False

    # ---------------- 搜索 ----------------
    def search_notes(self, keyword, max_count=20):
        self.log(f"搜索: {keyword}  目标 {max_count} 条")
        self._api_buffer.clear()
        url = f"https://www.xiaohongshu.com/search_result?keyword={keyword}&source=web_explore_feed"
        self.page.goto(url, wait_until="domcontentloaded")
        self._sleep(3)

        collected, last, stale = {}, 0, 0
        while len(collected) < max_count and stale < 5:
            self._check_stop()
            self.page.mouse.wheel(0, 3000)
            self._sleep(2)
            for item in self._drain("search/notes"):
                d = item["data"].get("data") or {}
                for it in d.get("items", []) or []:
                    nid = it.get("id")
                    if not nid: continue
                    nc = it.get("note_card") or {}
                    user = nc.get("user") or {}
                    info = nc.get("interact_info") or {}
                    xsec = it.get("xsec_token", "")
                    # IP 属地：搜索结果里通常没有，先尝试几个可能字段（snake / camel）
                    ip_loc = (nc.get("ip_location") or nc.get("ipLocation")
                              or user.get("ip_location") or user.get("ipLocation")
                              or it.get("ip_location") or it.get("ipLocation") or "")
                    collected[nid] = {
                        "note_id": nid,
                        "title": nc.get("display_title", ""),
                        "type": nc.get("type", ""),
                        "author": user.get("nickname", ""),
                        "author_id": user.get("user_id", ""),
                        "liked_count": info.get("liked_count", ""),
                        "collected_count": info.get("collected_count", ""),
                        "comment_count": info.get("comment_count", ""),
                        "share_count": info.get("share_count", ""),
                        "ip_location": ip_loc,
                        "cover": (nc.get("cover") or {}).get("url_default", ""),
                        "url": f"https://www.xiaohongshu.com/explore/{nid}?xsec_token={xsec}&xsec_source=pc_search",
                    }
            self.log(f"  当前 {len(collected)}")
            if len(collected) == last: stale += 1
            else: stale, last = 0, len(collected)

        result = list(collected.values())[:max_count]
        self.log(f"搜索完成 共 {len(result)}")
        return result

    # ---------------- 笔记详情 + 评论 ----------------
    def fetch_note_detail(self, url_or_id, want_comments=True, max_comments=100):
        if url_or_id.startswith("http"):
            url = url_or_id
            m = re.search(r"/(?:explore|discovery/item)/([0-9a-zA-Z]+)", url)
            note_id = m.group(1) if m else url
        else:
            note_id = url_or_id.strip()
            url = f"https://www.xiaohongshu.com/explore/{note_id}"

        self.log(f"采集笔记: {note_id}")
        self._api_buffer.clear()
        self.page.goto(url, wait_until="domcontentloaded")
        self._sleep(4)

        # 诊断：记录缓冲中所有 API 响应 URL
        buf_urls = [x["url"] for x in self._api_buffer]
        self.log(f"  [dbg] buffer({len(buf_urls)}): {buf_urls[:5]}")

        detail = {"note_id": note_id, "url": url}
        feed_items = self._drain("/feed")
        self.log(f"  [dbg] /feed hits: {len(feed_items)}")
        for item in feed_items:
            d = item["data"].get("data") or {}
            for it in d.get("items", []) or []:
                nc = it.get("note_card") or {}
                if not nc: continue
                user = nc.get("user") or {}
                info = nc.get("interact_info") or {}
                detail.update({
                    "title": nc.get("title", ""),
                    "desc": nc.get("desc", ""),
                    "type": nc.get("type", ""),
                    "time": nc.get("time", ""),
                    "last_update_time": nc.get("last_update_time", ""),
                    "ip_location": (nc.get("ip_location") or nc.get("ipLocation")
                                    or user.get("ip_location") or user.get("ipLocation") or ""),
                    "author": user.get("nickname", ""),
                    "author_id": user.get("user_id", ""),
                    "liked_count": info.get("liked_count", ""),
                    "collected_count": info.get("collected_count", ""),
                    "comment_count": info.get("comment_count", ""),
                    "share_count": info.get("share_count", ""),
                    "tag_list": ",".join([t.get("name", "") for t in (nc.get("tag_list") or [])]),
                    "image_urls": ",".join([(i.get("url_default") or i.get("url", ""))
                                            for i in (nc.get("image_list") or [])]),
                })
                video = nc.get("video") or {}
                if video:
                    streams = (((video.get("media") or {}).get("stream") or {}).get("h264") or [])
                    if streams:
                        detail["video_url"] = streams[0].get("master_url", "")
                break

        # IP 属地兜底：如果 API 没返回，从页面 DOM 抓
        if not detail.get("ip_location"):
            try:
                ip_dom = self._scrape_ip_from_dom()
                if ip_dom:
                    detail["ip_location"] = ip_dom
            except Exception:
                pass

        # 兜底：如果 image_urls 为空（API 没拦到，纯 SSR 页面），从 DOM 直接提取
        if not detail.get("image_urls"):
            try:
                dom_data = self._scrape_note_from_dom()
                if dom_data:
                    # 不覆盖已有字段
                    for k, v in dom_data.items():
                        if v and not detail.get(k):
                            detail[k] = v
                    self.log(f"  [dbg] DOM 兜底: 提取 {len((dom_data.get('image_urls') or '').split(',') if dom_data.get('image_urls') else [])} 张图")
            except Exception as e:
                self.log(f"  [dbg] DOM 兜底失败: {e}")

        comments = self._fetch_comments(max_comments) if want_comments else []
        return detail, comments

    def _scrape_note_from_dom(self):
        """从笔记页 DOM/window 全局对象提取数据 — SSR 兜底"""
        return self.page.evaluate("""() => {
            const out = {};
            // 1. 尝试 window.__INITIAL_STATE__ (XHS SSR 注入点)
            try {
                const st = window.__INITIAL_STATE__;
                if (st) {
                    // XHS 把笔记数据放在 st.note.noteDetailMap[noteId].note
                    const noteMap = st.note && (st.note.noteDetailMap || st.note.detailMap);
                    if (noteMap) {
                        const keys = Object.keys(noteMap);
                        for (const k of keys) {
                            const n = noteMap[k];
                            const nc = n && (n.note || n);
                            if (!nc) continue;
                            const imgs = (nc.imageList || nc.image_list || []).map(
                                i => i.urlDefault || i.url_default || i.url || ''
                            ).filter(Boolean);
                            if (imgs.length) {
                                out.title = nc.title || '';
                                out.desc = nc.desc || '';
                                out.type = nc.type || '';
                                const user = nc.user || {};
                                out.author = user.nickname || '';
                                out.author_id = user.userId || user.user_id || '';
                                const info = nc.interactInfo || nc.interact_info || {};
                                out.liked_count = String(info.likedCount || info.liked_count || '');
                                out.collected_count = String(info.collectedCount || info.collected_count || '');
                                out.comment_count = String(info.commentCount || info.comment_count || '');
                                out.share_count = String(info.shareCount || info.share_count || '');
                                out.ip_location = nc.ipLocation || nc.ip_location || '';
                                out.tag_list = (nc.tagList || nc.tag_list || []).map(t => t.name || '').filter(Boolean).join(',');
                                out.image_urls = imgs.join(',');
                                const video = nc.video || {};
                                const streams = (((video.media || {}).stream || {}).h264) || [];
                                if (streams.length) out.video_url = streams[0].masterUrl || streams[0].master_url || '';
                                return out;
                            }
                        }
                    }
                }
            } catch(e) {}
            // 2. DOM 兜底: 从 img 标签和 meta 提取
            try {
                const urls = new Set();
                const sels = [
                    '.swiper-wrapper img', '.swiper-slide img',
                    '.note-slider-img', '.note-content img',
                    '.media-container img', '.note-image',
                    'meta[property="og:image"]'
                ];
                for (const sel of sels) {
                    for (const el of document.querySelectorAll(sel)) {
                        const src = el.src || el.getAttribute('content') || '';
                        if (!src || !src.startsWith('http')) continue;
                        if (src.includes('avatar') || src.includes('emoji')) continue;
                        if (src.includes('xhscdn') || src.includes('xiaohongshu')) urls.add(src);
                    }
                }
                if (urls.size) out.image_urls = Array.from(urls).join(',');
                // title 兜底
                const t1 = document.querySelector('meta[property="og:title"]');
                if (t1) out.title = t1.getAttribute('content') || '';
                const t2 = document.querySelector('.title, #detail-title');
                if (!out.title && t2) out.title = t2.textContent.trim();
                const t3 = document.querySelector('meta[name="description"]');
                if (t3) out.desc = t3.getAttribute('content') || '';
            } catch(e) {}
            return out;
        }""")

    def fetch_note_ip_only(self, url_or_id, timeout=10):
        """ 仅采 IP 属地 - 加载笔记页 → API + DOM 双重抓取，比 fetch_note_detail 快 5-10x
            策略：① feed API 返回的 ip_location/ipLocation
                  ② 页面 DOM 元素（XHS 在右下角渲染"日期 城市"）
        """
        if url_or_id.startswith("http"):
            url = url_or_id
            m = re.search(r"/(?:explore|discovery/item)/([0-9a-zA-Z]+)", url)
            note_id = m.group(1) if m else url
        else:
            note_id = url_or_id.strip()
            url = f"https://www.xiaohongshu.com/explore/{note_id}"
        self._api_buffer.clear()
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._check_stop()
            # 1) 优先 API
            for item in self._drain("/feed"):
                d = item["data"].get("data") or {}
                for it in d.get("items", []) or []:
                    nc = it.get("note_card") or {}
                    user = nc.get("user") or {}
                    ip = (nc.get("ip_location") or nc.get("ipLocation")
                          or user.get("ip_location") or user.get("ipLocation") or "")
                    if ip:
                        return ip
            # 2) DOM 兜底（XHS 在笔记页底部渲染 "日期 城市"）
            ip = self._scrape_ip_from_dom()
            if ip:
                return ip
            self._sleep(0.4)
        return ""

    def _scrape_ip_from_dom(self):
        """ 从笔记页 DOM 抓 IP 属地（API 不返回时兜底）
            XHS 在笔记右下角显示 '编辑于 2025-08-28 江苏' 或仅 '2025-08-28 江苏' """
        try:
            return self.page.evaluate(r"""() => {
                // 已知中国省份/直辖市/海外地区列表
                const REGIONS = ['北京','上海','天津','重庆','河北','山西','辽宁','吉林','黑龙江',
                    '江苏','浙江','安徽','福建','江西','山东','河南','湖北','湖南','广东','海南',
                    '四川','贵州','云南','陕西','甘肃','青海','广西','内蒙古','西藏','宁夏','新疆',
                    '台湾','香港','澳门','日本','美国','英国','德国','法国','韩国','澳大利亚',
                    '加拿大','新加坡','马来西亚','泰国','越南','印度','俄罗斯'];
                // 1) 优先精确选择器
                const selectors = ['.date', '.ip-location', '.location',
                                   '.note-content .date', '.publish-info'];
                for (const s of selectors) {
                    const el = document.querySelector(s);
                    if (el) {
                        const t = (el.innerText || '').trim();
                        for (const r of REGIONS) {
                            if (t.includes(r)) return r;
                        }
                    }
                }
                // 2) 暴力扫描整页文本，找形如 'YYYY-MM-DD 省份' 的结构
                const allText = document.body.innerText || '';
                const m = allText.match(/(?:20\d{2}-\d{1,2}-\d{1,2}|今天|昨天|\d+小时前|\d+天前)\s*([一-龥]{2,5})/);
                if (m) {
                    for (const r of REGIONS) {
                        if (m[1].includes(r)) return r;
                    }
                }
                // 3) 找 'IP属地：XX' / 'IP 属地 XX'
                const m2 = allText.match(/IP\s*属地[:：\s]*([一-龥]{2,5})/);
                if (m2) {
                    for (const r of REGIONS) {
                        if (m2[1].includes(r)) return r;
                    }
                }
                return '';
            }""")
        except Exception:
            return ""

    def _scroll_comments(self):
        """ 滚动评论容器。XHS 笔记页右侧评论是独立可滚动 div，page.mouse.wheel 无效。"""
        try:
            return bool(self.page.evaluate("""() => {
                const sels = [
                    '.note-scroller', '.scroller', '.note-page-scroller',
                    '.interaction-container', '.comments-container',
                    '.note-detail-content', '.comments-el',
                ];
                let did = false;
                for (const s of sels) {
                    for (const el of document.querySelectorAll(s)) {
                        if (el.scrollHeight > el.clientHeight + 50) {
                            el.scrollTop = el.scrollTop + 1800;
                            did = true;
                        }
                    }
                }
                if (!did) {
                    // 兜底：扫描所有 div 找最大可滚的
                    let best = null, max = 0;
                    document.querySelectorAll('*').forEach(el => {
                        const d = el.scrollHeight - el.clientHeight;
                        if (d > max && el.clientHeight > 300 && el.clientHeight < 2000) {
                            max = d; best = el;
                        }
                    });
                    if (best) {
                        best.scrollTop += 1800;
                        did = true;
                    }
                }
                return did;
            }"""))
        except Exception:
            return False

    def _expand_replies(self):
        """ 点击"展开 N 条回复"按钮，加载嵌套子评论 """
        try:
            btns = self.page.locator("span:has-text('展开'):has-text('回复')").all()
            for b in btns[:5]:
                try:
                    b.click(timeout=800)
                    self._sleep(0.4)
                except Exception:
                    continue
        except Exception:
            pass

    def _fetch_comments(self, max_count=100):
        self.log(f"采集评论 上限 {max_count}")
        collected, seen, last, stale = [], set(), 0, 0
        while len(collected) < max_count and stale < 6:
            self._check_stop()
            # 1) 滚评论容器
            scrolled = self._scroll_comments()
            if not scrolled:
                # 兜底：鼠标移到右侧再滚
                try:
                    vp = self.page.viewport_size or {"width": 1366, "height": 800}
                    self.page.mouse.move(vp["width"] * 0.75, vp["height"] * 0.5)
                    self.page.mouse.wheel(0, 2000)
                except Exception:
                    pass
            self._sleep(1.8)
            # 2) 展开嵌套回复
            self._expand_replies()
            self._sleep(0.5)

            for item in self._drain("/comment/"):
                d = item["data"].get("data") or {}
                for c in d.get("comments", []) or []:
                    cid = c.get("id")
                    if not cid or cid in seen: continue
                    seen.add(cid)
                    u = c.get("user_info") or {}
                    collected.append({
                        "comment_id": cid, "parent_id": "",
                        "content": c.get("content", ""),
                        "like_count": c.get("like_count", ""),
                        "create_time": c.get("create_time", ""),
                        "ip_location": c.get("ip_location", ""),
                        "user_nickname": u.get("nickname", ""),
                        "user_id": u.get("user_id", ""),
                        "sub_comment_count": c.get("sub_comment_count", 0),
                    })
                    for sc in c.get("sub_comments", []) or []:
                        scid = sc.get("id")
                        if not scid or scid in seen: continue
                        seen.add(scid)
                        su = sc.get("user_info") or {}
                        collected.append({
                            "comment_id": scid, "parent_id": cid,
                            "content": sc.get("content", ""),
                            "like_count": sc.get("like_count", ""),
                            "create_time": sc.get("create_time", ""),
                            "ip_location": sc.get("ip_location", ""),
                            "user_nickname": su.get("nickname", ""),
                            "user_id": su.get("user_id", ""),
                            "sub_comment_count": 0,
                        })
            self.log(f"  评论 {len(collected)}")
            if len(collected) == last: stale += 1
            else: stale, last = 0, len(collected)
        return collected[:max_count]

    # ---------------- 用户主页 ----------------
    def fetch_user_notes(self, user_url_or_id, max_count=30):
        if user_url_or_id.startswith("http"):
            url = user_url_or_id
        else:
            url = f"https://www.xiaohongshu.com/user/profile/{user_url_or_id.strip()}"

        self.log(f"采集用户主页: {url[:80]}")
        self._api_buffer.clear()
        self.page.goto(url, wait_until="domcontentloaded")
        self._sleep(3)

        collected, last, stale = {}, 0, 0
        while len(collected) < max_count and stale < 5:
            self._check_stop()
            self.page.mouse.wheel(0, 3000)
            self._sleep(2)
            for item in self._drain("user_posted"):
                d = item["data"].get("data") or {}
                for it in d.get("notes", []) or []:
                    nid = it.get("note_id") or it.get("id")
                    if not nid: continue
                    info = it.get("interact_info") or {}
                    ip_loc = it.get("ip_location") or ""
                    collected[nid] = {
                        "note_id": nid,
                        "title": it.get("display_title") or it.get("title", ""),
                        "type": it.get("type", ""),
                        "liked_count": info.get("liked_count", ""),
                        "ip_location": ip_loc,
                        "cover": (it.get("cover") or {}).get("url_default", ""),
                        "url": f"https://www.xiaohongshu.com/explore/{nid}",
                    }
            self.log(f"  当前 {len(collected)}")
            if len(collected) == last: stale += 1
            else: stale, last = 0, len(collected)
        return list(collected.values())[:max_count]

    # ---------------- 互动操作 ----------------
    def _require_login(self):
        if not self.is_logged_in():
            raise RuntimeError(f"账号 [{self.current_account}] 未登录")

    def post_reply_to_comment(self, note_url, target_comment_id, content):
        """ 页面模式精准回复指定评论。失败回退到顶层评论 """
        self._require_login()
        self.log(f"打开并回复 cid={target_comment_id[:10]}...")
        self.page.goto(note_url, wait_until="domcontentloaded")
        self._sleep(3)

        # 1) 滚动评论加载，找到目标评论
        found = False
        for _ in range(8):
            self._check_stop()
            in_dom = self.page.evaluate("""(cid) => {
                for (const el of document.querySelectorAll('*')) {
                    const id = el.id || (el.dataset && (el.dataset.id || el.dataset.commentId)) || '';
                    if (id && (id === cid || id.endsWith(cid))) return true;
                }
                return false;
            }""", target_comment_id)
            if in_dom:
                found = True
                break
            self._scroll_comments()
            self._sleep(1.5)

        if not found:
            self.log(f"  ⚠ 找不到目标评论，改发顶层")
            return self.post_comment_fast(note_url, content)

        # 2) 滚到目标评论 + 点 "回复" 按钮
        click_reply = """async (cid) => {
            // 找目标评论 DOM
            let target = null;
            for (const el of document.querySelectorAll('*')) {
                const id = el.id || (el.dataset && (el.dataset.id || el.dataset.commentId)) || '';
                if (id && (id === cid || id.endsWith(cid))) {
                    target = el;
                    break;
                }
            }
            if (!target) return {err: 'target lost'};
            target.scrollIntoView({behavior: 'instant', block: 'center'});
            await new Promise(r => setTimeout(r, 600));
            // 在目标评论 DOM 范围内找"回复"
            const reply = Array.from(target.querySelectorAll('*')).find(c => {
                const t = (c.textContent || '').trim();
                return t === '回复' && c.children.length === 0 && c.offsetParent !== null;
            });
            if (!reply) return {err: 'no reply btn in target'};
            reply.click();
            await new Promise(r => setTimeout(r, 800));
            return {ok: true};
        }"""
        r = self.page.evaluate(click_reply, target_comment_id)
        if not r.get("ok"):
            self.log(f"  ⚠ 点不到回复按钮 ({r.get('err')})，改发顶层")
            return self.post_comment_fast(note_url, content)

        # 3) 输入框已弹出（回复模式），注入内容并发送
        send_js = """async ({content}) => {
            // 找当前活动的 contenteditable
            let input = document.activeElement;
            if (!input || !input.isContentEditable) {
                for (const el of document.querySelectorAll('[contenteditable="true"]')) {
                    if (el.offsetParent !== null) { input = el; break; }
                }
            }
            if (!input) return {err: 'no input'};
            input.focus();
            await new Promise(r => setTimeout(r, 200));
            const ok = document.execCommand('insertText', false, content);
            if (!ok) {
                input.innerText = content;
                input.dispatchEvent(new InputEvent('input', {bubbles: true, data: content}));
            }
            await new Promise(r => setTimeout(r, 600));
            // 找发送
            for (const b of document.querySelectorAll('button, span, div')) {
                const t = (b.textContent || '').trim();
                if (t !== '发送' || b.offsetParent === null) continue;
                if (b.disabled) continue;
                if (b.classList && (b.classList.contains('disabled') ||
                                     b.classList.contains('not-active'))) continue;
                if (b.getAttribute('aria-disabled') === 'true') continue;
                b.click();
                return {ok: true};
            }
            return {err: 'no send btn'};
        }"""
        r2 = self.page.evaluate(send_js, {"content": content})
        self._sleep(2.5)
        if not r2.get("ok"):
            raise RuntimeError(r2.get("err", "回复失败"))
        self.log("✓ 精准回复已发送")
        return True

    def post_comment_fast(self, note_url, content):
        """ 一次性 JS 激活+输入+发送，比 post_comment 快约 2× """
        self._require_login()
        self.log(f"打开: {note_url[:80]}")
        self.page.goto(note_url, wait_until="domcontentloaded")
        self._sleep(2.5)

        js = """async ({content}) => {
            // 1) 点占位符激活
            for (const sel of ['.not-active.inner-when-not-active', '.not-active',
                                '.inner-when-not-active', '.engage-bar .content']) {
                const p = document.querySelector(sel);
                if (p && p.offsetParent !== null) {
                    p.click();
                    break;
                }
            }
            await new Promise(r => setTimeout(r, 500));

            // 2) 找输入框并 focus
            let input = null;
            for (const sel of ['#content-textarea',
                                '[contenteditable="true"][data-tribute="true"]',
                                '.content-input[contenteditable="true"]',
                                '[contenteditable="true"]']) {
                const el = document.querySelector(sel);
                if (el && el.offsetParent !== null) { input = el; break; }
            }
            if (!input) return {err: '没找到输入框'};
            input.focus();
            await new Promise(r => setTimeout(r, 200));

            // 3) execCommand 插入文本（让 Vue/React 检测到内容变化）
            const ok = document.execCommand('insertText', false, content);
            if (!ok) {
                // 兜底: 直接 innerText + 派发 input 事件
                input.innerText = content;
                input.dispatchEvent(new InputEvent('input', {bubbles: true, data: content}));
            }
            await new Promise(r => setTimeout(r, 600));

            // 4) 找发送按钮
            const candidates = Array.from(document.querySelectorAll('button, span, div'))
                .filter(b => {
                    const t = (b.textContent || '').trim();
                    return t === '发送' && b.offsetParent !== null;
                });
            for (const b of candidates) {
                if (b.disabled) continue;
                if (b.classList && (b.classList.contains('disabled') ||
                                     b.classList.contains('not-active'))) continue;
                if (b.getAttribute('aria-disabled') === 'true') continue;
                b.click();
                return {ok: true};
            }
            return {err: '没找到发送按钮'};
        }"""
        result = self.page.evaluate(js, {"content": content})
        self._sleep(2)
        if not result.get("ok"):
            raise RuntimeError(result.get("err", "评论失败"))
        self.log("✓ 评论已发送 (fast)")
        return True

    def post_comment(self, note_url, content):
        self._require_login()
        self.log(f"打开: {note_url[:80]}")
        self.page.goto(note_url, wait_until="domcontentloaded")
        self._sleep(4)

        # 1) 激活评论区（默认折叠，先点占位符让输入框可点击）
        activated = False
        for sel in [
            '.not-active.inner-when-not-active',
            '.inner-when-not-active',
            '.not-active',
            '.engage-bar .content',
            'span:has-text("说点什么")',
            'span:has-text("留下你的精彩评论")',
        ]:
            try:
                el = self.page.locator(sel).first
                if el.is_visible(timeout=800):
                    try: el.click(timeout=2000)
                    except Exception: el.click(timeout=2000, force=True)
                    self._sleep(0.8)
                    activated = True
                    break
            except Exception:
                continue
        if not activated:
            try:
                self.page.mouse.wheel(0, 1200); self._sleep(0.8)
            except Exception:
                pass

        # 2) 用 JS focus 输入框（绕过 click intercept）
        focused = False
        for sel in [
            '#content-textarea',
            '[contenteditable="true"][data-tribute="true"]',
            '.content-input[contenteditable="true"]',
            '.input-box [contenteditable="true"]',
            '[contenteditable="true"]',
        ]:
            try:
                el = self.page.locator(sel).first
                if el.count() == 0:
                    continue
                try:
                    el.evaluate("node => node.focus()")
                    focused = True; self._sleep(0.3); break
                except Exception:
                    pass
                try:
                    el.click(timeout=2000, force=True)
                    focused = True; self._sleep(0.3); break
                except Exception:
                    continue
            except Exception:
                continue
        if not focused:
            raise RuntimeError("找不到/无法聚焦评论输入框")

        # 3) 输入
        self.page.keyboard.type(content, delay=60)
        self._sleep(1)

        # 4) 发送
        sent = False
        for sel in [
            'button.submit:not([disabled])',
            'button.btn-submit:not([disabled])',
            '.btn.submit:not(.disabled)',
            '.submit:not(.disabled):not([disabled])',
            '.right-btn:has-text("发送")',
            'button:has-text("发送"):not([disabled])',
        ]:
            try:
                btn = self.page.locator(sel).first
                if btn.is_visible(timeout=1200):
                    try: btn.click(timeout=2000)
                    except Exception: btn.click(timeout=2000, force=True)
                    sent = True; break
            except Exception:
                continue
        if not sent:
            try: self.page.keyboard.press("Control+Enter"); sent = True
            except Exception: pass
        if not sent:
            raise RuntimeError("找不到发送按钮")
        self._sleep(2.5)
        self.log("✓ 评论已发送")
        return True

    def like_note(self, note_url):
        self._require_login()
        self.log(f"点赞: {note_url[:80]}")
        self.page.goto(note_url, wait_until="domcontentloaded")
        self._sleep(3)

        # 已点赞检测
        for sel in ['.like-active', '.like-wrapper.active', '.liked', '[class*="like-active"]']:
            try:
                if self.page.locator(sel).first.is_visible(timeout=800):
                    self.log("已经点过赞")
                    return True
            except Exception:
                continue

        for sel in [
            '.like-wrapper:not(.active):not(.liked)',
            '.like-lottie',
            '.interact-container .like',
            'span.like-wrapper',
            '[class*="like"]:not([class*="liked"]):not([class*="active"])',
        ]:
            try:
                btn = self.page.locator(sel).first
                if btn.is_visible(timeout=1500):
                    btn.click()
                    self._sleep(1.5)
                    self.log("✓ 已点赞")
                    return True
            except Exception:
                continue
        raise RuntimeError("找不到点赞按钮")

    def follow_user(self, user_url):
        self._require_login()
        if not user_url.startswith("http"):
            user_url = f"https://www.xiaohongshu.com/user/profile/{user_url}"
        self.log(f"打开用户页: {user_url[:80]}")
        self.page.goto(user_url, wait_until="domcontentloaded")
        self._sleep(3)

        # 已关注？
        for sel in ['button:has-text("已关注")', 'button:has-text("互相关注")',
                    '.follow-button.followed', 'text=已关注']:
            try:
                if self.page.locator(sel).first.is_visible(timeout=800):
                    self.log("已经关注过")
                    return True
            except Exception:
                continue

        for sel in [
            'button.follow-button:has-text("关注"):not(:has-text("已"))',
            '.reds-button:has-text("关注"):not(:has-text("已"))',
            'button:has-text("关注"):not(:has-text("已"))',
            '.user-info button:has-text("关注")',
        ]:
            try:
                btn = self.page.locator(sel).first
                if btn.is_visible(timeout=1500):
                    btn.click()
                    self._sleep(2)
                    self.log("✓ 已关注")
                    return True
            except Exception:
                continue
        raise RuntimeError("找不到关注按钮")

    # ---------------- 爆品发现 ----------------
    def search_hot_notes(self, keyword, max_scan=100, min_liked=0,
                         min_comments=0, min_collected=0,
                         sort_by="liked_count", top_n=30):
        """ 扫 max_scan 条搜索结果，按阈值过滤+排序，取前 top_n """
        from analyzer import parse_xhs_count
        rows = self.search_notes(keyword, max_scan)
        for r in rows:
            r["_n_liked"] = parse_xhs_count(r.get("liked_count"))
            r["_n_comments"] = parse_xhs_count(r.get("comment_count"))
            r["_n_collected"] = parse_xhs_count(r.get("collected_count"))
        before = len(rows)
        rows = [r for r in rows
                if r["_n_liked"] >= min_liked
                and r["_n_comments"] >= min_comments
                and r["_n_collected"] >= min_collected]
        sort_field = {
            "liked_count": "_n_liked",
            "comment_count": "_n_comments",
            "collected_count": "_n_collected",
        }.get(sort_by, "_n_liked")
        rows.sort(key=lambda r: r[sort_field], reverse=True)
        rows = rows[:top_n]
        for r in rows:
            r.pop("_n_liked", None)
            r.pop("_n_comments", None)
            r.pop("_n_collected", None)
        self.log(f"过滤: 扫描 {before} 条 → 命中 {len(rows)} 条爆品")
        return rows

    # ---------------- 媒体下载 ----------------
    def _fetch_bytes(self, url, timeout=30000, referer=None):
        """用浏览器上下文的 request 下载（带 Cookie/Headers），失败时 fallback 到 urllib
        referer: 来源页 URL（XHS CDN 校验 Referer，必须用具体笔记页 URL）
        """
        ref = referer or "https://www.xiaohongshu.com/"
        try:
            resp = self.context.request.get(
                url,
                headers={
                    "Referer": ref,
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Sec-Fetch-Dest": "image",
                    "Sec-Fetch-Mode": "no-cors",
                    "Sec-Fetch-Site": "cross-site",
                },
                timeout=timeout,
            )
            if resp.ok:
                return resp.body()
            else:
                self.log(f"    [dbg] CDN {resp.status}: {url[:90]}")
        except Exception as e:
            self.log(f"    [dbg] ctx.request 异常: {e}")
        # fallback: urllib
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": ref,
                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception:
            return None

    def download_media(self, detail, out_root):
        """ 下载笔记的图片和视频到 out_root/media_<note_id>/ """
        nid = detail.get("note_id", "x")
        folder = Path(out_root) / f"media_{nid}"
        folder.mkdir(parents=True, exist_ok=True)
        saved = []

        # 图片
        image_urls = [u for u in (detail.get("image_urls") or "").split(",") if u]
        self.log(f"  [dbg] image_urls({len(image_urls)}): {image_urls[:2]}")
        # 笔记页 URL 作 Referer（XHS CDN 校验来源页）
        note_url = detail.get("url", "https://www.xiaohongshu.com/")
        for i, url in enumerate(image_urls, 1):
            self._check_stop()
            # http -> https（XHS CDN 强制 HTTPS）
            if url.startswith("http://"):
                url = "https://" + url[7:]
            # 关键：保留 !xxx 处理后缀（CDN 鉴权需要），仅在带后缀时优先尝试 webp
            # 例如：https://sns-webpic-qc.xhscdn.com/.../xxx!nd_dft_wlteh_webp_3
            ext = ".webp" if "webp" in url.lower() else ".jpg"
            for e in (".png", ".jpeg"):
                if e in url.lower():
                    ext = e; break
            fp = folder / f"img_{i:03d}{ext}"
            try:
                data = self._fetch_bytes(url, referer=note_url)
                # 第一次失败：尝试砍掉处理后缀
                if not data and "!" in url:
                    data = self._fetch_bytes(url.split("!")[0], referer=note_url)
                if not data:
                    raise ValueError("空响应")
                fp.write_bytes(data)
                saved.append(str(fp))
                self.log(f"  ✓ 图 {i}/{len(image_urls)}")
            except Exception as e:
                self.log(f"  ✗ 图 {i}: {e}")

        # 视频
        vurl = detail.get("video_url", "")
        if vurl:
            self._check_stop()
            fp = folder / "video.mp4"
            try:
                data = self._fetch_bytes(vurl, timeout=120000)
                if not data:
                    raise ValueError("空响应")
                fp.write_bytes(data)
                saved.append(str(fp))
                self.log("  ✓ 视频")
            except Exception as e:
                self.log(f"  ✗ 视频: {e}")

        # 保存元数据 txt
        meta = folder / "info.txt"
        try:
            meta.write_text(
                f"标题: {detail.get('title','')}\n"
                f"作者: {detail.get('author','')}\n"
                f"URL : {detail.get('url','')}\n"
                f"描述:\n{detail.get('desc','')}\n",
                encoding="utf-8",
            )
        except Exception:
            pass

        self.log(f"保存到 {folder}")
        return folder

    def check_ip(self):
        """通过浏览器访问 ipify 拿出口 IP，用于验证代理"""
        try:
            self.page.goto("https://api.ipify.org?format=json",
                           wait_until="domcontentloaded", timeout=15000)
            body = self.page.content()
            m = re.search(r'"ip"\s*:\s*"([^"]+)"', body)
            ip = m.group(1) if m else "(解析失败)"
            self.log(f"✓ 当前出口 IP: {ip}")
            return ip
        except Exception as e:
            self.log(f"✗ IP 检测失败: {e}")
            return None

    # ---------------- API 直发模式 ----------------
    def _ensure_xhs_page(self):
        """ API 请求必须在 xiaohongshu.com 域名下发出（让 JS 自动签名） """
        try:
            cur = self.page.url or ""
        except Exception:
            cur = ""
        if "xiaohongshu.com" not in cur:
            self.log("导航到 XHS 首页以激活 API 签名")
            try:
                self.page.goto("https://www.xiaohongshu.com/explore",
                               wait_until="domcontentloaded", timeout=15000)
                time.sleep(1.5)
            except Exception as e:
                raise RuntimeError(f"无法打开 XHS 首页: {e}")

    def _get_cookie(self, name):
        try:
            for c in self.context.cookies():
                if c.get("name") == name:
                    return c.get("value", "")
        except Exception:
            pass
        return ""

    def _api_call(self, path, body):
        """ 浏览器内 fetch + Python 算签名头注入 """
        self._ensure_xhs_page()
        if path.startswith("/api/"):
            url = "https://edith.xiaohongshu.com" + path
            sign_uri = path  # 签名时 uri 不带 host
        else:
            url = path
            # 把绝对 URL 的 path 提取出来用于签名
            try:
                sign_uri = urlparse(path).path or path
            except Exception:
                sign_uri = path

        # 拿 a1 cookie
        a1 = self._get_cookie("a1")
        if not a1:
            raise RuntimeError("a1 cookie 缺失，无法签名（先登录）")
        try:
            sig = xhs_sign.sign(sign_uri, data=body, a1=a1, method="POST")
        except Exception as e:
            raise RuntimeError(f"签名生成失败: {e}")
        # 直接用浏览器 fetch + Python 算好的签名头
        js = """async ({url, body, headers}) => {
            try {
                const r = await fetch(url, {
                    method: 'POST',
                    headers: headers,
                    credentials: 'include',
                    body: JSON.stringify(body),
                });
                const text = await r.text();
                let data; try { data = JSON.parse(text); }
                catch(e) { data = {raw_text: text}; }
                return {http: r.status, data: data, via: 'py-sign'};
            } catch (e) {
                return {http: 0, err: e.toString(), via: 'py-sign-err'};
            }
        }"""
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "x-s": sig["x-s"],
            "x-t": sig["x-t"],
            "x-s-common": sig["x-s-common"],
        }
        if sig.get("x-rap-param"):
            headers["x-rap-param"] = sig["x-rap-param"]
        result = self.page.evaluate(js, {"url": url, "body": body, "headers": headers})
        via = result.get("via", "?")
        self.log(f"  (API via: {via})")
        if result.get("err"):
            raise RuntimeError(f"[{via}] {result['err']}")
        http = result.get("http", 0)
        if http != 200:
            raise RuntimeError(f"[{via}] HTTP {http}: {result.get('data')}")
        d = result.get("data") or {}
        code = d.get("code")
        if code not in (0, None) and d.get("success") is not True:
            raise RuntimeError(f"[{via}] code={code} msg={d.get('msg', '')}")
        return d

    def api_like(self, note_id):
        self._require_login()
        if not note_id:
            raise ValueError("note_id 为空")
        r = self._api_call("/api/sns/web/v1/note/like", {"note_oid": note_id})
        self.log(f"⚡ ✓ 点赞 {note_id}")
        return r

    def api_unlike(self, note_id):
        self._require_login()
        r = self._api_call("/api/sns/web/v1/note/dislike", {"note_oid": note_id})
        return r

    def api_follow(self, target_user_id):
        self._require_login()
        if not target_user_id:
            raise ValueError("target_user_id 为空")
        r = self._api_call("/api/sns/web/v1/user/follow",
                           {"target_user_id": target_user_id})
        self.log(f"⚡ ✓ 关注 {target_user_id}")
        return r

    def api_unfollow(self, target_user_id):
        self._require_login()
        r = self._api_call("/api/sns/web/v1/user/unfollow",
                           {"target_user_id": target_user_id})
        return r

    def api_comment(self, note_id, content, target_comment_id="", note_url=""):
        """ 评论 API（edith.xiaohongshu.com） """
        self._require_login()
        if not note_id:
            raise ValueError("note_id 为空")
        if not content or not content.strip():
            raise ValueError("评论内容为空")
        body = {
            "note_id": note_id,
            "content": content,
            "at_users": [],
        }
        if target_comment_id:
            body["target_comment_id"] = target_comment_id
        r = self._api_call("/api/sns/web/v1/comment/post", body)
        self.log(f"⚡ ✓ 评论 {note_id}: {content[:30]}")
        return r

    # ---------------- 自动养号 ----------------
    def auto_nurture(self, duration_minutes=30, like_prob=0.15,
                     collect_prob=0.05, max_per_note_sec=20):
        """ 模拟真人浏览：开 explore 页 → 随机点笔记 → 滚正文+评论 → 偶尔点赞收藏 → 退出
            duration_minutes: 总时长
            like_prob:        每个笔记的点赞概率
            collect_prob:     每个笔记的收藏概率
        """
        self._require_login()
        self.log(f"🌱 启动养号 时长 {duration_minutes}分 "
                 f"点赞率 {like_prob:.0%} 收藏率 {collect_prob:.0%}")
        try:
            self.page.goto("https://www.xiaohongshu.com/explore",
                           wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            self.log(f"打开 explore 失败: {e}")
            return
        self._sleep(random.uniform(3, 5))

        end_time = time.time() + duration_minutes * 60
        stats = {"browsed": 0, "like": 0, "collect": 0}

        while time.time() < end_time:
            self._check_stop()
            # 1) home feed 上滚一会（分多帧小步滚，模拟真实滚轮节奏）
            scroll_rounds = random.randint(1, 3)
            for _ in range(scroll_rounds):
                self._check_stop()
                total_scroll = random.randint(600, 1400)
                steps = random.randint(4, 9)
                for s in range(steps):
                    step_delta = total_scroll // steps + random.randint(-30, 30)
                    self.page.mouse.wheel(0, step_delta)
                    self._sleep(random.uniform(0.08, 0.22))
                self._sleep(random.uniform(1.2, 3.5))

            # 2) 拿到 feed 里笔记卡片，用真实鼠标点击（而非 goto）
            # 真实用户不会直接 goto url，是在 feed 卡片上点击
            try:
                card_info = self.page.evaluate("""() => {
                    const cards = [];
                    for (const a of document.querySelectorAll('a[href*="/explore/"]')) {
                        if (!a.href.includes('xsec_token')) continue;
                        const r = a.getBoundingClientRect();
                        if (r.width < 50 || r.height < 50) continue;
                        if (r.top < 0 || r.bottom > window.innerHeight) continue;
                        cards.push({
                            href: a.href,
                            cx: r.left + r.width * (0.3 + Math.random() * 0.4),
                            cy: r.top  + r.height * (0.3 + Math.random() * 0.4),
                        });
                        if (cards.length >= 12) break;
                    }
                    return cards;
                }""")
            except Exception:
                card_info = []

            if not card_info:
                self._sleep(random.uniform(1.5, 3))
                continue

            card = random.choice(card_info)
            try:
                self.log("  👁 浏览笔记（点击卡片）")
                # 先把鼠标挪到卡片附近，模拟阅读卡片过程
                self.page.mouse.move(
                    card["cx"] + random.uniform(-30, 30),
                    card["cy"] + random.uniform(-20, 20),
                )
                self._sleep(random.uniform(0.4, 1.2))
                self.page.mouse.click(card["cx"], card["cy"])
                # 等页面跳转完成
                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=12000)
                except Exception:
                    pass
            except Exception:
                continue
            stats["browsed"] += 1
            view_sec = random.uniform(5, max_per_note_sec)
            self._sleep(view_sec)

            # 3) 滚评论
            for _ in range(random.randint(1, 3)):
                self._check_stop()
                try: self._scroll_comments()
                except Exception: pass
                self._sleep(random.uniform(2, 4))

            # 4) 偶尔点赞
            if random.random() < like_prob:
                try:
                    btn = self.page.locator(
                        '.like-wrapper:not(.active), .like-lottie, .interact-container .like'
                    ).first
                    if btn.is_visible(timeout=1000):
                        btn.click(timeout=2000)
                        stats["like"] += 1
                        self.log(f"  💗 养号点赞 (累计 {stats['like']})")
                        self._sleep(random.uniform(1, 2))
                except Exception:
                    pass

            # 5) 更少概率收藏
            if random.random() < collect_prob:
                try:
                    btn = self.page.locator(
                        '.collect-wrapper:not(.active), [class*="collect"]:not([class*="active"])'
                    ).first
                    if btn.is_visible(timeout=1000):
                        btn.click(timeout=2000)
                        stats["collect"] += 1
                        self.log(f"  ⭐ 养号收藏 (累计 {stats['collect']})")
                except Exception:
                    pass

            # 6) 退回 explore
            self._sleep(random.uniform(1.5, 3))
            try:
                self.page.go_back(timeout=5000)
            except Exception:
                try:
                    self.page.goto("https://www.xiaohongshu.com/explore",
                                   wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
            self._sleep(random.uniform(2, 5))

        self.log(f"🌱 养号完成: 浏览 {stats['browsed']} 点赞 {stats['like']} "
                 f"收藏 {stats['collect']}")
        return stats

    # ---------------- 笔记发布（页面模式） ----------------
    def publish_note(self, title, body, images=None, tags=None, note_type="image",
                     video_path=None):
        """ 在创作中心发笔记
            title:     标题
            body:      正文
            note_type: 'image' 图文 / 'video' 视频 / 'longtext' 长文
            images:    图文模式必填
            video_path:视频模式必填
            tags:      标签列表
        """
        self._require_login()
        tags = tags or []
        images = images or []
        if note_type == "image":
            if not images:
                raise ValueError("图文模式至少需要 1 张图片")
            for p in images:
                if not Path(p).exists():
                    raise ValueError(f"图片不存在: {p}")
        elif note_type == "video":
            if not video_path or not Path(video_path).exists():
                raise ValueError("视频文件不存在")
        elif note_type == "longtext":
            if not body or len(body.strip()) < 50:
                raise ValueError("长文需至少 50 字正文")
        else:
            raise ValueError(f"未知类型: {note_type}")

        self.log(f"📝 打开创作中心 [{note_type}]: {title[:30]}")
        self.page.goto(
            "https://creator.xiaohongshu.com/publish/publish?source=official",
            wait_until="domcontentloaded", timeout=30000,
        )
        self._sleep(4)

        # 1) 切到对应 tab（按 4 个 tab 的位置定位最准）
        tab_text = {
            "image": "上传图文",
            "video": "上传视频",
            "longtext": "写长文",
        }[note_type]
        tab_idx = {"video": 0, "image": 1, "longtext": 2, "podcast": 3}[note_type]
        self._sleep(3)  # 等 tab 渲染好

        # 先用 JS 找到目标 tab 的坐标，然后用 page.mouse 真实点击
        result = self.page.evaluate("""({wanted, idx}) => {
            const allTabs = ['上传视频', '上传图文', '写长文', '发播客'];
            const found = [];
            for (const el of document.querySelectorAll('*')) {
                if (el.children.length > 0) continue;
                const t = (el.textContent || '').trim();
                if (!allTabs.includes(t)) continue;
                if (el.offsetParent === null) continue;
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                found.push({t, x: r.left + r.width/2, y: r.top + r.height/2,
                            cx: r.left, cy: r.top, w: r.width, h: r.height});
            }
            // 按 Y 分组
            const groups = {};
            for (const f of found) {
                const key = Math.round(f.cy / 20) * 20;
                if (!groups[key]) groups[key] = [];
                groups[key].push(f);
            }
            // 取最顶部且含 ≥2 个不同 tab 的组
            let bestGroup = null, bestY = Infinity;
            for (const k in groups) {
                const distinct = new Set(groups[k].map(f => f.t)).size;
                if (distinct < 2) continue;
                const y = parseFloat(k);
                if (y < bestY) { bestY = y; bestGroup = groups[k]; }
            }
            if (!bestGroup) {
                return {ok: false, names: found.map(f => `${f.t}@${Math.round(f.cy)}`)};
            }
            // 同名去重，按 x 排序
            const seen = new Set();
            const tabs = [];
            for (const f of bestGroup) {
                if (seen.has(f.t)) continue;
                seen.add(f.t); tabs.push(f);
            }
            tabs.sort((a, b) => a.cx - b.cx);
            let target = tabs.find(f => f.t === wanted) || tabs[idx];
            if (!target) return {ok: false, names: tabs.map(f => f.t)};
            return {
                ok: true, clicked: target.t,
                cx: target.x, cy: target.y,
                topY: Math.round(bestY),
                all: tabs.map(f => f.t),
            };
        }""", {"wanted": tab_text, "idx": tab_idx})

        if not result.get("ok"):
            raise RuntimeError(
                f"找不到 [{tab_text}] tab. 页面 tab 候选: {result.get('names') or '(空)'}"
            )
        # 用真实鼠标坐标点击（让浏览器走完整事件链）
        cx, cy = result["cx"], result["cy"]
        self.log(f"  → 点击 tab [{result['clicked']}] @ ({int(cx)},{int(cy)}) "
                 f"顺序 {result.get('all')}")
        try:
            self.page.mouse.move(cx, cy)
            self._sleep(0.2)
            self.page.mouse.click(cx, cy)
        except Exception as e:
            self.log(f"  ⚠ mouse click 失败: {e}")
        self._sleep(2.5)

        # 长文页经常存在“误点到非 tab 元素”的情况：日志显示切到写长文，但页面仍停留在上传视频页。
        # 这里做一次显式确认，不通过就 fallback 用 Playwright 定位再点一次。
        if note_type == "longtext":
            entered = False
            try:
                entered = self.page.locator('text=新的创作').first.is_visible(timeout=2500)
                if not entered:
                    entered = self.page.locator('text=支持千字长文').first.is_visible(timeout=2500)
            except Exception:
                entered = False

            if not entered:
                # 重试: 重新算一次坐标 + 真实鼠标点击
                self.log("  ⚠ 第一次切换未生效，重试")
                retry = self.page.evaluate("""() => {
                    const tabs = [];
                    for (const el of document.querySelectorAll('*')) {
                        if (el.children.length > 0) continue;
                        const t = (el.textContent || '').trim();
                        if (t !== '写长文') continue;
                        if (el.offsetParent === null) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) continue;
                        tabs.push({y: r.top, x: r.left + r.width/2,
                                    cy: r.top + r.height/2});
                    }
                    tabs.sort((a, b) => a.y - b.y);
                    return tabs[0] || null;
                }""")
                if retry:
                    try:
                        self.page.mouse.move(retry["x"], retry["cy"])
                        self._sleep(0.3)
                        self.page.mouse.click(retry["x"], retry["cy"])
                    except Exception:
                        pass
                    self._sleep(2.5)

                try:
                    entered = self.page.locator('text=新的创作').first.is_visible(timeout=5000)
                    if not entered:
                        entered = self.page.locator('text=支持千字长文').first.is_visible(timeout=5000)
                except Exception:
                    entered = False

            if not entered:
                self._dump_debug("longtext_tab_not_entered")
                raise RuntimeError("写长文 tab 点击后仍未进入长文页面（可能页面结构变更）")

        # 2) 上传文件（仅图文/视频）
        if note_type in ("image", "video"):
            files_to_upload = images if note_type == "image" else [video_path]
            upload_timeout = 180 if note_type == "image" else 600
            accept_filter = ("image" if note_type == "image" else "video")
            file_candidates = self.page.locator(
                f'input[type="file"][accept*="{accept_filter}"]'
            ).all()
            if not file_candidates:
                file_candidates = self.page.locator('input[type="file"]').all()
            if not file_candidates:
                raise RuntimeError("找不到上传文件输入框，页面结构可能变了")
            try:
                file_candidates[0].set_input_files(files_to_upload)
                self.log(f"  ⬆ 上传 {len(files_to_upload)} 个文件中...")
            except Exception as e:
                raise RuntimeError(f"文件上传失败: {e}")

            end = time.time() + upload_timeout
            uploaded = False
            while time.time() < end:
                self._check_stop()
                # 用 JS 检查标题输入框是否已渲染（任何含"标题"占位的可见 input）
                try:
                    ok = self.page.evaluate("""() => {
                        for (const el of document.querySelectorAll('input')) {
                            if (el.offsetParent === null) continue;
                            const ph = el.placeholder || '';
                            if (ph.includes('标题') || ph.toLowerCase().includes('title')) {
                                return true;
                            }
                        }
                        return false;
                    }""")
                    if ok:
                        uploaded = True; break
                except Exception:
                    pass
                self._sleep(2)
            if not uploaded:
                raise RuntimeError(f"上传/处理超时（{upload_timeout}s）")
            self.log(f"  ✓ 文件就绪")
            self._sleep(2)
        else:
            # 长文：先到落地页，必须点"新的创作"才进编辑器（用真实鼠标坐标）
            self._sleep(2)
            target = self.page.evaluate("""() => {
                for (const el of document.querySelectorAll('button, a, div, span')) {
                    if (el.offsetParent === null) continue;
                    const t = (el.textContent || '').trim();
                    if (t !== '新的创作') continue;
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    return {x: r.left + r.width/2, y: r.top + r.height/2};
                }
                return null;
            }""")
            if target:
                self.log(f"  → 点击[新的创作] @ ({int(target['x'])},{int(target['y'])})")
                try:
                    self.page.mouse.move(target["x"], target["y"])
                    self._sleep(0.3)
                    self.page.mouse.click(target["x"], target["y"])
                except Exception as e:
                    self.log(f"  ⚠ 鼠标点击失败: {e}")
                self._sleep(3)
            else:
                self.log("  ⚠ 找不到[新的创作]按钮")

            # 等编辑器（两个 contenteditable 出现）
            end = time.time() + 20
            entered_editor = False
            while time.time() < end:
                try:
                    n = self.page.evaluate("""() => {
                        let n = 0;
                        for (const el of document.querySelectorAll('[contenteditable="true"], [contenteditable=""]')) {
                            if (el.offsetParent === null) continue;
                            const r = el.getBoundingClientRect();
                            if (r.width > 50 && r.height > 10) n++;
                        }
                        return n;
                    }""")
                    if n >= 2:
                        entered_editor = True
                        break
                except Exception:
                    pass
                self._sleep(1)
            if entered_editor:
                self.log("  ✓ 长文编辑器就绪")
            self._sleep(2)

        # 4) 填标题
        if note_type == "longtext":
            # 长文: ProseMirror 富文本，标题/正文是同一编辑器里的两个段落
            # 必须用真实鼠标点击占位符位置 + 键盘输入
            positions = self.page.evaluate("""() => {
                function find(text) {
                    for (const el of document.querySelectorAll('*')) {
                        if (el.children.length > 0) continue;
                        const t = (el.textContent || '').trim();
                        if (!t.includes(text)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) continue;
                        if (r.top < 80) continue;  // 跳过顶部 nav
                        return {x: r.left + r.width/2, y: r.top + r.height/2};
                    }
                    // 占位符可能 textContent 为空（用 ::before 渲染）
                    // 退而求其次找带 data-placeholder 属性的
                    for (const el of document.querySelectorAll('[data-placeholder], [placeholder]')) {
                        if (el.offsetParent === null) continue;
                        const ph = el.getAttribute('data-placeholder') ||
                                    el.getAttribute('placeholder') || '';
                        if (!ph.includes(text)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width === 0) continue;
                        return {x: r.left + r.width/2, y: r.top + r.height/2};
                    }
                    return null;
                }
                return {
                    title: find('输入标题'),
                    body: find('输入文字'),
                };
            }""")
            tp = positions.get("title") if positions else None
            bp = positions.get("body") if positions else None
            if not tp:
                try: self._dump_debug("longtext_title_pos_not_found")
                except Exception: pass
                raise RuntimeError("找不到标题占位符 '输入标题'")
            # 点标题占位 → 输入
            self.log(f"  → 点标题 @ ({int(tp['x'])},{int(tp['y'])})")
            self._human_click(tp["x"], tp["y"])
            self._sleep(random.uniform(0.3, 0.6))
            self._human_type(title[:30])
            self._sleep(random.uniform(0.3, 0.6))
            self.log(f"  ✓ 标题填好")

            # 点正文占位 → 输入（注意 body 位置在标题输入后可能上移）
            # 先重新算 body 位置
            bp2 = self.page.evaluate("""() => {
                for (const el of document.querySelectorAll('*')) {
                    if (el.children.length > 0) continue;
                    const t = (el.textContent || '').trim();
                    if (!t.includes('输入文字')) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    return {x: r.left + r.width/2, y: r.top + r.height/2};
                }
                return null;
            }""")
            bp_use = bp2 or bp
            if bp_use:
                self.log(f"  → 点正文 @ ({int(bp_use['x'])},{int(bp_use['y'])})")
                self._human_click(bp_use["x"], bp_use["y"])
                self._sleep(random.uniform(0.3, 0.6))
                full_body = body
                if tags:
                    full_body += "\n" + " ".join(f"#{t}#" for t in tags)
                # 分段真实打字：每段 40 字左右停顿一下，模拟人类输入节奏
                chunk = 40
                for start in range(0, len(full_body), chunk):
                    part = full_body[start:start + chunk]
                    self._human_type(part, base_delay_ms=55)
                    if start + chunk < len(full_body):
                        self._sleep(random.uniform(0.2, 0.6))
                self._sleep(1)
                self.log(f"  ✓ 正文填好")
            else:
                # 用 Tab 键尝试从标题跳到正文
                try:
                    self.page.keyboard.press("Tab")
                    self._sleep(random.uniform(0.4, 0.7))
                    full_body = body
                    if tags:
                        full_body += "\n" + " ".join(f"#{t}#" for t in tags)
                    chunk = 40
                    for start in range(0, len(full_body), chunk):
                        part = full_body[start:start + chunk]
                        self._human_type(part, base_delay_ms=55)
                        if start + chunk < len(full_body):
                            self._sleep(random.uniform(0.2, 0.5))
                    self.log(f"  ✓ 正文填好(Tab键)")
                except Exception:
                    self.log("  ⚠ 找不到正文位置，可能正文为空")
            self._sleep(1.5)
            # 跳过后面的标准填正文流程
            _longtext_filled = True
        else:
            # 图文/视频: 找标题 input，用真实鼠标点击 + keyboard.type 逐字输入
            # 注意：JS setter 方式缺少 keydown/keypress/keyup 事件且 isTrusted=false，
            # 是被平台检测的关键信号，必须走键盘路径。
            title_pos = self.page.evaluate("""() => {
                for (const el of document.querySelectorAll('input')) {
                    if (el.offsetParent === null) continue;
                    const ph = el.placeholder || '';
                    if (!(ph.includes('标题') || ph.toLowerCase().includes('title'))) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    return {x: r.left + r.width * 0.15, y: r.top + r.height / 2};
                }
                return null;
            }""")
            if not title_pos:
                try: self._dump_debug(f"{note_type}_title_not_found")
                except Exception: pass
                raise RuntimeError("找不到标题输入框（DOM 可能变了）")
            # 贝塞尔曲线点击聚焦
            self._human_click(title_pos["x"], title_pos["y"])
            self._sleep(random.uniform(0.3, 0.6))
            self.page.keyboard.press("Control+a")
            self._sleep(random.uniform(0.1, 0.2))
            self._human_type(title[:30])
            self.log(f"  ✓ 标题填好")
            self._sleep(random.uniform(0.4, 0.8))

        # 5) 填正文（长文已经在 4) 一起填了，跳过）
        full_body = body
        if tags:
            full_body += "\n" + " ".join(f"#{t}#" for t in tags)
        if note_type == "longtext":
            ok = True  # 已在上面填
        else:
            # 图文/视频: 找正文编辑器坐标，然后真实点击 + 分段输入
            body_pos = self.page.evaluate("""() => {
                let best = null; let bestArea = 0;
                const all = document.querySelectorAll(
                    '[contenteditable="true"], textarea[placeholder*="正文"], '
                    + 'textarea[placeholder*="描述"], textarea[placeholder*="content"]'
                );
                for (const el of all) {
                    if (el.offsetParent === null) continue;
                    const txt = (el.textContent || '').trim();
                    const ph = el.placeholder || '';
                    if (txt.includes('输入标题') || ph.includes('标题')) continue;
                    const r = el.getBoundingClientRect();
                    const area = r.width * r.height;
                    if (area > bestArea) {
                        bestArea = area;
                        best = {x: r.left + r.width * 0.1, y: r.top + 20};
                    }
                }
                return best;
            }""")
            if body_pos:
                self._human_click(body_pos["x"], body_pos["y"])
                self._sleep(random.uniform(0.3, 0.6))
                chunk = 40
                for start in range(0, len(full_body), chunk):
                    part = full_body[start:start + chunk]
                    self._human_type(part, base_delay_ms=55)
                    if start + chunk < len(full_body):
                        self._sleep(random.uniform(0.15, 0.5))
                ok = True
            else:
                ok = False
        if not ok:
            self.log("  ⚠ 找不到正文编辑器（保留空正文）")
        else:
            self.log("  ✓ 正文填好")
        self._sleep(1.5)

        # 6) 多步推进发布
        # 长文流程: 一键排版 → 模板选择(下一步) → 最终发布页(发布)
        # 图文/视频: 直接(发布)
        self._sleep(2)

        # JS: 找发布相关按钮 — 多重判定，避免误点页面标题/导航
        # 核心：必须是 button 标签 或 有 button-like 视觉特征（实色背景 + cursor:pointer）
        find_btn_js = """(keywords) => {
            const results = [];
            // 只扫真按钮：button / role=button / 含 btn 类名 / 含 cursor:pointer 的可点元素
            const sel = 'button, [role="button"], [class*="btn"], [class*="Btn"], [class*="button"], [class*="Button"], [class*="publish"], [class*="submit"]';
            for (const kw of keywords) {
                for (const el of document.querySelectorAll(sel)) {
                    if (el.disabled) continue;
                    const cls = (typeof el.className === 'string') ? el.className : '';
                    if (cls.includes('disabled') || cls.includes('--disabled')) continue;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) < 0.3) continue;
                    if (st.pointerEvents === 'none') continue;
                    const t = (el.textContent || '').trim();
                    if (t !== kw) continue;

                    // 文字外观不应该像标题：H1~H6 / role=heading 直接排除
                    if (/^H[1-6]$/.test(el.tagName) || el.getAttribute('role') === 'heading') continue;
                    // 父级若是 heading/title 也排除
                    let p = el.parentElement, isInTitle = false;
                    for (let i = 0; p && i < 3; i++, p = p.parentElement) {
                        const pcls = (typeof p.className === 'string') ? p.className.toLowerCase() : '';
                        if (pcls.includes('title') || pcls.includes('header') || pcls.includes('breadcrumb') || pcls.includes('tab-bar')) { isInTitle = true; break; }
                        if (/^H[1-6]$/.test(p.tagName)) { isInTitle = true; break; }
                    }
                    if (isInTitle) continue;

                    // 按钮形状：必须有合理宽高
                    const r0 = el.getBoundingClientRect();
                    if (r0.width < 60 || r0.height < 28 || r0.height > 70) continue;
                    // 字号约束：按钮文字一般 12~18px，标题文字往往 ≥20px
                    const fs = parseFloat(st.fontSize) || 14;
                    if (fs > 19) continue;

                    // 按钮视觉特征：button 标签 或 有实色背景
                    const isBtnTag = el.tagName === 'BUTTON' || el.getAttribute('role') === 'button';
                    const bg = st.backgroundColor || '';
                    const hasSolidBg = bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent' && !bg.startsWith('rgba(0, 0, 0, 0)');
                    const hasBtnClass = /\\bbtn\\b|\\bbutton\\b|publish|submit/i.test(cls);
                    if (!isBtnTag && !hasSolidBg && !hasBtnClass) continue;

                    // 滚到中央并回算坐标
                    try { el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'}); } catch(e) {}
                    const r = el.getBoundingClientRect();
                    const vh = window.innerHeight, vw = window.innerWidth;
                    if (r.top < 40 || r.bottom > vh + 5) continue;  // 滚动后仍在顶部 = sticky 标题，跳过
                    if (r.left < 0 || r.right > vw + 5) continue;

                    results.push({
                        x: r.left + r.width/2,
                        y: r.top + r.height/2,
                        text: t,
                        kw: kw,
                        // 评分：真按钮标签 + 大宽度 + 偏下 优先
                        score: (isBtnTag ? 100 : 0) + (hasSolidBg ? 50 : 0) + r.width * 0.1 + r.top * 0.05,
                    });
                }
                if (results.length) {
                    // 同一关键字下选评分最高
                    results.sort((a, b) => b.score - a.score);
                    return results[0];
                }
            }
            return null;
        }"""

        # 推进步骤先（一键排版/下一步），最后才是发布（避免误点 sidebar）
        # 注意：XHS 发布页底部按钮就叫"发布"两个字，'发布笔记'是页面顶部标题，必须排除！
        secondary = ['一键排版', '下一步', '继续', '提交']
        primary = ['发布', '立即发布', '确认发布']
        all_kw = secondary + primary

        # 先滚到底（发布按钮一定在底部）
        try:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        self._sleep(1)

        # 诊断 + 直接定位：扫所有红色背景的元素（XHS 品牌红 ≈ rgb(255, 36, 66)）
        # 这是发布按钮最可靠的视觉特征，与 textContent 无关
        try:
            red_btns = self.page.evaluate(r"""() => {
                const out = [];
                for (const el of document.querySelectorAll('*')) {
                    const st = window.getComputedStyle(el);
                    const bg = st.backgroundColor || '';
                    const m = bg.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
                    if (!m) continue;
                    const r = parseInt(m[1]), g = parseInt(m[2]), b = parseInt(m[3]);
                    // XHS 红色: R 高、G 低、B 低
                    if (r < 200 || g > 120 || b > 120) continue;
                    if (st.display === 'none' || st.visibility === 'hidden') continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 50 || rect.height < 24) continue;
                    if (rect.width > 400 || rect.height > 100) continue;
                    // 跳过过大的红色区域（可能是 banner）
                    if (rect.width * rect.height > 30000) continue;
                    out.push({
                        tag: el.tagName,
                        cls: (typeof el.className === 'string' ? el.className : '').slice(0, 80),
                        text: ((el.textContent || '').trim() ||
                               el.getAttribute('aria-label') ||
                               el.getAttribute('title') || '<no-text>').slice(0, 30),
                        x: Math.round(rect.left), y: Math.round(rect.top),
                        w: Math.round(rect.width), h: Math.round(rect.height),
                        bg: bg, cursor: st.cursor,
                    });
                    if (out.length > 20) break;
                }
                return out;
            }""")
            self.log(f"  [dbg] 红色按钮候选 {len(red_btns)} 个:")
            for d in red_btns:
                self.log(f"    - <{d['tag']}.{d['cls'][:40]}> '{d['text']}' "
                         f"@({d['x']},{d['y']}) {d['w']}x{d['h']} cursor={d['cursor']}")
        except Exception as e:
            self.log(f"  [dbg] 红色按钮扫描失败: {e}")
            red_btns = []

        published = False
        last_clicked = ""

        # 优先策略：Playwright locator + scroll_into_view_if_needed + click
        # Playwright 的 click 自带 auto-wait 和 auto-scroll，比 mouse.click 鲁棒得多
        def _try_playwright_click(kw):
            """尝试用 Playwright locator 点击关键字按钮，自动滚动+点击。"""
            # 多个选择器策略，按优先级排序
            selectors = [
                f'button:text-is("{kw}"):not([disabled])',
                f'div[role="button"]:text-is("{kw}")',
                f'[class*="submit"]:text-is("{kw}")',
                f'[class*="publish"]:text-is("{kw}")',
                f'button:has-text("{kw}"):not([disabled])',
            ]
            for sel in selectors:
                try:
                    loc = self.page.locator(sel)
                    n = loc.count()
                    if n == 0:
                        continue
                    # 优先选最靠下（底部主操作按钮通常在页面底部）
                    # 也排除字号 > 18px 的（标题）
                    best_idx = -1
                    best_y = -1
                    for i in range(n):
                        el = loc.nth(i)
                        try:
                            if not el.is_visible(timeout=300):
                                continue
                            box = el.bounding_box(timeout=500)
                            if not box or box["width"] < 50 or box["height"] < 24 or box["height"] > 70:
                                continue
                            # 排除标题字号
                            fs = el.evaluate("e => parseFloat(window.getComputedStyle(e).fontSize)")
                            if fs and fs > 19:
                                continue
                            # 排除顶部 100px 的元素（一般是标题/导航）
                            if box["y"] < 100:
                                continue
                            if box["y"] > best_y:
                                best_y = box["y"]
                                best_idx = i
                        except Exception:
                            continue
                    if best_idx < 0:
                        continue
                    el = loc.nth(best_idx)
                    el.scroll_into_view_if_needed(timeout=3000)
                    self._sleep(random.uniform(0.4, 0.8))
                    box = el.bounding_box(timeout=1000)
                    self.log(f"  → 点[{kw}] via locator {sel[:40]}... @"
                             f"({int(box['x']+box['width']/2)},{int(box['y']+box['height']/2)})")
                    # 用人类轨迹点击
                    self._human_click(box["x"] + box["width"]/2,
                                      box["y"] + box["height"]/2)
                    return True
                except Exception:
                    continue
            return None

        def _try_shadow_button_click(kw):
            """处理 <xhs-publish-btn> 闭合 Shadow DOM 按钮。
            两个内部按钮各 120px，居中排列，红色发布按钮中心 = host中心 + 70px"""
            try:
                # 1) get_by_role 走无障碍树，能穿透闭合 Shadow DOM（成功率高）
                try:
                    el = self.page.get_by_role("button", name=kw).last
                    if el.is_visible(timeout=800):
                        el.scroll_into_view_if_needed(timeout=2000)
                        self._sleep(random.uniform(0.4, 0.7))
                        box = el.bounding_box(timeout=1000)
                        if box and box["width"] >= 50 and box["width"] <= 200:
                            self.log(f"  → [role] 点'{kw}' @"
                                     f"({int(box['x']+box['width']/2)},{int(box['y']+box['height']/2)}) "
                                     f"size={int(box['width'])}x{int(box['height'])}")
                            self._human_click(box["x"] + box["width"]/2,
                                              box["y"] + box["height"]/2)
                            return True
                except Exception:
                    pass

                # 2) 找 <xhs-publish-btn> 容器，用 host中心+70px 定位红色按钮
                if kw in primary:
                    try:
                        host = self.page.locator(
                            'xhs-publish-btn[submit-text="发布"], xhs-publish-btn[is-publish="true"]'
                        ).first
                        if host.count() > 0 and host.is_visible(timeout=800):
                            host.scroll_into_view_if_needed(timeout=2000)
                            self._sleep(random.uniform(0.4, 0.7))
                            box = host.bounding_box(timeout=1000)
                            if box:
                                self.log(f"  [dbg] xhs-publish-btn box: "
                                         f"x={int(box['x'])} y={int(box['y'])} "
                                         f"w={int(box['width'])} h={int(box['height'])}")
                                # 内部 2 个 button 各 120px 居中排列，gap≈12px
                                # 发布(红)在右，其中心相对host中心偏右 60+6=66px
                                cx = box["x"] + box["width"] / 2 + 66
                                cy = box["y"] + box["height"] / 2
                                self.log(f"  → [shadow-host] 点 红色发布按钮中心 @({int(cx)},{int(cy)})")
                                self._human_click(cx, cy)
                                # 第二次保险点（部分布局 gap 不同）
                                self._sleep(0.3)
                                return True
                    except Exception as e:
                        self.log(f"  [dbg] shadow-host 失败: {e}")

                # 3) 终极兜底：用 JS 在 host 元素上 dispatchEvent 触发组件内部逻辑
                if kw in primary:
                    try:
                        ok = self.page.evaluate(r"""() => {
                            const host = document.querySelector(
                                'xhs-publish-btn[submit-text="发布"], xhs-publish-btn[is-publish="true"]'
                            );
                            if (!host) return false;
                            // 尝试访问闭合 shadow root（部分实现会暴露 _shadowRoot 或 __vue__）
                            const root = host.shadowRoot || host._shadowRoot || null;
                            if (root) {
                                const btn = root.querySelector('button.bg-red, button.ce-btn.bg-red');
                                if (btn) {
                                    btn.click();
                                    return true;
                                }
                            }
                            // 没有暴露 shadow root：直接派发 click 到 host 中心+offset
                            const rect = host.getBoundingClientRect();
                            const cx = rect.left + rect.width/2 + 66;
                            const cy = rect.top + rect.height/2;
                            // 创建 mouse 事件序列
                            for (const type of ['mousedown', 'mouseup', 'click']) {
                                const ev = new MouseEvent(type, {
                                    bubbles: true, cancelable: true, composed: true,
                                    clientX: cx, clientY: cy, button: 0,
                                });
                                document.elementFromPoint(cx, cy)?.dispatchEvent(ev);
                            }
                            return true;
                        }""")
                        if ok:
                            self.log(f"  → [js-dispatch] 触发 xhs-publish-btn 内部点击")
                            return True
                    except Exception as e:
                        self.log(f"  [dbg] js-dispatch 失败: {e}")
            except Exception:
                pass
            return False

        def _try_red_button_click():
            """通过 XHS 品牌红色背景定位发布按钮 — 终极兜底，不依赖文字。"""
            try:
                btn = self.page.evaluate(r"""() => {
                    let best = null, bestY = -1;
                    for (const el of document.querySelectorAll('*')) {
                        const st = window.getComputedStyle(el);
                        const bg = st.backgroundColor || '';
                        const m = bg.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
                        if (!m) continue;
                        const r = parseInt(m[1]), g = parseInt(m[2]), b = parseInt(m[3]);
                        if (r < 200 || g > 120 || b > 120) continue;
                        if (st.display === 'none' || st.visibility === 'hidden') continue;
                        if (st.pointerEvents === 'none') continue;
                        const rect = el.getBoundingClientRect();
                        if (rect.width < 50 || rect.height < 28) continue;
                        if (rect.width > 300 || rect.height > 80) continue;
                        // 跳过左上角导航按钮（sidebar 选中态也可能是红的）
                        if (rect.left < 200 && rect.top < 200) continue;
                        // 滚到中央
                        try { el.scrollIntoView({block: 'center', behavior: 'instant'}); } catch(e) {}
                        const r2 = el.getBoundingClientRect();
                        if (r2.top < 0 || r2.bottom > window.innerHeight + 10) continue;
                        // 选 Y 最大的（最靠下，避免误点上方红色元素）
                        if (r2.top > bestY) {
                            bestY = r2.top;
                            best = {x: r2.left + r2.width/2, y: r2.top + r2.height/2,
                                    text: (el.textContent || '').trim() || '<no-text>'};
                        }
                    }
                    return best;
                }""")
                if not btn:
                    return None
                self.log(f"  → 红色按钮兜底: 点 '{btn['text']}' @"
                         f"({int(btn['x'])},{int(btn['y'])})")
                self._human_click(btn["x"], btn["y"])
                return btn["text"]
            except Exception as e:
                self.log(f"  [dbg] 红色按钮点击失败: {e}")
                return None

        for step in range(5):
            self._check_stop()
            # 先滚到底（发布按钮通常在底部）
            try:
                self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            self._sleep(random.uniform(0.6, 1.2))

            clicked_kw = None
            poll_end = time.time() + 18
            while time.time() < poll_end:
                self._check_stop()
                # ① 最高优先：Shadow DOM 容器（XHS 真实发布按钮在闭合 Shadow Root 里）
                for kw in primary:
                    if kw == last_clicked:
                        continue
                    if _try_shadow_button_click(kw):
                        clicked_kw = kw
                        break
                if clicked_kw:
                    break
                # ② 次优先：常规 Playwright locator（推进按钮+其它发布关键字）
                for kw in all_kw:
                    if kw == last_clicked:
                        continue
                    if _try_playwright_click(kw):
                        clicked_kw = kw
                        break
                if clicked_kw:
                    break
                self._sleep(1)

            # ③ 文字全失败 → 红色背景兜底
            if not clicked_kw:
                self.log(f"  ⚠ 第{step+1}步: 文字未匹配，尝试红色按钮兜底")
                red_text = _try_red_button_click()
                if red_text:
                    clicked_kw = red_text if red_text in primary else "发布"
                    try:
                        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    except Exception:
                        pass

            if not clicked_kw:
                self.log(f"  ⚠ 第{step+1}步: 等18s仍未找到可点按钮")
                break
            last_clicked = clicked_kw
            is_final = (clicked_kw in primary) or (clicked_kw == "发布")
            self._sleep(5)

            # 检查发布成功
            try:
                if self.page.locator("text=发布成功").first.is_visible(timeout=1500):
                    self.log("  ✓ 发布成功")
                    return True
            except Exception:
                pass

            if is_final:
                self._sleep(3)
                try:
                    if self.page.locator("text=发布成功").first.is_visible(timeout=5000):
                        self.log("  ✓ 发布成功")
                        return True
                except Exception:
                    pass
                # 检查 URL 是否跳走（成功后通常跳转到管理页）
                try:
                    cur_url = self.page.url
                    if "publish" not in cur_url:
                        self.log(f"  ✓ 发布后跳转: {cur_url[:70]}")
                        published = True
                        break
                except Exception:
                    pass
                self.log("  ✓ 已点最终发布按钮（未抓到成功提示）")
                published = True
                break

        if not published:
            raise RuntimeError("找不到发布按钮（DOM 可能变了）")

        # 7) 等发布成功提示（兜底等更长）
        self._sleep(2)
        try:
            ok_sel = self.page.locator("text=发布成功").first
            if ok_sel.is_visible(timeout=8000):
                self.log("  ✓ 发布成功")
        except Exception:
            pass
        return True

    def close(self):
        try:
            if self.context: self.save_state()
        except Exception: pass
        try:
            if self.assigned_ip:
                release_pool_ip(self.assigned_ip)
                self.assigned_ip = None
        except Exception: pass
        for fn in (lambda: self.browser and self.browser.close(),
                   lambda: self.pw and self.pw.stop()):
            try: fn()
            except Exception: pass
