# 绮绮采集器 - 华为云轻量服务器部署指南

## 你需要准备

- ✅ 一台华为云轻量服务器（1 核 2G 即可，CentOS / Ubuntu / OpenEuler）
- ✅ 服务器**公网 IP** 或者绑定一个**域名**
- ✅ 安全组**放行 5000 端口**（应用端口）+ 80/443（HTTPS 用）
- ✅ SSH 工具（推荐用 MobaXterm / 终端）

## 第 1 步：连接服务器

```bash
ssh root@你的公网IP
# 输入密码
```

## 第 2 步：装 Python 3.10+

**Ubuntu 22.04+ / OpenEuler 已自带 python3**：
```bash
python3 --version   # 应 >= 3.10
```

**没有的话装一下**：
```bash
# Ubuntu
apt update && apt install -y python3 python3-pip python3-venv

# CentOS / OpenEuler
yum install -y python3 python3-pip
```

## 第 3 步：上传代码

### 方式 A：SCP 上传（最简单）

电脑本地打开终端：
```bash
# 把整个 server 目录传到服务器
scp -r D:/bs/xhs_scraper/server root@你的IP:/opt/qiqi_server
```

### 方式 B：在服务器上 git clone（如果你把代码放 GitHub 了）

```bash
mkdir -p /opt && cd /opt
git clone https://github.com/你的用户名/qiqi-server.git qiqi_server
```

## 第 4 步：装依赖

```bash
cd /opt/qiqi_server
pip3 install -r requirements.txt
```

## 第 5 步：配置环境变量

**重要！必须改 ADMIN_TOKEN 和 FLASK_SECRET，否则不安全**。

```bash
# 生成两个强随机字符串
python3 -c "import secrets; print(secrets.token_hex(32))"
python3 -c "import secrets; print(secrets.token_hex(32))"
```

把生成的字符串保存好。然后配置环境变量：

```bash
# 编辑环境变量文件
nano /etc/profile.d/qiqi.sh
```

写入：
```bash
export ADMIN_TOKEN="你刚才生成的第一个字符串"
export FLASK_SECRET="你刚才生成的第二个字符串"
```

保存退出（Ctrl+X → Y → Enter），然后：
```bash
source /etc/profile.d/qiqi.sh
echo $ADMIN_TOKEN   # 验证有值
```

## 第 6 步：第一次启动测试

```bash
cd /opt/qiqi_server
python3 app.py
```

看到 `Running on http://0.0.0.0:5000` 就 OK 了。

**先在本地浏览器测试**：访问 `http://你的公网IP:5000/admin`
- 输入你的 ADMIN_TOKEN
- 应该能进管理后台

测试 OK 就 `Ctrl+C` 停掉。

## 第 7 步：生产部署（用 gunicorn + systemd）

### 装 gunicorn

```bash
pip3 install gunicorn
```

### 写 systemd 服务文件

```bash
nano /etc/systemd/system/qiqi.service
```

写入：
```ini
[Unit]
Description=QiQi License Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/qiqi_server
Environment="ADMIN_TOKEN=你的TOKEN"
Environment="FLASK_SECRET=你的SECRET"
ExecStart=/usr/bin/gunicorn -b 0.0.0.0:5000 -w 2 -t 60 app:app
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启动：
```bash
systemctl daemon-reload
systemctl enable qiqi
systemctl start qiqi
systemctl status qiqi    # 检查是否在运行
```

看到 `active (running)` 就 OK。

### 看日志

```bash
journalctl -u qiqi -f
```

## 第 8 步：配置 HTTPS（强烈推荐）

明文 HTTP 会让卡密被中间人窃取。装 Nginx + 免费 SSL：

### 装 Nginx

```bash
# Ubuntu
apt install -y nginx

# CentOS
yum install -y nginx
```

### 配置反向代理

```bash
nano /etc/nginx/conf.d/qiqi.conf
```

写入（先 HTTP 版）：
```nginx
server {
    listen 80;
    server_name your-domain.com;  # 改成你的域名

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 50M;
    }
}
```

重启 Nginx：
```bash
systemctl restart nginx
```

### 装 SSL 证书（用 certbot 免费）

```bash
# Ubuntu
apt install -y certbot python3-certbot-nginx

# 申请证书（要先把域名解析到这台服务器）
certbot --nginx -d your-domain.com
```

按提示输入邮箱、同意条款，证书会自动装好。

## 第 9 步：准备签名 JS 文件

如果你要用"签名 JS 自动更新"功能：

```bash
# 把你本地的签名 JS 上传到服务器
scp D:/bs/xhs_scraper/static/xhs_main.js root@你的IP:/opt/qiqi_server/sign_js/
scp D:/bs/xhs_scraper/static/xhs_rap.js root@你的IP:/opt/qiqi_server/sign_js/
scp D:/bs/xhs_scraper/static/xhs_xray.js root@你的IP:/opt/qiqi_server/sign_js/
scp D:/bs/xhs_scraper/static/sign_server.js root@你的IP:/opt/qiqi_server/sign_js/
```

以后 XHS 改 JS 时，只需把新 JS 替换到这个目录，客户端**启动自动拉新版**。

## 第 10 步：客户端连接你的服务器

打包客户端 exe 前，编辑 `D:\bs\xhs_scraper\license_server.txt`：

```
https://your-domain.com
```

或者纯 IP：
```
http://你的公网IP:5000
```

然后 `build_exe.bat` 打包。**这个 URL 就被烤进 exe 里**，发给客户的 exe 自动连你的服务器。

## 常见问题

### 1. 客户端连不上服务器
- 检查服务器**安全组**是否放行了 5000（或 443）
- `curl http://localhost:5000` 在服务器上测内部访问
- `curl http://你的公网IP:5000` 在外部测公网访问

### 2. 卡密生成报错
- 检查 `ADMIN_TOKEN` 环境变量是否设置：`systemctl show qiqi -p Environment`

### 3. 数据库（license.db）放哪
- 自动放在 `/opt/qiqi_server/license.db`
- **建议每天 cron 备份**：
  ```bash
  crontab -e
  # 加这行：每天 3 点备份
  0 3 * * * cp /opt/qiqi_server/license.db /opt/backup/license_$(date +\%Y\%m\%d).db
  ```

### 4. 看实时日志
```bash
journalctl -u qiqi -f --since "1 hour ago"
```

### 5. 更新代码后重启
```bash
cd /opt/qiqi_server
git pull   # 或重新 scp
systemctl restart qiqi
```

## 安全建议清单

- [ ] **必做**：改默认 `ADMIN_TOKEN` 和 `FLASK_SECRET`
- [ ] **必做**：配 HTTPS（certbot 5 分钟搞定）
- [ ] **必做**：服务器安全组只开必要端口（22/80/443/5000）
- [ ] **建议**：装 fail2ban 防 SSH 暴破
- [ ] **建议**：每天备份 `license.db`
- [ ] **建议**：弱密码 root 改强密码或换用 SSH key
- [ ] **建议**：每月 `apt update && apt upgrade` 打补丁

部署 OK 后访问 `https://your-domain.com/admin` 就能看到管理后台了。
