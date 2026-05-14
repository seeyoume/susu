"""绮绮采集器 - 客户端自动升级
- 启动时检查服务器最新版本
- 用户确认后下载新 exe 到临时文件
- 写一个 .bat 脚本：等当前进程退出 → 替换 exe → 启动新 exe
- 当前进程立刻退出
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import license_mgr as lm

CURRENT_VERSION = lm.CLIENT_VERSION  # 客户端编译进的版本号


def check_update(timeout=8):
    """ 查询服务器最新版本
        返回 dict: {has_update, latest_version, download_url, sha256, changelog, force}
        失败时返回 None """
    try:
        url = lm.get_server_url() + f"/client/version?current={CURRENT_VERSION}"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        if not data.get("ok"):
            return None
        return data
    except Exception:
        return None


def download_update(info, progress_cb=None):
    """ 下载新版 exe 到临时目录，校验 sha256
        progress_cb(downloaded, total)
        返回临时 exe 路径 """
    url = lm.get_server_url() + info["download_url"]
    total = int(info.get("size") or 0)
    tmp = Path(tempfile.gettempdir()) / f"qiqi_update_{int(time.time())}.exe"
    sha = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(url, timeout=60) as r:
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)
    # 校验
    expect_sha = info.get("sha256") or ""
    actual_sha = sha.hexdigest()
    if expect_sha and expect_sha.lower() != actual_sha.lower():
        try: tmp.unlink()
        except Exception: pass
        raise RuntimeError(f"文件 SHA256 校验失败：期望 {expect_sha[:16]}…，实际 {actual_sha[:16]}…")
    return tmp


def _get_current_exe():
    """ 返回当前运行的 exe 路径（PyInstaller 打包后）；开发模式下返回 None """
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return None


def apply_update(new_exe_path):
    """ 用 .bat 脚本异步替换并重启。当前进程立刻退出。
        - 开发模式（python main.py）下不可用 """
    cur = _get_current_exe()
    if not cur:
        raise RuntimeError("开发模式下无法自动替换 exe，请手动覆盖")
    new_exe_path = Path(new_exe_path)
    # 用 .bat 脚本：等 3 秒（让当前进程退出）→ 删旧 → 改名新 → 启动
    bat = Path(tempfile.gettempdir()) / f"qiqi_apply_{int(time.time())}.bat"
    bat.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        f'cd /d "{cur.parent}"\r\n'
        ":wait\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        f'tasklist /FI "IMAGENAME eq {cur.name}" 2>nul | find /I "{cur.name}" >nul && goto wait\r\n'
        f'del /F /Q "{cur}" 2>nul\r\n'
        f'move /Y "{new_exe_path}" "{cur}" >nul\r\n'
        f'start "" "{cur}"\r\n'
        f'del "%~f0"\r\n',
        encoding="utf-8"
    )
    # 启动 bat 后立刻退出本进程
    subprocess.Popen(
        ["cmd", "/c", str(bat)],
        creationflags=0x08000000,  # CREATE_NO_WINDOW
        close_fds=True,
    )
    # 给系统点时间启动那个 bat
    time.sleep(0.5)
    os._exit(0)
