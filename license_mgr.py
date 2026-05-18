"""绮绮采集器 - 授权激活客户端"""
import hashlib
import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

# 部署服务器后，把 URL 写到这个文件里（与 exe 同目录）
SERVER_FILE = Path(__file__).parent / "license_server.txt"
DEFAULT_SERVER = "http://127.0.0.1:5000"

# 本地授权数据藏在用户目录（不显眼）
LICENSE_FILE = Path(os.path.expanduser("~")) / ".qiqi_license.dat"
GRACE_DAYS = 3  # 离线宽限天数

# 客户端版本（与 UI 显示一致）
CLIENT_VERSION = "v2.9"

# subprocess 调用 wmic/powershell 时不弹黑窗（exe 模式下需要）
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def get_server_url():
    if SERVER_FILE.exists():
        try:
            url = SERVER_FILE.read_text(encoding="utf-8").strip()
            if url:
                return url.rstrip("/")
        except Exception:
            pass
    return DEFAULT_SERVER


def _read_machine_guid():
    """读 HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid。
    这个值由 Windows 安装时生成，除非重装系统/重新封装镜像，否则永远不变。
    是当前 Windows 上最稳定的机器标识。"""
    if platform.system() != "Windows":
        return ""
    try:
        import winreg
        # 强制走 64 位视图，避免 32 位进程看到 WOW6432Node 镜像
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as k:
            guid, _ = winreg.QueryValueEx(k, "MachineGuid")
            return str(guid).strip()
    except Exception:
        return ""


def _read_bios_uuid():
    """读主板/BIOS UUID。Win11 24H2+ 已移除 wmic，先尝试 PowerShell CIM。"""
    if platform.system() != "Windows":
        return ""
    cmds = [
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "(Get-CimInstance -ClassName Win32_ComputerSystemProduct).UUID"],
        ["wmic", "csproduct", "get", "UUID"],
    ]
    for cmd in cmds:
        try:
            r = subprocess.check_output(
                cmd, stderr=subprocess.DEVNULL, timeout=8,
                creationflags=_NO_WINDOW,
            ).decode("utf-8", errors="ignore")
            for line in r.splitlines():
                line = line.strip()
                if not line or line.upper() == "UUID":
                    continue
                low = line.lower()
                if low in ("ffffffff-ffff-ffff-ffff-ffffffffffff",
                           "00000000-0000-0000-0000-000000000000",
                           "not available", "not specified", "none"):
                    continue
                return line
        except Exception:
            continue
    return ""


