"""账号会话 - 每账号独占浏览器 + 工作线程"""
import collections
import queue
import threading
import time
import traceback

from scraper import XHSScraper, StoppedException


RISK_KEYWORDS = ["验证", "captcha", "频繁", "限流", "稍后", "blocked",
                 "Too Many", "请稍后再试", "操作异常", "异常访问"]


class AccountSession:
    def __init__(self, account_name, log_cb, proxy=None):
        self.account = account_name
        self.log_cb = log_cb
        self.proxy = proxy or ""
        self.task_q = queue.Queue()
        self.scraper = None
        self.busy = False
        self.started = False
        self.start_failed = False
        self.dead = False  # 浏览器被外部关闭后置 True
        self.logged_in = False
        # 启动就绪信号：避免 UI 派任务时浏览器还没 launch 完
        self.ready_event = threading.Event()
        # 自动化任务互斥：养号/搬运/定时调度等长任务进行时打开
        self.automation_running = False
        self.automation_label = ""
        self.automation_started_at = 0.0
        # 风控
        self.recent_ops = collections.deque(maxlen=30)
        self.alert = False
        self.alert_reason = ""
        self.alert_callback = None
        self.thread = threading.Thread(target=self._loop, daemon=True,
                                       name=f"sess-{account_name}")
        self.thread.start()

    def record_op(self, op_type, success, error=""):
        self.recent_ops.append({
            "t": time.time(), "type": op_type, "success": bool(success),
            "error": str(error or "")[:200],
        })
        self._check_alert()

    def _check_alert(self):
        if self.alert:
            return
        ops = list(self.recent_ops)
        if not ops:
            return
        # 1) 连续 3 次失败
        last3 = ops[-3:]
        if len(last3) == 3 and all(not o["success"] for o in last3):
            self._raise_alert("连续 3 次失败")
            return
        # 2) 60s 内失败率 > 50%（至少 5 次）
        now = time.time()
        win = [o for o in ops if now - o["t"] < 60]
        if len(win) >= 5:
            fr = sum(1 for o in win if not o["success"]) / len(win)
            if fr > 0.5:
                self._raise_alert(f"近60s失败率 {fr:.0%}")
                return
        # 3) 错误信息含风控关键词
        for o in ops[-5:]:
            if not o["success"]:
                err = o["error"]
                hit = next((k for k in RISK_KEYWORDS if k in err), None)
                if hit:
                    self._raise_alert(f"出现风控词「{hit}」")
                    return

    def _raise_alert(self, reason):
        self.alert = True
        self.alert_reason = reason
        self.log(f"🚨 风控警报: {reason}，已停止本号任务")
        try: self.scraper.request_stop()
        except Exception: pass
        try:
            while not self.task_q.empty():
                self.task_q.get_nowait()
        except Exception: pass
        if self.alert_callback:
            try: self.alert_callback(self.account, reason)
            except Exception: pass

    def clear_alert(self):
        self.alert = False
        self.alert_reason = ""
        self.recent_ops.clear()

    @property
    def nickname(self):
        if self.scraper:
            return self.scraper.user_nickname or ""
        return ""

    @property
    def alive(self):
        if self.dead or self.start_failed:
            return False
        return bool(self.scraper and self.scraper.is_alive())

    def log(self, msg):
        self.log_cb(self.account, msg)

    def _refresh_login_state(self):
        try:
            self.logged_in = bool(self.scraper and self.scraper.is_logged_in())
        except Exception:
            self.logged_in = False

    def _on_browser_dead(self):
        if self.dead:
            return
        self.dead = True
        self.started = False
        self.logged_in = False
        self.log("⚠ 浏览器已断开（被手动关闭？），请点 [启动并登录] 重启")
        try:
            self.task_q.put_nowait(None)
        except Exception:
            pass

    def _loop(self):
        try:
            self.scraper = XHSScraper(
                log_cb=lambda m: self.log_cb(self.account, m),
                account=self.account,
                proxy=self.proxy,
            )
            self.scraper.session = self  # 反向引用，方便任务回写 record_op
            self.scraper.start()
            # 注册浏览器断开监听
            try:
                self.scraper.browser.on("disconnected",
                                        lambda *_: self._on_browser_dead())
            except Exception:
                pass
            self.started = True
            self._refresh_login_state()
            if self.logged_in:
                try:
                    self.scraper.refresh_user_info()
                except Exception:
                    pass
            self.log("浏览器就绪"
                     + (f"（已登录: {self.nickname}）" if self.nickname else "（未登录）"))
            self.ready_event.set()
        except Exception as e:
            self.start_failed = True
            self.ready_event.set()  # 失败也要 set 让等待方解锁
            self.log(f"启动失败: {e}")
            return

        while True:
            item = self.task_q.get()
            if item is None:
                break
            if self.dead or not self.scraper.is_alive():
                self.log("✗ 浏览器已关闭，无法执行任务")
                continue
            fn, args = item
            self.busy = True
            self.scraper.clear_stop()
            try:
                fn(self.scraper, *args)
            except StoppedException:
                self.log("⏹ 已停止")
            except Exception as e:
                # 如果是浏览器关闭引起，标记 dead
                msg = str(e)
                if "Target page" in msg or "browser has been closed" in msg \
                        or "TargetClosedError" in msg:
                    self._on_browser_dead()
                else:
                    self.log(f"错误: {e}")
                    self.log(traceback.format_exc())
            finally:
                self.busy = False
                self._refresh_login_state()

        try:
            self.scraper.close()
        except Exception:
            pass

    def stop_task(self):
        if self.scraper:
            self.scraper.request_stop()

    def submit_automation(self, fn, args, label):
        """ 提交一个"自动化任务"。运行期间 automation_running=True，
            UI 派别的任务时可据此提示用户。 """
        def wrapped(scraper, *a):
            self.automation_running = True
            self.automation_label = label
            self.automation_started_at = time.time()
            try:
                fn(scraper, *a)
            finally:
                self.automation_running = False
                self.automation_label = ""
                self.automation_started_at = 0.0
        self.task_q.put((wrapped, args))

    def shutdown(self, join_timeout=None):
        """ 通知工作线程退出。
            join_timeout 给出时，会阻塞等待线程结束（保证 close()→save_state() 跑完）。
            playwright 对象有线程亲和性，必须在创建它的线程里 save_state，
            因此不能从主线程直接调 — 只能通过 join 等工作线程自己跑 close。"""
        if self.scraper:
            try: self.scraper.request_stop()
            except Exception: pass
        try: self.task_q.put_nowait(None)
        except Exception: pass
        if join_timeout and self.thread.is_alive():
            try: self.thread.join(timeout=join_timeout)
            except Exception: pass
