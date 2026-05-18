"""XHS 签名生成 - 启动持久 Node.js 子进程，避免每次启动开销
依赖: static/sign_server.js + static/xhs_main.js + static/xhs_rap.js
打包后从 sys._MEIPASS/static/ 读取
"""
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path


def _resource_root():
    """ PyInstaller exe 模式下读 _MEIPASS，开发模式下读脚本目录 """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    return Path(__file__).parent


def _update_dir():
    """ 自动更新写入的目录（用户态可写）"""
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(sys.executable)) / "static_update"
    return Path(__file__).parent / "static"


STATIC_DIR = _resource_root() / "static"
UPDATE_DIR = _update_dir()
SIGN_SCRIPT = STATIC_DIR / "sign_server.js"


def _resolve_js(name):
    """ 优先返回更新目录里的版本（如果存在） """
    upd = UPDATE_DIR / name
    if upd.exists():
        return upd
    return STATIC_DIR / name


def _find_node():
    """ 1) 优先用打包的 static/node(.exe)  2) 退回系统 PATH 里的 node """
    node_name = "node.exe" if os.name == "nt" else "node"
    bundled = STATIC_DIR / node_name
    if bundled.exists():
        # macOS 打包的 node 二进制需要可执行权限
        if os.name != "nt":
            try:
                bundled.chmod(bundled.stat().st_mode | 0o111)
            except Exception:
                pass
        return str(bundled)
    sys_node = shutil.which("node")
    if sys_node:
        return sys_node
    if sys.platform == "darwin":
        hint = "请先安装 Node.js：\n  brew install node\n或访问 https://nodejs.org 下载安装包"
    elif os.name == "nt":
        hint = "请先安装 Node.js：https://nodejs.org"
    else:
        hint = "请先安装 Node.js：sudo apt install nodejs  或  https://nodejs.org"
    raise RuntimeError(f"找不到 Node.js 可执行文件（既无内置 static/{node_name} 也无系统 node）\n{hint}")


class XhsSigner:
    """ 持久 Node.js 签名服务 (stdin/stdout 通信) """

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self._start()

    def _start(self):
        if not SIGN_SCRIPT.exists():
            raise RuntimeError(f"找不到签名脚本: {SIGN_SCRIPT}")
        # Windows 上需要明确 shell=False + creationflags 隐藏窗口
        startupinfo = None
        creationflags = 0
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = 0x08000000  # CREATE_NO_WINDOW
        except Exception:
            pass
        node_path = _find_node()
        self.proc = subprocess.Popen(
            [node_path, str(SIGN_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(STATIC_DIR),
            bufsize=1,
            text=True,
            encoding="utf-8",
            startupinfo=startupinfo,
            creationflags=creationflags,
        )

    def _alive(self):
        return self.proc and self.proc.poll() is None

    def sign(self, api, data=None, a1="", method="POST"):
        """ 返回 dict: {'x-s', 'x-t', 'x-s-common', [可选 'x-rap-param']} """
        with self.lock:
            if not self._alive():
                self._start()
            req = {"api": api, "data": data, "a1": a1, "method": method}
            try:
                self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
            except Exception as e:
                # 重启一次
                self._start()
                self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
            if not line:
                err = ""
                try: err = self.proc.stderr.read(2000) or ""
                except Exception: pass
                raise RuntimeError(f"签名进程无返回: {err}")
            try:
                r = json.loads(line)
            except Exception:
                raise RuntimeError(f"签名返回格式错误: {line[:200]}")
            if not r.get("ok"):
                raise RuntimeError(f"签名失败: {r.get('err', '?')}")
            return r

    def close(self):
        try:
            if self.proc:
                self.proc.stdin.close()
                self.proc.terminate()
        except Exception:
            pass


# 单例
_signer = None
_signer_lock = threading.Lock()


def get_signer():
    global _signer
    with _signer_lock:
        if _signer is None:
            _signer = XhsSigner()
    return _signer


def sign(api, data=None, a1="", method="POST"):
    return get_signer().sign(api, data, a1, method)
