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
    req = urllib.request.Request(url, headers={
        "User-Agent": f"QiQiCollector/{CURRENT_VERSION} (Updater)",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status = getattr(r, "status", 200) or 200
            if status != 200:
                raise RuntimeError(f"服务器返回 HTTP {status}")
            content_length = r.headers.get("Content-Length")
            if content_length and not total:
                total = int(content_length)
            with open(tmp, "wb") as f:
                last_cb = 0.0
                while True:
                    try:
                        chunk = r.read(128 * 1024)
                    except Exception as e:
                        raise RuntimeError(f"下载中断: {e}")
                    if not chunk:
                        break
                    f.write(chunk)
                    sha.update(chunk)
                    done += len(chunk)
                    # 限流回调，避免 GUI 卡死（每 0.1 秒最多回调一次）
                    now = time.time()
                    if progress_cb and (now - last_cb > 0.1 or done >= total):
                        try: progress_cb(done, total or done)
                        except Exception: pass
                        last_cb = now
    except urllib.error.HTTPError as e:
        try: tmp.unlink()
        except Exception: pass
        raise RuntimeError(f"HTTP 错误 {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        try: tmp.unlink()
        except Exception: pass
        raise RuntimeError(f"网络错误: {e.reason}")
    except Exception as e:
        try: tmp.unlink()
        except Exception: pass
        raise
    if done == 0:
        try: tmp.unlink()
        except Exception: pass
        raise RuntimeError("下载文件为空")
    # 校验
    expect_sha = info.get("sha256") or ""
    actual_sha = sha.hexdigest()
    if expect_sha and expect_sha.lower() != actual_sha.lower():
        try: tmp.unlink()
        except Exception: pass
        raise RuntimeError(f"文件 SHA256 校验失败：期望 {expect_sha[:16]}…，实际 {actual_sha[:16]}…")
    return tmp


def is_installer_update(info):
    """ 判断服务器下发的是安装包（Setup.exe）还是裸 exe 替换 """
    return bool(info.get("installer")) or str(info.get("download_url", "")).lower().endswith("_setup.exe")


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
    log = Path(tempfile.gettempdir()) / "qiqi_update.log"
    bat = Path(tempfile.gettempdir()) / f"qiqi_apply_{int(time.time())}.bat"

    # 写 bat 使用系统代码页（mbcs），避免中文路径乱码
    # 逻辑：等待旧进程退出（最多 30s）→ 重试删旧 exe → copy 新 exe → 启动 → 清理
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        f'set "NEW={new_exe_path}"',
        f'set "CUR={cur}"',
        f'set "LOG={log}"',
        f'set "NAME={cur.name}"',
        "set WAIT=0",
        ":waitloop",
        "timeout /t 1 /nobreak >nul",
        'tasklist /FI "IMAGENAME eq %NAME%" 2>nul | find /I "%NAME%" >nul',
        "if not errorlevel 1 (",
        "  set /a WAIT+=1",
        "  if %WAIT% lss 30 goto waitloop",
        ")",
        'echo [%DATE% %TIME%] replacing >> "%LOG%"',
        # 最多重试 5 次删旧 exe
        "set RETRY=0",
        ":delloop",
        'del /F /Q "%CUR%" 2>nul',
        'if exist "%CUR%" (',
        "  set /a RETRY+=1",
        "  if %RETRY% lss 5 (",
        "    timeout /t 1 /nobreak >nul",
        "    goto delloop",
        "  )",
        '  echo [%DATE% %TIME%] delete failed >> "%LOG%"',
        "  goto end",
        ")",
        # move 失败（跨盘）时 fallback copy
        'move /Y "%NEW%" "%CUR%" >nul 2>&1',
        "if errorlevel 1 (",
        '  copy /Y "%NEW%" "%CUR%" >nul',
        '  del /F /Q "%NEW%" 2>nul',
        ")",
        'echo [%DATE% %TIME%] done >> "%LOG%"',
        'start "" "%CUR%"',
        ":end",
        'del "%~f0"',
    ]
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="mbcs", errors="replace")

    subprocess.Popen(
        ["cmd", "/c", str(bat)],
        creationflags=0x08000000,  # CREATE_NO_WINDOW
        close_fds=True,
    )
    time.sleep(0.5)
    os._exit(0)


def apply_update_installer(setup_exe_path):
    """ 运行 Inno Setup 安装包静默升级（/SILENT），安装完成后自动重启。
        适用于服务器下发 _Setup.exe 时的场景。 """
    setup_exe_path = Path(setup_exe_path)
    subprocess.Popen(
        [str(setup_exe_path), "/SILENT", "/NORESTART"],
        creationflags=0x00000008,  # DETACHED_PROCESS
        close_fds=True,
    )
    time.sleep(0.5)
    os._exit(0)
