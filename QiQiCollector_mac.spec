# -*- mode: python ; coding: utf-8 -*-
# Mac 专用 spec — 生成 QiQiCollector.app
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

HERE = Path(SPECPATH)

# ── datas ────────────────────────────────────────────────────────────
datas = [
    (str(HERE / 'license_server.txt'), '.'),
    (str(HERE / 'assets' / 'logo.png'), 'assets'),
    (str(HERE / 'static' / 'xhs_main.js'),   'static'),
    (str(HERE / 'static' / 'xhs_rap.js'),    'static'),
    (str(HERE / 'static' / 'xhs_xray.js'),   'static'),
    (str(HERE / 'static' / 'sign_server.js'), 'static'),
    (str(HERE / 'static' / 'node_modules'),   'static/node_modules'),
]

# Mac 端 node 二进制（如果用户放了 static/node，一起打包；否则依赖系统 node）
_mac_node = HERE / 'static' / 'node'
if _mac_node.exists():
    datas.append((str(_mac_node), 'static'))

# icns 图标（如果已生成）
_icns = HERE / 'assets' / 'logo.icns'
ICON = str(_icns) if _icns.exists() else None

binaries = []
hiddenimports = [
    'openpyxl', 'pymysql',
    'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont',
    '_tkinter',
]

tmp_ret = collect_all('playwright')
datas     += tmp_ret[0]
binaries  += tmp_ret[1]
hiddenimports += tmp_ret[2]

# ── Analysis ─────────────────────────────────────────────────────────
a = Analysis(
    [str(HERE / 'main.py')],
    pathex=[str(HERE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='QiQiCollector',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # Mac 一般不用 UPX
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,   # Mac 专用：不模拟 argv（避免 Finder 拖放异常）
    target_arch=None,       # None = 当前架构; 'universal2' = 同时支持 Intel+M系列
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='QiQiCollector',
)

# 生成 .app bundle
app = BUNDLE(
    coll,
    name='QiQiCollector.app',
    icon=ICON,
    bundle_identifier='com.qiqi.collector',
    info_plist={
        'CFBundleName': 'QiQiCollector',
        'CFBundleDisplayName': '绮绮采集器',
        'CFBundleVersion': '2.5.0',
        'CFBundleShortVersionString': '2.5',
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,  # 支持深色模式
        'LSMinimumSystemVersion': '11.0',
        # 允许访问网络（Playwright 需要）
        'com.apple.security.network.client': True,
        # 允许访问用户文件夹
        'com.apple.security.files.user-selected.read-write': True,
    },
)
