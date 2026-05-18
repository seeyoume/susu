"""绮绮采集器 授权服务器"""
import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path

from flask import (Flask, jsonify, request, send_file,
                   render_template, redirect, session, url_for)

DB_FILE = Path(__file__).parent / "license.db"

# 加载 .env 文件（如果存在，由 bootstrap.sh 自动生成）
_ENV_FILE = Path(__file__).parent / ".env"
if _ENV_FILE.exists():
    try:
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

# !!!! 部署前必须改成强随机字符串 !!!!
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "CHANGE_ME_TO_RANDOM_STRING_64CHARS")

PLAN_DAYS = {
    "day": 1, "week": 7, "month": 30,
    "quarter": 90, "year": 365,
}

# 短时长试用套餐：以秒为单位
PLAN_SECONDS = {
    "trial_5min":  5 * 60,
    "trial_15min": 15 * 60,
    "trial_30min": 30 * 60,
    "trial_1h":    60 * 60,
    "trial_3h":    3 * 60 * 60,
    "trial_6h":    6 * 60 * 60,
}

# 中文展示名（admin UI 用）
PLAN_LABELS = {
    "trial_5min":  "5分钟试用",
    "trial_15min": "15分钟试用",
    "trial_30min": "30分钟试用",
    "trial_1h":    "1小时试用",
    "trial_3h":    "3小时试用",
    "trial_6h":    "6小时试用",
    "day":         "日卡 (1天)",
    "week":        "周卡 (7天)",
    "month":       "月卡 (30天)",
    "quarter":     "季卡 (90天)",
    "year":        "年卡 (365天)",
}


def plan_duration_seconds(plan, custom_seconds=None):
    """统一计算套餐时长（秒）"""
    if plan == "custom" and custom_seconds:
        return max(60, int(custom_seconds))  # 最少 1 分钟
    if plan in PLAN_SECONDS:
        return PLAN_SECONDS[plan]
    if plan in PLAN_DAYS:
        return PLAN_DAYS[plan] * 86400
    return 0


def is_valid_plan(plan):
    return plan in PLAN_DAYS or plan in PLAN_SECONDS or plan == "custom"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", secrets.token_hex(32))
# 上传 exe 文件大小限制：默认 500MB，可通过环境变量调整
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "500")) * 1024 * 1024


# 把所有 HTTP 错误（413、500 等）统一返回 JSON，避免前端收到 HTML
from flask import Response as _Response
from werkzeug.exceptions import HTTPException as _HTTPException

@app.errorhandler(_HTTPException)
def _handle_http_exc(e):
    import json as _json
    body = _json.dumps({"ok": False, "msg": f"HTTP {e.code}: {e.name}"})
    return _Response(body, status=e.code, mimetype="application/json")

@app.errorhandler(Exception)
def _handle_generic_exc(e):
    import json as _json, traceback as _tb
    print(f"[unhandled] {e}\n{_tb.format_exc()}", flush=True)
    body = _json.dumps({"ok": False, "msg": f"服务器异常: {e}"})
    return _Response(body, status=500, mimetype="application/json")


