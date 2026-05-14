"""MySQL 数据层 - 操作历史 / 去重 / 笔记沉淀"""
import json
import threading
from pathlib import Path

try:
    import pymysql
    _HAS_PYMYSQL = True
except ImportError:
    _HAS_PYMYSQL = False

DB_CONFIG_FILE = Path(__file__).parent / "accounts" / "_db.json"
_local = threading.local()
_config_cache = None

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS actions (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        account VARCHAR(64) NOT NULL,
        action_type VARCHAR(32) NOT NULL,
        target_id VARCHAR(128) NOT NULL,
        target_url VARCHAR(1024),
        content TEXT,
        status VARCHAR(16) NOT NULL,
        error VARCHAR(512),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_acc_type_target (account, action_type, target_id),
        INDEX idx_created (created_at),
        INDEX idx_acc_date (account, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS notes (
        note_id VARCHAR(128) PRIMARY KEY,
        title VARCHAR(1024),
        author VARCHAR(128),
        author_id VARCHAR(128),
        liked_count VARCHAR(32),
        comment_count VARCHAR(32),
        collected_count VARCHAR(32),
        share_count VARCHAR(32),
        type VARCHAR(32),
        url VARCHAR(1024),
        cover VARCHAR(1024),
        first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_author (author_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS intent_users (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id VARCHAR(128),
        user_nickname VARCHAR(256),
        user_url VARCHAR(1024),
        intent_type VARCHAR(128),
        matched_keywords VARCHAR(512),
        comment TEXT,
        ip_location VARCHAR(64),
        note_id VARCHAR(128),
        note_title VARCHAR(1024),
        note_url VARCHAR(1024),
        found_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_user (user_id),
        INDEX idx_note (note_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


def load_db_config():
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if not DB_CONFIG_FILE.exists():
        _config_cache = {}
    else:
        try:
            _config_cache = json.loads(DB_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            _config_cache = {}
    return _config_cache


def save_db_config(cfg):
    global _config_cache
    DB_CONFIG_FILE.parent.mkdir(exist_ok=True)
    DB_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    _config_cache = cfg
    _local.conn = None


def is_enabled():
    cfg = load_db_config()
    return bool(_HAS_PYMYSQL and cfg.get("host") and cfg.get("database"))


def _connect(cfg):
    return pymysql.connect(
        host=cfg["host"],
        port=int(cfg.get("port", 3306)),
        user=cfg.get("user", "root"),
        password=cfg.get("password", ""),
        database=cfg["database"],
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=10,
    )


def get_conn():
    if not _HAS_PYMYSQL:
        return None
    cfg = load_db_config()
    if not cfg or not cfg.get("host"):
        return None
    conn = getattr(_local, "conn", None)
    if conn is None:
        try:
            _local.conn = _connect(cfg)
            return _local.conn
        except Exception as e:
            _local.conn = None
            raise RuntimeError(f"MySQL 连接失败: {e}")
    try:
        conn.ping(reconnect=True)
        return conn
    except Exception:
        _local.conn = None
        return get_conn()


def test_connection(cfg):
    if not _HAS_PYMYSQL:
        raise RuntimeError("pymysql 未安装，请先 pip install pymysql")
    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            v = cur.fetchone()[0]
        return v
    finally:
        conn.close()


def init_schema():
    conn = get_conn()
    if not conn:
        raise RuntimeError("DB 未连接")
    with conn.cursor() as cur:
        for sql in SCHEMA:
            cur.execute(sql)
    return True


# ---------------- actions ----------------
def has_action(account, action_type, target_id):
    """ 该账号是否对该目标成功执行过该类型操作（用于去重） """
    try:
        conn = get_conn()
        if not conn:
            return False
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM actions WHERE account=%s AND action_type=%s "
                "AND target_id=%s AND status='success' LIMIT 1",
                (account, action_type, target_id))
            return cur.fetchone() is not None
    except Exception:
        return False


def record_action(account, action_type, target_id, target_url="", content="",
                  status="success", error=""):
    try:
        conn = get_conn()
        if not conn:
            return
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO actions (account, action_type, target_id, target_url,
                content, status, error) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE target_url=VALUES(target_url),
                content=VALUES(content), status=VALUES(status),
                error=VALUES(error), created_at=NOW()""",
                (account, action_type, target_id, (target_url or "")[:1024],
                 content or "", status, (error or "")[:500]))
    except Exception:
        pass


def today_count(account, action_type=None):
    """ 当天累计 / 某类型当天累计 """
    try:
        conn = get_conn()
        if not conn:
            return 0
        with conn.cursor() as cur:
            if action_type:
                cur.execute(
                    """SELECT COUNT(*) FROM actions WHERE account=%s AND action_type=%s
                    AND status='success' AND DATE(created_at)=CURDATE()""",
                    (account, action_type))
            else:
                cur.execute(
                    """SELECT COUNT(*) FROM actions WHERE account=%s
                    AND status='success' AND DATE(created_at)=CURDATE()""",
                    (account,))
            return cur.fetchone()[0]
    except Exception:
        return 0


def today_stats(account):
    """ 返回 dict: {'like':5, 'comment':3, 'follow':2} """
    try:
        conn = get_conn()
        if not conn:
            return {}
        with conn.cursor() as cur:
            cur.execute(
                """SELECT action_type, COUNT(*) FROM actions
                WHERE account=%s AND status='success' AND DATE(created_at)=CURDATE()
                GROUP BY action_type""",
                (account,))
            return {r[0]: r[1] for r in cur.fetchall()}
    except Exception:
        return {}


# ---------------- notes / intent ----------------
def save_notes(rows):
    if not rows:
        return
    try:
        conn = get_conn()
        if not conn:
            return
        with conn.cursor() as cur:
            for r in rows:
                nid = r.get("note_id")
                if not nid:
                    continue
                cur.execute(
                    """INSERT INTO notes (note_id, title, author, author_id, liked_count,
                    comment_count, collected_count, share_count, type, url, cover)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE title=VALUES(title), author=VALUES(author),
                    liked_count=VALUES(liked_count), comment_count=VALUES(comment_count),
                    collected_count=VALUES(collected_count), last_seen=NOW()""",
                    (nid, str(r.get("title", ""))[:1024], r.get("author", ""),
                     r.get("author_id", ""),
                     str(r.get("liked_count", "")), str(r.get("comment_count", "")),
                     str(r.get("collected_count", "")), str(r.get("share_count", "")),
                     r.get("type", ""), str(r.get("url", ""))[:1024],
                     str(r.get("cover", ""))[:1024]))
    except Exception:
        pass


def save_intent_users(rows):
    if not rows:
        return
    try:
        conn = get_conn()
        if not conn:
            return
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """INSERT INTO intent_users (user_id, user_nickname, user_url,
                    intent_type, matched_keywords, comment, ip_location,
                    note_id, note_title, note_url)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (r.get("user_id", ""), r.get("user_nickname", ""),
                     r.get("user_url", ""), r.get("intent_type", ""),
                     r.get("matched_keywords", ""), r.get("comment", ""),
                     r.get("ip_location", ""), r.get("note_id", ""),
                     str(r.get("note_title", ""))[:1024], r.get("note_url", "")))
    except Exception:
        pass
