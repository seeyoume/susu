#!/bin/bash
# 绮绮采集器服务端 - 宝塔一键启动脚本
# 用法: cd /www/wwwroot/qiqi && bash bootstrap.sh

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-5000}"
SERVICE_NAME="qiqi-license"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}===== 绮绮采集器服务端部署 =====${NC}"
echo "项目目录: $PROJECT_DIR"
echo "监听端口: $PORT"
echo

# 1) 检查 Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ 未检测到 python3，请先在宝塔安装 Python 3.10+${NC}"
    exit 1
fi
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "${GREEN}✓ Python $PY_VER${NC}"

# 2) 创建 venv
if [ ! -d "$PROJECT_DIR/venv" ]; then
    echo "→ 创建虚拟环境 venv/..."
    python3 -m venv "$PROJECT_DIR/venv"
fi
source "$PROJECT_DIR/venv/bin/activate"
echo -e "${GREEN}✓ 虚拟环境已激活${NC}"

# 3) 装依赖
echo "→ 安装依赖（flask + gunicorn）..."
pip install --upgrade pip -q
pip install -r "$PROJECT_DIR/requirements.txt" -q -i https://pypi.tuna.tsinghua.edu.cn/simple
echo -e "${GREEN}✓ 依赖安装完成${NC}"

# 4) 生成或读取 .env
ENV_FILE="$PROJECT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    ADMIN_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    FLASK_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(16))")
    cat > "$ENV_FILE" <<EOF
ADMIN_TOKEN=$ADMIN_TOKEN
FLASK_SECRET=$FLASK_SECRET
PORT=$PORT
EOF
    chmod 600 "$ENV_FILE"
    echo -e "${GREEN}✓ 已生成 .env（含强随机 Token）${NC}"
else
    echo -e "${YELLOW}! .env 已存在，沿用现有 Token${NC}"
    source "$ENV_FILE"
fi
source "$ENV_FILE"

# 5) 初始化数据库
echo "→ 初始化 sqlite 数据库..."
cd "$PROJECT_DIR"
python3 -c "from app import init_db; init_db()"
echo -e "${GREEN}✓ 数据库就绪${NC}"

# 6) 检测是否有 systemd
if command -v systemctl &> /dev/null; then
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    echo "→ 创建 systemd 服务: $SERVICE_FILE"
    sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=QiQi License Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$PROJECT_DIR/venv/bin/gunicorn -w 2 -b 0.0.0.0:$PORT --timeout 60 app:app
Restart=always
RestartSec=3
StandardOutput=append:$PROJECT_DIR/server.log
StandardError=append:$PROJECT_DIR/server.log

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl restart "$SERVICE_NAME"
    sleep 2
    if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
        echo -e "${GREEN}✓ systemd 服务已启动并设为开机自启${NC}"
    else
        echo -e "${RED}✗ 服务启动失败，看日志: $PROJECT_DIR/server.log${NC}"
        sudo systemctl status "$SERVICE_NAME" --no-pager
        exit 1
    fi
else
    # 无 systemd，直接 nohup 跑
    echo "→ 使用 nohup 启动..."
    pkill -f "gunicorn.*app:app" || true
    nohup "$PROJECT_DIR/venv/bin/gunicorn" -w 2 -b 0.0.0.0:$PORT --timeout 60 app:app \
        > "$PROJECT_DIR/server.log" 2>&1 &
    sleep 2
    if pgrep -f "gunicorn.*app:app" > /dev/null; then
        echo -e "${GREEN}✓ 服务已用 nohup 启动${NC}"
    else
        echo -e "${RED}✗ 启动失败，看 server.log${NC}"
        exit 1
    fi
fi

# 7) 探测外网 IP
PUBLIC_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || curl -s --max-time 5 http://ip.sb 2>/dev/null || hostname -I | awk '{print $1}')

echo
echo -e "${GREEN}====== 🎉 部署成功 ======${NC}"
echo
echo -e "${YELLOW}管理后台:${NC}"
echo -e "   ${GREEN}http://$PUBLIC_IP:$PORT/admin/login${NC}"
echo
echo -e "${YELLOW}登录 Token (请妥善保存):${NC}"
echo -e "   ${GREEN}$ADMIN_TOKEN${NC}"
echo
echo -e "${YELLOW}本地配置:${NC}"
echo "   编辑 Windows 上的 license_server.txt：写入"
echo -e "   ${GREEN}http://$PUBLIC_IP:$PORT${NC}"
echo
echo -e "${YELLOW}首次登录后要做的事:${NC}"
echo "   1. 进【🤖 AI 默认 Key】Tab 填 DeepSeek API Key"
echo "   2. 进【⏱ 套餐限额】Tab 检查默认限额（可改）"
echo "   3. 进【🔑 卡密管理】Tab 生成测试卡密"
echo
echo -e "${YELLOW}查看日志:${NC}"
if command -v systemctl &> /dev/null; then
    echo "   sudo journalctl -u $SERVICE_NAME -f"
    echo "   或 tail -f $PROJECT_DIR/server.log"
else
    echo "   tail -f $PROJECT_DIR/server.log"
fi
echo
echo -e "${YELLOW}重启服务:${NC}"
if command -v systemctl &> /dev/null; then
    echo "   sudo systemctl restart $SERVICE_NAME"
else
    echo "   pkill -f 'gunicorn.*app:app' && cd $PROJECT_DIR && bash bootstrap.sh"
fi
echo
echo -e "${YELLOW}⚠️  上线前建议:${NC}"
echo "   - 在宝塔加站点 + Nginx 反代到 127.0.0.1:$PORT"
echo "   - 申请 Let's Encrypt SSL 证书，开启强制 HTTPS"
echo "   - Nginx 务必传 X-Real-IP 头（否则监控面板看到的全是 127.0.0.1）"
echo