def init_db():
    conn = sqlite3.connect(str(DB_FILE))
    conn.execute("""CREATE TABLE IF NOT EXISTS keys (
        key TEXT PRIMARY KEY,
        plan TEXT NOT NULL,
        days INTEGER NOT NULL,
        machine_id TEXT,
        activated_at INTEGER,
        expires_at INTEGER,
        status TEXT DEFAULT 'unused',
        note TEXT,
        created_at INTEGER NOT NULL,
        duration_seconds INTEGER DEFAULT 0
    )""")
    # 迁移：旧库没有 duration_seconds 列时补上
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(keys)").fetchall()]
        if "duration_seconds" not in cols:
            conn.execute("ALTER TABLE keys ADD COLUMN duration_seconds INTEGER DEFAULT 0")
    except Exception:
        pass
    conn.execute("""CREATE TABLE IF NOT EXISTS announces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        priority TEXT DEFAULT 'info',
        created_at INTEGER NOT NULL
    )""")
    # 系统配置表（存储默认 DeepSeek key 等）
    conn.execute("""CREATE TABLE IF NOT EXISTS sys_config (
        k TEXT PRIMARY KEY,
        v TEXT NOT NULL,
        updated_at INTEGER NOT NULL
    )""")
    # 客户端心跳/使用统计
    conn.execute("""CREATE TABLE IF NOT EXISTS client_stats (
        machine_id TEXT PRIMARY KEY,
        card_key TEXT,
        plan TEXT,
        last_ip TEXT,
        last_seen INTEGER,
        first_seen INTEGER,
        version TEXT,
        os_info TEXT,
        action_count INTEGER DEFAULT 0,
        ai_calls INTEGER DEFAULT 0,
        note TEXT
    )""")
    # 机器封禁表
    conn.execute("""CREATE TABLE IF NOT EXISTS blocked_machines (
        machine_id TEXT PRIMARY KEY,
        reason TEXT,
        blocked_at INTEGER NOT NULL,
        blocked_by TEXT
    )""")
    # 套餐级别限额（行为频率 / AI 调用次数）
    conn.execute("""CREATE TABLE IF NOT EXISTS plan_limits (
        plan TEXT PRIMARY KEY,
        action_delay_min INTEGER DEFAULT 30,
        action_delay_max INTEGER DEFAULT 90,
        daily_actions INTEGER DEFAULT 50,
        ai_daily INTEGER DEFAULT 100,
        max_accounts INTEGER DEFAULT 1
    )""")
    # 客户端版本管理
    conn.execute("""CREATE TABLE IF NOT EXISTS releases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version TEXT NOT NULL UNIQUE,
        filename TEXT NOT NULL,
        size INTEGER DEFAULT 0,
        sha256 TEXT,
        changelog TEXT,
        min_version TEXT DEFAULT '',
        force_update INTEGER DEFAULT 0,
        published_at INTEGER NOT NULL
    )""")
    # 初始化默认套餐限额
    for plan, (dmin, dmax, daily, ai_daily, max_acc) in {
        "day":     (60, 120, 30, 30, 1),
        "week":    (45, 100, 80, 100, 2),
        "month":   (30, 90, 200, 300, 3),
        "quarter": (20, 60, 500, 800, 5),
        "year":    (15, 45, 2000, 3000, 10),
    }.items():
        conn.execute("INSERT OR IGNORE INTO plan_limits VALUES (?, ?, ?, ?, ?, ?)",
                     (plan, dmin, dmax, daily, ai_daily, max_acc))
    conn.commit()
    conn.close()


