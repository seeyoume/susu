"""绮绮采集器 - 激活页"""
import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import license_mgr as lm


BRAND_NAME = "绮绮采集器"
SUBTITLE = "仅供学习使用"
VERSION = "v2.5"
QQ_NUMBER = "3954037698"
_ASSETS = Path(os.path.dirname(os.path.abspath(__file__))) / "assets"


def show_activation(parent=None, err_msg=""):
    """
    弹激活窗口。
    parent=None 时: 自己作为 Tk 根窗口运行 mainloop（最稳）
    parent 给定时: 用 Toplevel
    return True=激活成功, False=用户取消/退出
    """
    # Windows: 设置 AppUserModelID 让任务栏图标用我们的
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "qiqi.collector.v2"
            )
        except Exception:
            pass

    if parent is None:
        dlg = tk.Tk()
        is_root = True
    else:
        dlg = tk.Toplevel(parent)
        is_root = False
        try:
            dlg.transient(parent)
            dlg.grab_set()
        except Exception:
            pass

    dlg.title(f"{BRAND_NAME} - 激活")
    dlg.geometry("560x620")
    dlg.resizable(False, False)
    dlg.configure(bg="#1e2329")
    # 窗口图标
    try:
        ico = _ASSETS / "logo.ico"
        png = _ASSETS / "logo.png"
        if ico.exists():
            dlg.iconbitmap(str(ico))
        if png.exists():
            dlg._icon_photo = tk.PhotoImage(file=str(png))
            dlg.iconphoto(False, dlg._icon_photo)
    except Exception:
        pass

    # 强制置顶（前 500ms）
    dlg.lift()
    dlg.attributes("-topmost", True)
    dlg.after(500, lambda: dlg.attributes("-topmost", False))
    try:
        dlg.focus_force()
    except Exception:
        pass

    # ---- 标题区 ----
    header = tk.Frame(dlg, bg="#1e2329"); header.pack(fill="x", pady=(18, 4))
    # 大 LOGO
    try:
        png = _ASSETS / "logo.png"
        if png.exists():
            dlg._big_logo = tk.PhotoImage(file=str(png))
            # PhotoImage 不支持缩放到指定像素，先按比例采样到约 80px 高
            w, h = dlg._big_logo.width(), dlg._big_logo.height()
            if h > 100:
                ratio = max(1, h // 80)
                dlg._big_logo = dlg._big_logo.subsample(ratio, ratio)
            tk.Label(header, image=dlg._big_logo, bg="#1e2329").pack()
    except Exception:
        pass
    tk.Label(header, text=BRAND_NAME, font=("Microsoft YaHei", 26, "bold"),
             bg="#1e2329", fg="#fff").pack(pady=(4, 0))
    tk.Label(header, text=f"{SUBTITLE}    {VERSION}",
             font=("Microsoft YaHei", 10), bg="#1e2329", fg="#8b949e").pack(pady=(2, 0))

    # ---- 底部（先 pack 到 bottom，确保不会被 body 的 expand=True 挤出可视区）----
    foot = tk.Frame(dlg, bg="#161b22"); foot.pack(fill="x", side="bottom")
    tk.Label(foot, text="日卡 / 周卡 / 月卡 / 季卡 / 年卡",
             font=("Microsoft YaHei", 9), bg="#161b22", fg="#8b949e")\
        .pack(pady=(10, 2))

    def copy_qq(_event=None):
        dlg.clipboard_clear()
        dlg.clipboard_append(QQ_NUMBER)
        lbl_qq.configure(text=f"✓ 已复制 QQ：{QQ_NUMBER}", fg="#7ee787")
        dlg.after(1500, lambda: lbl_qq.configure(
            text=f"客服 QQ：{QQ_NUMBER}（点击复制）", fg="#58a6ff"))

    lbl_qq = tk.Label(foot, text=f"客服 QQ：{QQ_NUMBER}（点击复制）",
                      font=("Microsoft YaHei", 10, "bold"),
                      bg="#161b22", fg="#58a6ff", cursor="hand2")
    lbl_qq.pack(pady=(0, 10))
    lbl_qq.bind("<Button-1>", copy_qq)

    body = tk.Frame(dlg, bg="#1e2329"); body.pack(fill="both", expand=True, padx=36, pady=14)

    # ---- 机器码 ----
    tk.Label(body, text="① 您的机器码（购买卡密时提供给客服）",
             font=("Microsoft YaHei", 10, "bold"), bg="#1e2329", fg="#d4d4d4")\
        .pack(anchor="w", pady=(4, 4))
    mid = lm.get_machine_id()
    mid_row = tk.Frame(body, bg="#1e2329"); mid_row.pack(fill="x")
    e_mid = tk.Entry(mid_row, font=("Consolas", 11),
                     bg="#0d1117", fg="#7ee787", insertbackground="#fff",
                     relief="flat", bd=0)
    e_mid.insert(0, mid)
    e_mid.configure(state="readonly", readonlybackground="#0d1117")
    e_mid.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 6))

    def copy_mid():
        dlg.clipboard_clear()
        dlg.clipboard_append(mid)
        btn_copy.configure(text="✓ 已复制")
        dlg.after(1200, lambda: btn_copy.configure(text="复制"))

    btn_copy = tk.Button(mid_row, text="复制", command=copy_mid,
                         bg="#21262d", fg="#fff", relief="flat",
                         font=("Microsoft YaHei", 9), width=8, cursor="hand2",
                         activebackground="#30363d")
    btn_copy.pack(side="left", ipady=6)

    # ---- 卡密输入 ----
    tk.Label(body, text="\n② 输入您的激活卡密",
             font=("Microsoft YaHei", 10, "bold"), bg="#1e2329", fg="#d4d4d4")\
        .pack(anchor="w", pady=(8, 4))
    e_key = tk.Entry(body, font=("Consolas", 14),
                     bg="#0d1117", fg="#fff", insertbackground="#fff",
                     relief="flat", bd=0, justify="center")
    e_key.pack(fill="x", ipady=10)
    e_key.focus_set()

    status_var = tk.StringVar(value=err_msg or "")
    status_lbl = tk.Label(body, textvariable=status_var,
                          font=("Microsoft YaHei", 10), bg="#1e2329",
                          fg="#f85149", wraplength=480, justify="left")
    status_lbl.pack(anchor="w", pady=(10, 4), fill="x")

    result = {"ok": False}

    def show_msg(msg, color="#f85149"):
        status_lbl.configure(fg=color)
        status_var.set(msg)
        dlg.update_idletasks()

    def do_activate(event=None):
        key = e_key.get().strip().upper()
        if not key:
            show_msg("✗ 请输入卡密")
            return
        btn_act.configure(state="disabled", text="激活中...")
        show_msg("正在向服务器验证...", color="#8b949e")
        dlg.after(50, lambda: _do_activate_async(key))

    def _do_activate_async(key):
        try:
            data = lm.activate(key)
            show_msg(
                f"✓ 激活成功！套餐: {lm.PLAN_NAMES.get(data.get('plan',''), data.get('plan',''))}  "
                f"到期: {lm.fmt_expire(data['expires_at'])}",
                color="#7ee787",
            )
            result["ok"] = True
            dlg.after(1400, _close)
        except Exception as e:
            btn_act.configure(state="normal", text="🔑 激活")
            show_msg(f"✗ {e}")

    def _close():
        try:
            dlg.destroy()
        except Exception:
            pass
        if is_root:
            try:
                dlg.quit()
            except Exception:
                pass

    e_key.bind("<Return>", do_activate)

    # ---- 按钮 ----
    btn_row = tk.Frame(body, bg="#1e2329"); btn_row.pack(pady=(16, 6), fill="x")
    btn_act = tk.Button(btn_row, text="🔑 激活", command=do_activate,
                        bg="#238636", fg="#fff", relief="flat", cursor="hand2",
                        font=("Microsoft YaHei", 11, "bold"),
                        activebackground="#2ea043")
    btn_act.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 4))
    btn_quit = tk.Button(btn_row, text="退出", command=_close,
                         bg="#21262d", fg="#d4d4d4", relief="flat", cursor="hand2",
                         font=("Microsoft YaHei", 11),
                         activebackground="#30363d")
    btn_quit.pack(side="left", fill="x", expand=True, ipady=8, padx=(4, 0))

    dlg.protocol("WM_DELETE_WINDOW", _close)

    if is_root:
        dlg.mainloop()
    else:
        dlg.wait_window()

    return result["ok"]


if __name__ == "__main__":
    print("激活成功" if show_activation() else "用户退出")
