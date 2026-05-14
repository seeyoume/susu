"""签名 JS 自动更新
从授权服务器拉 manifest → 比对版本 → 下载更新的 JS 文件
"""
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import license_mgr as lm


def _static_dir():
    """ 始终写入用户态目录（exe 模式下 _MEIPASS 是只读的） """
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(sys.executable)) / "static_update"
    return Path(__file__).parent / "static"


def _local_version_file():
    return _static_dir() / "_version.txt"


def _read_local_version():
    try:
        return _local_version_file().read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _write_local_version(v):
    f = _local_version_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(str(v), encoding="utf-8")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def check_and_update(log=None):
    """
    检查并更新签名 JS。
    返回 (updated: bool, msg: str)
    """
    log = log or (lambda m: None)
    server = lm.get_server_url()
    if not server:
        return False, "未配置授权服务器"
    manifest_url = server.rstrip("/") + "/sign/js/manifest"
    try:
        with urllib.request.urlopen(manifest_url, timeout=8) as resp:
            manifest = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return False, f"获取 manifest 失败: {e}"
    if not isinstance(manifest, dict) or not manifest.get("files"):
        return False, "manifest 格式异常"
    remote_ver = str(manifest.get("version", ""))
    local_ver = _read_local_version()
    if remote_ver and remote_ver == local_ver:
        return False, f"已是最新版本 ({remote_ver})"

    target = _static_dir()
    target.mkdir(parents=True, exist_ok=True)
    updated_files = []
    for fname, info in manifest["files"].items():
        if not fname.endswith(".js") or "/" in fname or "\\" in fname:
            continue
        try:
            file_url = f"{server.rstrip('/')}/sign/js/file/{fname}"
            with urllib.request.urlopen(file_url, timeout=20) as r:
                data = r.read()
        except Exception as e:
            log(f"下载 {fname} 失败: {e}")
            continue
        expected = info.get("sha256")
        if expected and _sha256(data) != expected:
            log(f"{fname} 校验失败，跳过")
            continue
        try:
            (target / fname).write_bytes(data)
            updated_files.append(fname)
        except Exception as e:
            log(f"写入 {fname} 失败: {e}")

    if updated_files:
        _write_local_version(remote_ver)
        return True, f"签名 JS 已更新到 {remote_ver}：{', '.join(updated_files)}"
    return False, "无文件更新"