# ---------- 通用工具 ----------
def get_client_ip():
    """ 宝塔 Nginx 反代后真实 IP """
    return (request.headers.get("X-Real-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "")


def get_config(k, default=""):
    conn = get_conn()
    row = conn.execute("SELECT v FROM sys_config WHERE k=?", (k,)).fetchone()
    return row["v"] if row else default


def set_config(k, v):
    conn = get_conn()
    conn.execute(
        "INSERT INTO sys_config(k,v,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
        (k, v, int(time.time())))
    conn.commit()


def is_machine_blocked(mid):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM blocked_machines WHERE machine_id=?", (mid,)).fetchone()
    return bool(row)


def record_client_heartbeat(mid, card_key, plan, version="", os_info=""):
    """ 写客户端心跳/统计 """
    conn = get_conn()
    now = int(time.time())
    ip = get_client_ip()
    row = conn.execute("SELECT first_seen FROM client_stats WHERE machine_id=?",
                       (mid,)).fetchone()
    if row:
        conn.execute(
            "UPDATE client_stats SET card_key=?, plan=?, last_ip=?, last_seen=?, "
            "version=?, os_info=? WHERE machine_id=?",
            (card_key, plan, ip, now, version, os_info, mid))
    else:
        conn.execute(
            "INSERT INTO client_stats(machine_id, card_key, plan, last_ip, "
            "last_seen, first_seen, version, os_info) VALUES (?,?,?,?,?,?,?,?)",
            (mid, card_key, plan, ip, now, now, version, os_info))
    conn.commit()


def get_conn():
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    return conn


def gen_key():
    raw = secrets.token_hex(8).upper()  # 16 hex chars
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


# ---------- 客户端接口 ----------
@app.route("/activate", methods=["POST"])
def activate():
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip().upper()
    mid = (body.get("machine_id") or "").strip()
    version = (body.get("version") or "").strip()
    os_info = (body.get("os") or "").strip()
    if not key or not mid:
        return jsonify(ok=False, msg="参数缺失"), 400
    # 机器封禁检测
    if is_machine_blocked(mid):
        return jsonify(ok=False, msg="该设备已被禁用，请联系客服"), 403
    conn = get_conn()
    row = conn.execute("SELECT * FROM keys WHERE key=?", (key,)).fetchone()
    if not row:
        return jsonify(ok=False, msg="卡密无效"), 404
    if row["status"] == "revoked":
        return jsonify(ok=False, msg="此卡密已被禁用"), 403
    if row["status"] == "used":
        # 已绑定其他设备 → 拒绝；machine_id 被管理员清空（解绑）→ 允许接管，保留原到期时间
        if not row["machine_id"]:
            if row["expires_at"] and int(time.time()) > row["expires_at"]:
                return jsonify(ok=False, msg="此卡密已过期"), 403
            conn.execute("UPDATE keys SET machine_id=? WHERE key=?", (mid, key))
            conn.commit()
            record_client_heartbeat(mid, key, row["plan"], version, os_info)
            return jsonify(ok=True, expires_at=row["expires_at"], plan=row["plan"])
        if row["machine_id"] != mid:
            return jsonify(ok=False, msg="此卡密已绑定其他设备"), 403
        record_client_heartbeat(mid, key, row["plan"], version, os_info)
        return jsonify(ok=True, expires_at=row["expires_at"], plan=row["plan"])
    now = int(time.time())
    # 秒级时长优先（试用卡密），否则用天数
    try:
        ds = row["duration_seconds"]
    except (KeyError, IndexError):
        ds = 0
    duration = ds if ds and ds > 0 else (row["days"] * 86400)
    expires = now + duration
    conn.execute(
        "UPDATE keys SET machine_id=?, activated_at=?, expires_at=?, status='used' WHERE key=?",
        (mid, now, expires, key),
    )
    conn.commit()
    record_client_heartbeat(mid, key, row["plan"], version, os_info)
    return jsonify(ok=True, expires_at=expires, plan=row["plan"])


@app.route("/validate", methods=["POST"])
def validate():
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip().upper()
    mid = (body.get("machine_id") or "").strip()
    version = (body.get("version") or "").strip()
    os_info = (body.get("os") or "").strip()
    conn = get_conn()
    # 机器封禁
    if is_machine_blocked(mid):
        return jsonify(ok=False, msg="该设备已被禁用，请联系客服")
    row = conn.execute("SELECT * FROM keys WHERE key=?", (key,)).fetchone()
    if not row:
        return jsonify(ok=False, msg="卡密不存在")
    if row["status"] == "revoked":
        return jsonify(ok=False, msg="卡密已被禁用")
    if row["machine_id"] != mid:
        return jsonify(ok=False, msg="设备不匹配")
    if int(time.time()) > (row["expires_at"] or 0):
        return jsonify(ok=False, msg="授权已过期")
    record_client_heartbeat(mid, key, row["plan"], version, os_info)
    # 同时返回套餐限额（客户端用来做延时/上限控制）
    limit = conn.execute(
        "SELECT * FROM plan_limits WHERE plan=?", (row["plan"],)).fetchone()
    limits = dict(limit) if limit else {}
    return jsonify(ok=True, expires_at=row["expires_at"], plan=row["plan"],
                   limits=limits)


# ---------- 默认 DeepSeek Key 下发 ----------
@app.route("/ai/default_key", methods=["POST"])
def ai_default_key():
    """ 客户端请求获取系统级默认 AI Key。
        需要传 card key + machine_id 校验授权才下发。 """
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip().upper()
    mid = (body.get("machine_id") or "").strip()
    if not key or not mid:
        return jsonify(ok=False, msg="参数缺失"), 400
    if is_machine_blocked(mid):
        return jsonify(ok=False, msg="该设备已被禁用"), 403
    conn = get_conn()
    row = conn.execute("SELECT * FROM keys WHERE key=?", (key,)).fetchone()
    if not row or row["status"] == "revoked":
        return jsonify(ok=False, msg="授权无效"), 403
    if row["machine_id"] != mid:
        return jsonify(ok=False, msg="设备不匹配"), 403
    if int(time.time()) > (row["expires_at"] or 0):
        return jsonify(ok=False, msg="授权已过期"), 403
    default_key = get_config("default_ai_key", "")
    default_model = get_config("default_ai_model", "deepseek-chat")
    default_base = get_config("default_ai_base_url",
                              "https://api.deepseek.com/v1/chat/completions")
    if not default_key:
        return jsonify(ok=False, msg="管理员尚未配置默认 AI Key")
    # 统计 AI 调用
    try:
        conn.execute("UPDATE client_stats SET ai_calls = COALESCE(ai_calls, 0) + 1 "
                     "WHERE machine_id=?", (mid,))
        conn.commit()
    except Exception:
        pass
    return jsonify(ok=True,
                   api_key=default_key,
                   model=default_model,
                   base_url=default_base)


# ---------- 客户端版本检查 + 升级下载 ----------
RELEASES_DIR = Path(__file__).parent / "releases"
RELEASES_DIR.mkdir(exist_ok=True)


def _ver_tuple(v):
    """ 'v2.6' -> (2, 6)  容错处理 """
    s = v.strip().lstrip("vV")
    parts = []
    for x in s.split("."):
        try: parts.append(int(x))
        except Exception: parts.append(0)
    return tuple(parts)


@app.route("/client/version", methods=["GET"])
def client_version():
    """ 客户端启动 / 定时检查时调用，返回最新版本信息 """
    cur = (request.args.get("current") or "").strip()
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM releases ORDER BY published_at DESC LIMIT 1").fetchone()
    if not row:
        return jsonify(ok=True, has_update=False)
    latest = row["version"]
    has_update = False
    if cur:
        try:
            has_update = _ver_tuple(latest) > _ver_tuple(cur)
        except Exception:
            has_update = (latest != cur)
    force = bool(row["force_update"])
    # 强制升级阈值
    if cur and row["min_version"]:
        try:
            if _ver_tuple(cur) < _ver_tuple(row["min_version"]):
                force = True
        except Exception:
            pass
    return jsonify(
        ok=True,
        has_update=has_update,
        latest_version=latest,
        download_url=f"/client/download/{row['filename']}",
        size=row["size"],
        sha256=row["sha256"],
        changelog=row["changelog"] or "",
        force=force,
    )


@app.route("/client/download/<filename>", methods=["GET"])
def client_download(filename):
    """ 下载 exe 安装包 """
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify(ok=False), 400
    p = RELEASES_DIR / filename
    if not p.exists():
        return jsonify(ok=False, msg="文件不存在"), 404
    return send_file(str(p), as_attachment=True,
                      mimetype="application/octet-stream")


# ---------- 客户端心跳上报（用户使用 IP / 行为统计） ----------
@app.route("/client/heartbeat", methods=["POST"])
def client_heartbeat():
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip().upper()
    mid = (body.get("machine_id") or "").strip()
    version = (body.get("version") or "").strip()
    os_info = (body.get("os") or "").strip()
    action_delta = int(body.get("action_delta", 0))
    if not key or not mid:
        return jsonify(ok=False), 400
    if is_machine_blocked(mid):
        return jsonify(ok=False, msg="blocked", blocked=True)
    conn = get_conn()
    row = conn.execute("SELECT plan FROM keys WHERE key=?", (key,)).fetchone()
    plan = row["plan"] if row else ""
    record_client_heartbeat(mid, key, plan, version, os_info)
    if action_delta > 0:
        conn.execute(
            "UPDATE client_stats SET action_count=COALESCE(action_count,0)+? "
            "WHERE machine_id=?", (action_delta, mid))
        conn.commit()
    return jsonify(ok=True)


# ---------- 管理接口 ----------
def _check_admin():
    return request.headers.get("X-Admin-Token", "") == ADMIN_TOKEN


@app.route("/admin/create", methods=["POST"])
def admin_create():
    if not _check_admin():
        return jsonify(ok=False, msg="无权访问"), 401
    body = request.get_json(silent=True) or {}
    plan = body.get("plan", "month")
    count = int(body.get("count", 1))
    note = body.get("note", "")
    custom_seconds = body.get("custom_seconds")
    if not is_valid_plan(plan):
        return jsonify(ok=False, msg="无效套餐"), 400
    duration_sec = plan_duration_seconds(plan, custom_seconds)
    if duration_sec <= 0:
        return jsonify(ok=False, msg="时长无效"), 400
    days = max(1, duration_sec // 86400)  # 兼容旧列，至少 1
    now = int(time.time())
    conn = get_conn()
    keys = []
    for _ in range(max(1, min(count, 500))):
        k = gen_key()
        conn.execute(
            "INSERT INTO keys(key,plan,days,note,created_at,duration_seconds) "
            "VALUES(?,?,?,?,?,?)",
            (k, plan, days, note, now, duration_sec),
        )
        keys.append(k)
    conn.commit()
    return jsonify(ok=True, keys=keys, plan=plan,
                   duration_seconds=duration_sec, days=days)


@app.route("/admin/list", methods=["GET"])
def admin_list():
    if not _check_admin():
        return jsonify(ok=False), 401
    status = request.args.get("status", "")
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM keys WHERE status=? ORDER BY created_at DESC LIMIT 500",
            (status,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM keys ORDER BY created_at DESC LIMIT 500").fetchall()
    return jsonify(ok=True, count=len(rows), keys=[dict(r) for r in rows])


@app.route("/admin/revoke", methods=["POST"])
def admin_revoke():
    if not _check_admin():
        return jsonify(ok=False), 401
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").upper()
    conn = get_conn()
    conn.execute("UPDATE keys SET status='revoked' WHERE key=?", (key,))
    conn.commit()
    return jsonify(ok=True)


@app.route("/admin/stats", methods=["GET"])
def admin_stats():
    if not _check_admin():
        return jsonify(ok=False), 401
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
    used = conn.execute("SELECT COUNT(*) FROM keys WHERE status='used'").fetchone()[0]
    unused = conn.execute("SELECT COUNT(*) FROM keys WHERE status='unused'").fetchone()[0]
    revoked = conn.execute("SELECT COUNT(*) FROM keys WHERE status='revoked'").fetchone()[0]
    active = conn.execute(
        "SELECT COUNT(*) FROM keys WHERE status='used' AND expires_at > ?",
        (int(time.time()),)).fetchone()[0]
    return jsonify(ok=True, total=total, used=used, unused=unused,
                   revoked=revoked, active=active)


# ---------- 签名 JS 自动更新 ----------
SIGN_JS_DIR = Path(__file__).parent / "sign_js"


@app.route("/sign/js/manifest", methods=["GET"])
def sign_js_manifest():
    """ 客户端启动时拉取，比较版本决定是否更新 """
    if not SIGN_JS_DIR.exists():
        return jsonify(version="0", files={})
    files = {}
    latest_mtime = 0
    for p in sorted(SIGN_JS_DIR.glob("*.js")):
        data = p.read_bytes()
        files[p.name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
        latest_mtime = max(latest_mtime, p.stat().st_mtime)
    return jsonify(version=str(int(latest_mtime)), files=files)


@app.route("/sign/js/file/<filename>", methods=["GET"])
def sign_js_file(filename):
    """ 下载指定 JS 文件 """
    if not filename.endswith(".js") or "/" in filename or "\\" in filename or ".." in filename:
        return jsonify(ok=False), 400
    p = SIGN_JS_DIR / filename
    if not p.exists():
        return jsonify(ok=False), 404
    return send_file(str(p), mimetype="application/javascript")


# ---------- 公告（客户端轮询） ----------
@app.route("/announce/latest", methods=["GET"])
def announce_latest():
    """ 客户端启动时调，传 since_id（已读最大ID），返回比它新的所有公告 """
    try:
        since = int(request.args.get("since", "0"))
    except ValueError:
        since = 0
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM announces WHERE id > ? ORDER BY id DESC LIMIT 10",
        (since,)).fetchall()
    return jsonify(ok=True, list=[dict(r) for r in rows])


# ---------- 管理后台 ----------
def _need_login():
    return not session.get("admin")


@app.route("/admin", methods=["GET"])
def admin_home():
    if _need_login():
        return redirect("/admin/login")
    return render_template("admin.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        token = request.form.get("token", "").strip()
        if token and token == ADMIN_TOKEN:
            session["admin"] = True
            return redirect("/admin")
        return render_template("login.html", err="Token 错误")
    return render_template("login.html", err=None)


@app.route("/admin/logout", methods=["GET"])
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin/login")


def _need_api_login():
    if _need_login():
        return jsonify(ok=False, msg="not authenticated"), 401
    return None


@app.route("/admin/api/stats", methods=["GET"])
def admin_api_stats():
    fail = _need_api_login()
    if fail: return fail
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
    used = conn.execute("SELECT COUNT(*) FROM keys WHERE status='used'").fetchone()[0]
    unused = conn.execute("SELECT COUNT(*) FROM keys WHERE status='unused'").fetchone()[0]
    revoked = conn.execute("SELECT COUNT(*) FROM keys WHERE status='revoked'").fetchone()[0]
    active = conn.execute(
        "SELECT COUNT(*) FROM keys WHERE status='used' AND expires_at > ?",
        (int(time.time()),)).fetchone()[0]
    return jsonify(ok=True, total=total, used=used, unused=unused,
                   revoked=revoked, active=active)


@app.route("/admin/api/keys", methods=["GET"])
def admin_api_keys():
    fail = _need_api_login()
    if fail: return fail
    status = request.args.get("status", "")
    limit = int(request.args.get("limit", "500"))
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM keys WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (status, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM keys ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return jsonify(ok=True, keys=[dict(r) for r in rows])


@app.route("/admin/api/keys/create", methods=["POST"])
def admin_api_keys_create():
    fail = _need_api_login()
    if fail: return fail
    body = request.get_json(silent=True) or {}
    plan = body.get("plan", "month")
    count = int(body.get("count", 1))
    note = body.get("note", "")
    custom_seconds = body.get("custom_seconds")
    if not is_valid_plan(plan):
        return jsonify(ok=False, msg="无效套餐"), 400
    duration_sec = plan_duration_seconds(plan, custom_seconds)
    if duration_sec <= 0:
        return jsonify(ok=False, msg="时长无效"), 400
    days = max(1, duration_sec // 86400)
    now = int(time.time())
    conn = get_conn()
    keys = []
    for _ in range(max(1, min(count, 500))):
        k = gen_key()
        conn.execute(
            "INSERT INTO keys(key,plan,days,note,created_at,duration_seconds) "
            "VALUES(?,?,?,?,?,?)",
            (k, plan, days, note, now, duration_sec))
        keys.append(k)
    conn.commit()
    return jsonify(ok=True, keys=keys, duration_seconds=duration_sec)


@app.route("/admin/api/plans", methods=["GET"])
def admin_api_plans():
    """前端获取套餐选项列表"""
    fail = _need_api_login()
    if fail: return fail
    items = []
    # 试用套餐放最上面
    for k in ["trial_5min", "trial_15min", "trial_30min",
              "trial_1h", "trial_3h", "trial_6h"]:
        items.append({"value": k, "label": PLAN_LABELS[k],
                      "seconds": PLAN_SECONDS[k], "is_trial": True})
    for k in ["day", "week", "month", "quarter", "year"]:
        items.append({"value": k, "label": PLAN_LABELS[k],
                      "seconds": PLAN_DAYS[k] * 86400, "is_trial": False})
    items.append({"value": "custom", "label": "🛠 自定义时长",
                  "seconds": 0, "is_trial": False})
    return jsonify(ok=True, plans=items)


@app.route("/admin/api/keys/revoke", methods=["POST"])
def admin_api_keys_revoke():
    fail = _need_api_login()
    if fail: return fail
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").upper()
    conn = get_conn()
    conn.execute("UPDATE keys SET status='revoked' WHERE key=?", (key,))
    conn.commit()
    return jsonify(ok=True)


@app.route("/admin/api/keys/unbind", methods=["POST"])
def admin_api_keys_unbind():
    """ 解绑卡密：清掉 machine_id，保留 status='used' 和原到期时间。
        客户拿同一张卡密在新机器上可以直接重新激活，剩余有效期不变。 """
    fail = _need_api_login()
    if fail: return fail
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").upper()
    conn = get_conn()
    row = conn.execute("SELECT status FROM keys WHERE key=?", (key,)).fetchone()
    if not row:
        return jsonify(ok=False, msg="卡密不存在"), 404
    if row["status"] == "revoked":
        return jsonify(ok=False, msg="卡密已禁用，无法解绑"), 400
    conn.execute("UPDATE keys SET machine_id=NULL WHERE key=?", (key,))
    conn.commit()
    return jsonify(ok=True)


@app.route("/admin/api/announces", methods=["GET"])
def admin_api_announces():
    fail = _need_api_login()
    if fail: return fail
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM announces ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify(ok=True, list=[dict(r) for r in rows])


@app.route("/admin/api/announce", methods=["POST"])
def admin_api_announce():
    fail = _need_api_login()
    if fail: return fail
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    prio = body.get("priority", "info")
    if not title or not content:
        return jsonify(ok=False, msg="标题/内容必填"), 400
    if prio not in ("info", "warn", "urgent"):
        prio = "info"
    conn = get_conn()
    conn.execute(
        "INSERT INTO announces(title,content,priority,created_at) VALUES(?,?,?,?)",
        (title, content, prio, int(time.time())))
    conn.commit()
    return jsonify(ok=True)


@app.route("/admin/api/announce/delete", methods=["POST"])
def admin_api_announce_delete():
    fail = _need_api_login()
    if fail: return fail
    body = request.get_json(silent=True) or {}
    aid = int(body.get("id", 0))
    conn = get_conn()
    conn.execute("DELETE FROM announces WHERE id=?", (aid,))
    conn.commit()
    return jsonify(ok=True)


# ---------- 默认 AI Key 管理 ----------
@app.route("/admin/api/ai_config", methods=["GET"])
def admin_api_ai_config_get():
    fail = _need_api_login()
    if fail: return fail
    return jsonify(ok=True,
                   api_key=get_config("default_ai_key", ""),
                   model=get_config("default_ai_model", "deepseek-chat"),
                   base_url=get_config("default_ai_base_url",
                                        "https://api.deepseek.com/v1/chat/completions"))


@app.route("/admin/api/ai_config", methods=["POST"])
def admin_api_ai_config_set():
    fail = _need_api_login()
    if fail: return fail
    body = request.get_json(silent=True) or {}
    api_key = (body.get("api_key") or "").strip()
    model = (body.get("model") or "deepseek-chat").strip()
    base_url = (body.get("base_url") or
                "https://api.deepseek.com/v1/chat/completions").strip()
    set_config("default_ai_key", api_key)
    set_config("default_ai_model", model)
    set_config("default_ai_base_url", base_url)
    return jsonify(ok=True)


# ---------- 客户端统计列表 ----------
@app.route("/admin/api/clients", methods=["GET"])
def admin_api_clients():
    fail = _need_api_login()
    if fail: return fail
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM client_stats ORDER BY last_seen DESC LIMIT 500").fetchall()
    now = int(time.time())
    out = []
    for r in rows:
        d = dict(r)
        d["last_seen_human"] = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_seen"]))
            if r["last_seen"] else "")
        d["online"] = (now - (r["last_seen"] or 0)) < 600  # 10分钟内活跃
        d["blocked"] = bool(conn.execute(
            "SELECT 1 FROM blocked_machines WHERE machine_id=?",
            (r["machine_id"],)).fetchone())
        out.append(d)
    return jsonify(ok=True, list=out)


# ---------- 机器封禁管理 ----------
@app.route("/admin/api/block", methods=["POST"])
def admin_api_block():
    fail = _need_api_login()
    if fail: return fail
    body = request.get_json(silent=True) or {}
    mid = (body.get("machine_id") or "").strip()
    reason = (body.get("reason") or "管理员手动封禁").strip()
    if not mid:
        return jsonify(ok=False, msg="machine_id 必填"), 400
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO blocked_machines(machine_id,reason,blocked_at,blocked_by) "
        "VALUES (?,?,?,?)",
        (mid, reason, int(time.time()), "admin"))
    conn.commit()
    return jsonify(ok=True)


@app.route("/admin/api/unblock", methods=["POST"])
def admin_api_unblock():
    fail = _need_api_login()
    if fail: return fail
    body = request.get_json(silent=True) or {}
    mid = (body.get("machine_id") or "").strip()
    if not mid:
        return jsonify(ok=False), 400
    conn = get_conn()
    conn.execute("DELETE FROM blocked_machines WHERE machine_id=?", (mid,))
    conn.commit()
    return jsonify(ok=True)


@app.route("/admin/api/blocked", methods=["GET"])
def admin_api_blocked_list():
    fail = _need_api_login()
    if fail: return fail
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM blocked_machines ORDER BY blocked_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["blocked_at_human"] = time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(r["blocked_at"]))
        out.append(d)
    return jsonify(ok=True, list=out)


# ---------- 套餐限额管理 ----------
@app.route("/admin/api/plan_limits", methods=["GET"])
def admin_api_plan_limits_get():
    fail = _need_api_login()
    if fail: return fail
    conn = get_conn()
    rows = conn.execute("SELECT * FROM plan_limits").fetchall()
    return jsonify(ok=True, list=[dict(r) for r in rows])


# ---------- 客户端版本管理（后台） ----------
@app.route("/admin/api/releases", methods=["GET"])
def admin_api_releases_list():
    fail = _need_api_login()
    if fail: return fail
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM releases ORDER BY published_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["published_at_human"] = time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(r["published_at"]))
        out.append(d)
    return jsonify(ok=True, list=out)


@app.route("/admin/api/releases/upload", methods=["POST"])
def admin_api_releases_upload():
    fail = _need_api_login()
    if fail: return fail
    import traceback
    try:
        f = request.files.get("file")
        version = (request.form.get("version") or "").strip()
        changelog = (request.form.get("changelog") or "").strip()
        min_version = (request.form.get("min_version") or "").strip()
        force_update = int(request.form.get("force_update", "0"))
        if not f or not version:
            return jsonify(ok=False, msg="文件 / 版本号必填"), 400
        if not version.startswith("v"):
            version = "v" + version

        # 确保目录存在 + 可写
        RELEASES_DIR.mkdir(parents=True, exist_ok=True)
        if not os.access(str(RELEASES_DIR), os.W_OK):
            return jsonify(ok=False,
                           msg=f"上传目录无写入权限: {RELEASES_DIR}"), 500

        filename = f"QiQiCollector_{version.replace('.', '_')}.exe"
        save_path = RELEASES_DIR / filename

        # 重复版本预检（避免上传完才报错浪费时间）
        conn = get_conn()
        exists = conn.execute(
            "SELECT 1 FROM releases WHERE version=?", (version,)).fetchone()
        if exists:
            return jsonify(ok=False, msg="该版本号已存在"), 400

        # 流式保存到磁盘
        f.save(str(save_path))
        size = save_path.stat().st_size
        if size == 0:
            try: save_path.unlink()
            except Exception: pass
            return jsonify(ok=False, msg="上传文件为空"), 400

        # 流式计算 SHA256（不再 read_bytes 整个读入内存）
        sha = hashlib.sha256()
        with open(save_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                sha.update(chunk)
        sha_hex = sha.hexdigest()

        try:
            conn.execute(
                "INSERT INTO releases(version, filename, size, sha256, changelog, "
                "min_version, force_update, published_at) VALUES (?,?,?,?,?,?,?,?)",
                (version, filename, size, sha_hex, changelog, min_version,
                 force_update, int(time.time())))
            conn.commit()
        except sqlite3.IntegrityError:
            try: save_path.unlink()
            except Exception: pass
            return jsonify(ok=False, msg="该版本号已存在"), 400
        return jsonify(ok=True, filename=filename, sha256=sha_hex, size=size)
    except Exception as e:
        # 把异常细节回传，避免 500 黑盒
        tb = traceback.format_exc()
        try:
            print(f"[releases/upload] 异常: {e}\n{tb}", flush=True)
        except Exception:
            pass
        return jsonify(ok=False, msg=f"服务器异常: {e}"), 500


@app.route("/admin/api/releases/delete", methods=["POST"])
def admin_api_releases_delete():
    fail = _need_api_login()
    if fail: return fail
    body = request.get_json(silent=True) or {}
    rid = int(body.get("id", 0))
    conn = get_conn()
    row = conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()
    if row:
        try:
            (RELEASES_DIR / row["filename"]).unlink(missing_ok=True)
        except Exception:
            pass
    conn.execute("DELETE FROM releases WHERE id=?", (rid,))
    conn.commit()
    return jsonify(ok=True)


@app.route("/admin/api/plan_limits", methods=["POST"])
def admin_api_plan_limits_set():
    fail = _need_api_login()
    if fail: return fail
    body = request.get_json(silent=True) or {}
    plan = body.get("plan", "")
    if plan not in PLAN_DAYS:
        return jsonify(ok=False, msg="无效套餐"), 400
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO plan_limits "
        "(plan, action_delay_min, action_delay_max, daily_actions, ai_daily, max_accounts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (plan,
         int(body.get("action_delay_min", 30)),
         int(body.get("action_delay_max", 90)),
         int(body.get("daily_actions", 50)),
         int(body.get("ai_daily", 100)),
         int(body.get("max_accounts", 1))))
    conn.commit()
    return jsonify(ok=True)


@app.route("/", methods=["GET"])
def index():
    return redirect("/admin")


# 无论是 `python app.py` 还是 gunicorn/supervisor 启动，都执行建表
try:
    init_db()
except Exception as _e:
    print(f"[init_db] 警告: {_e}", flush=True)

if __name__ == "__main__":
    if ADMIN_TOKEN.startswith("CHANGE_ME"):
        print("=" * 50)
        print("⚠️  ADMIN_TOKEN 还是默认值，部署前请改！")
        print("    设置环境变量 ADMIN_TOKEN=<你的随机串>")
        print("=" * 50)
    app.run(host="0.0.0.0", port=5000)
