"""绮绮采集器 - 简化版定时任务
仅支持 "daily HH:MM" 调度（每天指定时间触发一次）
"""
import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

SCHED_FILE = Path(__file__).parent / "accounts" / "_scheduled.json"


def _load():
    if not SCHED_FILE.exists():
        return []
    try:
        return json.loads(SCHED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(tasks):
    SCHED_FILE.parent.mkdir(exist_ok=True)
    SCHED_FILE.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_tasks():
    return _load()


def add_task(name, account, task_type, params, schedule, enabled=True):
    tasks = _load()
    tasks.append({
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "account": account,
        "type": task_type,         # 'nurture' / 'search_like' / 'follow_authors'
        "params": params,          # dict
        "schedule": schedule,      # 'daily HH:MM'
        "enabled": enabled,
        "last_run": "",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    _save(tasks)
    return tasks[-1]


def update_task(tid, **patch):
    tasks = _load()
    for t in tasks:
        if t["id"] == tid:
            t.update(patch)
            break
    _save(tasks)


def delete_task(tid):
    tasks = [t for t in _load() if t["id"] != tid]
    _save(tasks)


def _should_run(task, now):
    if not task.get("enabled", True):
        return False
    sched = task.get("schedule", "")
    if not sched.startswith("daily "):
        return False
    try:
        hm = sched.split(" ", 1)[1]
        hour, minute = map(int, hm.split(":"))
    except Exception:
        return False
    today = now.strftime("%Y-%m-%d")
    last = task.get("last_run", "") or ""
    if last.startswith(today):
        return False  # 今天跑过了
    # 当前时间 >= 计划时间
    if (now.hour, now.minute) >= (hour, minute):
        return True
    return False


class Scheduler:
    def __init__(self, runner_cb, log_cb=None):
        """ runner_cb(task): 触发任务时的回调（在调度线程里调用）
            log_cb(msg): 写日志（可选） """
        self.runner = runner_cb
        self.log = log_cb or (lambda m: None)
        self.thread = None
        self.running = False

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True,
                                       name="scheduler")
        self.thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            try:
                now = datetime.now()
                tasks = _load()
                changed = False
                for t in tasks:
                    if _should_run(t, now):
                        try:
                            self.log(f"⏰ 触发定时任务: {t['name']} ({t['account']})")
                            self.runner(t)
                            t["last_run"] = now.strftime("%Y-%m-%d %H:%M")
                            changed = True
                        except Exception as e:
                            self.log(f"⏰ 定时任务异常 [{t['name']}]: {e}")
                if changed:
                    _save(tasks)
            except Exception:
                pass
            # 30 秒检查一次
            for _ in range(30):
                if not self.running:
                    return
                time.sleep(1)


TASK_TYPES = {
    "nurture":        "🌱 自动养号",
    "search_like":    "🔍 搜索+点赞 Top N",
    "follow_authors": "👤 关注关键词作者",
}
