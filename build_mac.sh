#!/usr/bin/env bash
# ============================================================
# 绮绮采集器 - Mac 一键打包脚本
# 使用方法：
#   chmod +x build_mac.sh
#   ./build_mac.sh
#
# 前置要求（脚本会自动检查）：
#   - macOS 11+
#   - Python 3.9-3.12（推荐 3.11）
#   - Homebrew（用于安装 node）
#   - 建议：pip install virtualenv 先建虚拟环境
# ============================================================

set -e
cd "$(dirname "$0")"

ARCH=$(uname -m)   # arm64 = M系列, x86_64 = Intel
PYTHON=${PYTHON:-python3}

echo "================================================"
echo " 绮绮采集器 Mac 打包  (arch: $ARCH)"
echo "================================================"
echo ""

# ── 1. 检查 Python ───────────────────────────────────────
if ! command -v "$PYTHON" &>/dev/null; then
    echo "[ERROR] 找不到 python3，请先安装: brew install python@3.11"
    exit 1
fi
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[OK] Python $PY_VER"

# ── 2. 检查 / 安装 Node.js ───────────────────────────────
if command -v node &>/dev/null; then
    NODE_PATH=$(which node)
    echo "[OK] Node.js $(node --version) at $NODE_PATH"
else
    echo "[INFO] 未检测到 Node.js，尝试通过 Homebrew 安装..."
    if ! command -v brew &>/dev/null; then
        echo "[ERROR] 未安装 Homebrew，请先执行:"
        echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        exit 1
    fi
    brew install node
    NODE_PATH=$(which node)
    echo "[OK] Node.js $(node --version) 安装完成"
fi

# 把系统 node 复制到 static/node（打包进 .app 里，用户不需要额外装 node）
echo ""
echo "[步骤1] 复制 node 二进制到 static/ ..."
cp "$NODE_PATH" static/node
chmod +x static/node
echo "[OK] static/node 已就绪"

# ── 3. 安装 Python 依赖 ──────────────────────────────────
echo ""
echo "[步骤2] 安装 Python 依赖..."
"$PYTHON" -m pip install --upgrade pip -q
"$PYTHON" -m pip install -r requirements.txt
"$PYTHON" -m pip install pyinstaller pillow

# ── 4. 安装 Playwright 浏览器 ───────────────────────────
echo ""
echo "[步骤3] 安装 Playwright Chromium 浏览器..."
"$PYTHON" -m playwright install chromium
echo "[OK] Playwright Chromium 已安装"

# ── 5. 生成 ICNS 图标 ───────────────────────────────────
echo ""
echo "[步骤4] 生成 .icns 图标..."
if [ -f assets/logo.png ]; then
    ICONSET_DIR=assets/logo.iconset
    mkdir -p "$ICONSET_DIR"
    for SIZE in 16 32 64 128 256 512; do
        sips -z $SIZE $SIZE assets/logo.png \
            --out "$ICONSET_DIR/icon_${SIZE}x${SIZE}.png" &>/dev/null
        DOUBLE=$((SIZE * 2))
        sips -z $DOUBLE $DOUBLE assets/logo.png \
            --out "$ICONSET_DIR/icon_${SIZE}x${SIZE}@2x.png" &>/dev/null
    done
    iconutil -c icns "$ICONSET_DIR" -o assets/logo.icns
    rm -rf "$ICONSET_DIR"
    echo "[OK] assets/logo.icns 已生成"
else
    echo "[WARN] 未找到 assets/logo.png，跳过图标生成"
fi

# ── 6. PyInstaller 打包 ──────────────────────────────────
echo ""
echo "[步骤5] 开始打包（约 3-8 分钟，请耐心等待）..."
"$PYTHON" -m PyInstaller --noconfirm QiQiCollector_mac.spec

# ── 7. 打包完成后下载 Playwright 浏览器到 app 内 ─────────
APP_BUNDLE="dist/QiQiCollector.app"
if [ -d "$APP_BUNDLE" ]; then
    echo ""
    echo "[步骤6] 将 Playwright Chromium 写入 .app bundle..."
    # Playwright 在 Mac 下把浏览器放在 ~/Library/Caches/ms-playwright
    # 打包后 app 会走 PLAYWRIGHT_BROWSERS_PATH 环境变量
    # 提示用户首次运行时会自动下载（如果需要离线可手动复制）
    echo "[INFO] .app 运行时会读取 ~/Library/Caches/ms-playwright 里的浏览器"
    echo "[INFO] 如需离线分发，请将该目录随 .app 一起打包给用户"
fi

# ── 8. 生成 DMG（可选）──────────────────────────────────
if command -v create-dmg &>/dev/null; then
    echo ""
    echo "[步骤7] 生成 DMG 安装包..."
    create-dmg \
        --volname "绮绮采集器" \
        --volicon "assets/logo.icns" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 128 \
        --icon "QiQiCollector.app" 150 180 \
        --hide-extension "QiQiCollector.app" \
        --app-drop-link 450 180 \
        "dist/QiQiCollector_mac.dmg" \
        "dist/QiQiCollector.app" || true
    echo "[OK] DMG 生成于 dist/QiQiCollector_mac.dmg"
else
    echo "[INFO] 未安装 create-dmg，跳过 DMG 生成"
    echo "[INFO] 安装方法: brew install create-dmg"
fi

echo ""
echo "================================================"
echo " 打包完成！"
echo " 产物路径: dist/QiQiCollector.app"
echo ""
echo " 给用户分发时注意："
echo "  1. 首次打开会被 macOS 拦截 (未公证)"
echo "     → 右键点击 .app → 打开 → 确认打开"
echo "  2. 需要系统装有 Chromium/Chrome（Playwright 驱动）"
echo "     → 或让用户运行: playwright install chromium"
echo "  3. 如果 M 系列 Mac 报错，尝试在终端运行:"
echo "     open dist/QiQiCollector.app"
echo "================================================"