def get_machine_id():
    """生成稳定的机器唯一标识。

    优先级：MachineGuid（注册表）> BIOS UUID（CIM/wmic）。
    不使用 uuid.getnode() — 它会因 MAC 随机化、虚拟网卡、网卡禁用而漂移，
    拿不到时还会返回随机值（CPython 文档明确说明）。"""
    parts = []
    if platform.system() == "Windows":
        guid = _read_machine_guid()
        if guid:
            parts.append("mg:" + guid)
        bios = _read_bios_uuid()
        if bios:
            parts.append("bu:" + bios)
    else:
        try:
            with open("/etc/machine-id", "r") as f:
                mid = f.read().strip()
                if mid:
                    parts.append("mi:" + mid)
        except Exception:
            pass
    if not parts:
        # 应当极少触发；MachineGuid 不可读说明环境异常
        parts.append("node:" + platform.node())
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def get_machine_id_legacy():
    """旧版机器码算法（v2.6 及之前）。仅用于校验已激活老用户的本地授权文件。
    依赖 wmic + uuid.getnode()，在 Win11 24H2+ 和多网卡环境下不稳，已不再用于新激活。"""
    parts = []
    if platform.system() == "Windows":
        try:
            r = subprocess.check_output(
                ["wmic", "csproduct", "get", "UUID"],
                stderr=subprocess.DEVNULL, timeout=5,
                creationflags=_NO_WINDOW,
            ).decode("utf-8", errors="ignore")
            for line in r.splitlines():
                line = line.strip()
                if line and line.upper() != "UUID":
                    parts.append(line); break
        except Exception:
            pass
    try:
        parts.append(f"{uuid.getnode():012x}")
    except Exception:
        pass
    if not parts:
        parts.append(platform.node())
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def load_license():
    if not LICENSE_FILE.exists():
        return None
    try:
        return json.loads(LICENSE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_license(data):
    LICENSE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def clear_license():
    try:
        if LICENSE_FILE.exists():
            LICENSE_FILE.unlink()
    except Exception:
        pass


def _post(path, body, timeout=10):
    url = get_server_url() + path
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _os_info():
    try:
        return f"{platform.system()} {platform.release()}"
    except Exception:
        return ""


def activate(card_key):
    mid = get_machine_id()
    result = _post("/activate", {
        "key": card_key.strip().upper(),
        "machine_id": mid,
        "version": CLIENT_VERSION,
        "os": _os_info(),
    })
    if not result.get("ok"):
        raise RuntimeError(result.get("msg", "激活失败"))
    data = {
        "key": card_key.strip().upper(),
        "machine_id": mid,
        "expires_at": int(result["expires_at"]),
        "plan": result.get("plan", ""),
        "limits": result.get("limits") or {},
        "last_check": int(time.time()),
    }
    save_license(data)
    return data


def validate_online(quiet=False):
    data = load_license()
    if not data:
        raise RuntimeError("未激活")
    saved = data.get("machine_id")
    # 当前算法或旧算法任一匹配，都视为同一台机器（兼容 v2.6 之前激活的老用户）
    if saved != get_machine_id() and saved != get_machine_id_legacy():
        raise RuntimeError("机器标识变更，请重新激活")
    try:
        result = _post("/validate", {
            "key": data["key"],
            "machine_id": data["machine_id"],
            "version": CLIENT_VERSION,
            "os": _os_info(),
        })
    except (urllib.error.URLError, OSError, TimeoutError):
        # 离线宽限
        last = data.get("last_check", 0)
        if int(time.time()) - last < GRACE_DAYS * 86400:
            return data
        raise RuntimeError(f"无法连接授权服务器（已离线 >{GRACE_DAYS} 天）")
    if not result.get("ok"):
        raise RuntimeError(result.get("msg", "授权已失效"))
    data["expires_at"] = int(result["expires_at"])
    data["plan"] = result.get("plan", data.get("plan", ""))
    if result.get("limits"):
        data["limits"] = result["limits"]
    data["last_check"] = int(time.time())
    save_license(data)
    return data


def heartbeat(action_delta=0):
    """ 定时上报使用情况（IP/版本/操作量） """
    data = load_license()
    if not data:
        return False
    try:
        r = _post("/client/heartbeat", {
            "key": data["key"],
            "machine_id": data["machine_id"],
            "version": CLIENT_VERSION,
            "os": _os_info(),
            "action_delta": action_delta,
        }, timeout=8)
        if r.get("blocked"):
            # 服务端把这台机器封了 → 清本地授权
            clear_license()
            raise RuntimeError("该设备已被禁用")
        return bool(r.get("ok"))
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def fetch_default_ai():
    """ 向服务器获取系统级默认 DeepSeek API Key（用户未配置自己的时使用） """
    data = load_license()
    if not data:
        raise RuntimeError("未激活")
    r = _post("/ai/default_key", {
        "key": data["key"],
        "machine_id": data["machine_id"],
    }, timeout=10)
    if not r.get("ok"):
        raise RuntimeError(r.get("msg", "无法获取默认 AI 配置"))
    return {
        "api_key": r["api_key"],
        "model": r.get("model", "deepseek-chat"),
        "base_url": r.get("base_url", "https://api.deepseek.com/v1/chat/completions"),
    }


def get_limits():
    """ 读取本地缓存的套餐限额（由 validate 时下发） """
    data = load_license()
    if not data:
        return {}
    return data.get("limits") or {}


def check_status():
    """ 返回 (ok, data, error_msg) """
    data = load_license()
    if not data:
        return False, None, "未激活"
    if int(time.time()) > data.get("expires_at", 0):
        return False, data, "授权已过期"
    try:
        data = validate_online(quiet=True)
        return True, data, ""
    except RuntimeError as e:
        return False, data, str(e)


PLAN_NAMES = {
    "day": "日卡", "week": "周卡", "month": "月卡",
    "quarter": "季卡", "year": "年卡",
}


def fmt_expire(ts):
    return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")


def fmt_remain(ts):
    sec = int(ts) - int(time.time())
    if sec <= 0:
        return "已过期"
    d = sec // 86400
    h = (sec % 86400) // 3600
    if d > 0:
        return f"{d}天{h}小时"
    return f"{h}小时"
