#!/bin/bash
# 绮绮服务端 - 一键更新脚本（保留数据，只更新代码）
# 用法: cd /www/wwwroot/qiqi && bash update.sh

set -e
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="qiqi-license"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

cd "$PROJECT_DIR"

# 1) 备份当前数据库
TS=$(date +%Y%m%d_%H%M%S)
if [ -f license.db ]; then
    cp license.db "license.db.bak_$TS"
    echo -e "${GREEN}✓ 已备份 license.db → license.db.bak_$TS${NC}"
fi

# 2) 备份 .env
if [ -f .env ]; then
    cp .env ".env.bak_$TS"
fi

# 3) 拉新依赖（如果改了 requirements.txt）
if [ -d venv ]; then
    source venv/bin/activate
    pip install -r requirements.txt -q -i https://pypi.tuna.tsinghua.edu.cn/simple
    echo -e "${GREEN}✓ 依赖已更新${NC}"
fi

# 4) 重启服务
if command -v systemctl &> /dev/null && systemctl list-units | grep -q "$SERVICE_NAME"; then
    systemctl restart "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo -e "${GREEN}✓ 服务已重启${NC}"
    else
        echo -e "${RED}✗ 重启失败${NC}"
        systemctl status "$SERVICE_NAME" --no-pager
        exit 1
    fi
else
    pkill -f "gunicorn.*app:app" || true
    sleep 1
    nohup venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 --timeout 60 app:app \
        > server.log 2>&1 &
    echo -e "${GREEN}✓ 服务已重启 (nohup)${NC}"
fi

# 5) 清理 7 天前的备份
find . -name 'license.db.bak_*' -mtime +7 -delete 2>/dev/null
find . -name '.env.bak_*' -mtime +7 -delete 2>/dev/null

echo
echo -e "${YELLOW}更新完成！查看日志:${NC}"
echo "  tail -f server.log"
echo "  或 journalctl -u $SERVICE_NAME -f"
