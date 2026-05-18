# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('license_server.txt', '.'), ('assets/logo.png', 'assets'), ('assets/logo.ico', 'assets'), ('static/xhs_main.js', 'static'), ('static/xhs_rap.js', 'static'), ('static/xhs_xray.js', 'static'), ('static/sign_server.js', 'static'), ('static/node.exe', 'static'), ('static/node_modules', 'static/node_modules')]
binaries = []
hiddenimports = ['openpyxl', 'pymysql', 'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont', 'ttkbootstrap']
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# 打包 ttkbootstrap 主题资源
tmp_ret = collect_all('ttkbootstrap')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
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
    a.binaries,
    a.datas,
    [],
    name='QiQiCollector',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\logo.ico'],
)
