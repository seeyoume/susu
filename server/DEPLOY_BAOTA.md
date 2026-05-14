# 绮绮采集器 - 宝塔面板部署指南

## ✅ 前置条件

- 一台 Linux 服务器（**CentOS 7+ / Ubuntu 20+ / Debian 11+** 都可以）
- 推荐配置：**2核 2G / 系统盘 40G** 起（每月 ~50 元）
- 已经买好的**域名**（推荐，便于 HTTPS）—— 没有也能用纯 IP
- 已安装最新版**宝塔面板** (https://www.bt.cn/)
- 宝塔面板里安装好：**Python 项目管理器**（软件商店搜）

---

## 一、上传代码

### 方式 A: 用宝塔文件管理器（推荐）

1. **打包项目**（Windows 上把整个 `server/` 文件夹压缩成 `qiqi_server.zip`）

   要包含的文件：
   ```
   server/
   ├── app.py
   ├── admin_cli.py
   ├── requirements.txt
   ├── templates/
   │   ├── admin.html
   │   └── login.html
   └── sign_js/        ← 可选，没有就空文件夹
   ```

2. 登录宝塔 → 文件 → 进入 `/www/wwwroot/`
3. 新建目录 `qiqi` → 进入该目录 → 上传 `qiqi_server.zip` → 解压
4. 把 `server/*` 内容直接放到 `/www/wwwroot/qiqi/`（不要嵌套 server 目录）

最终目录结构：
```
/www/wwwroot/qiqi/
├── app.py
├── admin_cli.py
├── requirements.txt
├── templates/
└── sign_js/
```

### 方式 B: Git 克隆（如果你有 Git 仓库）

```bash
cd /www/wwwroot/
git clone https://github.com/你的仓库.git qiqi
```

---

## 二、创建 Python 项目（宝塔自动管理）

1. 宝塔面板左侧 → **Python 项目** → **添加 Python 项目**

2. 填写：

   | 字段 | 值 |
   |---|---|
   | **项目名称** | `qiqi-license-server` |
   | **路径** | `/www/wwwroot/qiqi` |
   | **Python 版本** | `3.10` 或更高（必须 3.8+） |
   | **框架** | `Flask` |
   | **启动方式** | `gunicorn` |
   | **启动文件** | `app.py` |
   | **入口参数** | `app:app` |
   | **端口** | `5000` |
   | **进程数** | `2`（小流量够用） |

3. **环境变量**（极其重要！）：
   ```
   ADMIN_TOKEN=随机64位字符串（这是登录管理后台的密码）
   FLASK_SECRET=另一个随机32位字符串
   ```

   生成方法：在本地 Python 里跑：
   ```python
   import secrets
   print(secrets.token_hex(32))   # ADMIN_TOKEN
   print(secrets.token_hex(16))   # FLASK_SECRET
   ```

4. 点击 **创建** → 宝塔会自动 `pip install -r requirements.txt`

5. 启动后能看到状态变绿 → 验证：浏览器访问 `http://你服务器IP:5000/admin/login`

---

## 三、配置防火墙（开放端口）

### 宝塔层
左侧 → **安全** → 防火墙 → 添加端口 `5000` （如果用反向代理，这步可以跳过）

### 云服务商层（阿里云/腾讯云）
登录控制台 → 安全组 → 添加规则：
- 协议: TCP
- 端口: 5000（如果走反代，开 80/443 就够）
- 来源: `0.0.0.0/0`

---

## 四、绑定域名 + Nginx 反向代理（推荐，能上 HTTPS）

如果你只有 IP，跳到第 V 步。

### 4.1 在宝塔加站点
1. 宝塔 → **网站** → **添加站点**
2. 域名：`api.你的域名.com`
3. 根目录：`/www/wwwroot/qiqi`（不重要，反代会接管）
4. PHP 版本：**纯静态**（不需要 PHP）
5. 创建数据库：**不创建**

### 4.2 配置反向代理
1. 进入刚建的站点设置
2. 左侧 → **反向代理** → **添加反向代理**
3. 填写：
   - 代理名称：`qiqi-api`
   - 目标 URL：`http://127.0.0.1:5000`
   - 发送域名：`$host`
4. **保存** → 点击高级配置（编辑配置文件），把 `location /` 块替换为：

```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_redirect off;
    proxy_connect_timeout 30s;
    proxy_send_timeout 60s;
    proxy_read_timeout 60s;
}
```

> ⚠️ **`X-Real-IP` 和 `X-Forwarded-For` 一定要传**，否则服务端拿不到用户真实 IP，监控面板看到的全是 `127.0.0.1`。

### 4.3 申请 SSL 证书（强烈推荐）
站点设置 → SSL → **Let's Encrypt** → 勾选域名 → 申请。
申请成功后开启 **强制 HTTPS**。

现在访问：`https://api.你的域名.com/admin/login` ✅

---

## 五、第一次登录管理后台

1. 浏览器打开：
   - 有域名：`https://api.你的域名.com/admin/login`
   - 没域名：`http://你服务器IP:5000/admin/login`

2. 输入 **ADMIN_TOKEN**（你刚才设的那个64位串）→ 登录

3. 进入后看到 7 个 Tab：
   - 📊 概览
   - 🔑 卡密管理 — 生成日卡/周卡/月卡卡密
   - 👥 用户监控 — 实时看每个客户端 IP/操作量
   - 🤖 AI 默认 Key — **首次进来必填这个！**
   - ⏱ 套餐限额 — 每个套餐的延时/上限
   - 🚫 封禁机器 — 黑名单
   - 📢 公告 — 推送给所有用户

4. **去 🤖 AI 默认 Key Tab，填上你的 DeepSeek API Key**
   - 申请地址：https://platform.deepseek.com/
   - 充值 10 元能用很久
   - 填完保存，所有用户立刻有 AI 功能（30 分钟生效）

---

## 六、让客户端连上你的服务器

回到本地开发机：

### 6.1 修改客户端配置

打开 `D:\bs\xhs_scraper\license_server.txt`（没有就新建），写入：
```
https://api.你的域名.com
```
（如果没域名，写 `http://你服务器IP:5000`）

### 6.2 重新打包 exe

```bat
cd D:\bs\xhs_scraper
build_exe.bat
```

这次打包的 `QiQiCollector.exe` 启动后会连你的服务器，激活/校验/拿默认 AI Key/上报心跳都走宝塔。

---

## 七、运营流程

### 卖卡密给客户

1. 后台 → 🔑 卡密管理 → 选套餐（日卡/周卡/月卡/季卡/年卡）→ 数量 → 生成
2. 复制卡密发给客户
3. 客户在客户端激活 → 看到 ✓ 激活成功

### 监控客户使用

1. 后台 → 👥 用户监控 → 看每个客户的 IP、操作量、版本
2. 发现某个客户 5 分钟内点赞 1000 次（明显刷得太凶）→ 点【封禁】
3. 该机器下次启动客户端时被驱逐

### 推送公告

1. 后台 → 📢 公告 → 写标题 + 内容 → 选级别（普通/重要/紧急）→ 发布
2. 所有客户端启动时自动弹窗

---

## 🔧 常见问题

### Q1: 客户端激活失败 `无法连接授权服务器`
- 服务器防火墙没开 5000 端口
- Nginx 反代没生效
- 服务端 gunicorn 没启动 → 宝塔 Python 项目 Tab 看状态

### Q2: 后台 👥 用户监控 IP 全是 127.0.0.1
- Nginx 配置里没传 `X-Real-IP`，回到第 4.2 步加上

### Q3: 客户端日志显示 `DeepSeek: ✗ 未配置`
- 后台 🤖 AI 默认 Key 没填
- 或客户端缓存还没过 30 分钟（重启客户端立即生效）

### Q4: 改了套餐限额，客户端还用老的延时
- 客户端的 limits 在 validate 时下发，需要等下次启动；
- 让客户重启客户端即可。

### Q5: 担心 sqlite 数据丢失
- 数据在 `/www/wwwroot/qiqi/license.db`
- 宝塔 → 计划任务 → 添加 Shell 脚本，每天备份：
  ```bash
  cp /www/wwwroot/qiqi/license.db /www/backup/qiqi_$(date +%Y%m%d).db
  find /www/backup/ -name 'qiqi_*.db' -mtime +30 -delete
  ```

### Q6: 想看实时日志
- 宝塔 → Python 项目 → qiqi-license-server → 日志按钮
- 或终端：`tail -f /www/wwwroot/qiqi/logs/*.log`

### Q7: 想热更新代码
1. 上传新代码到 `/www/wwwroot/qiqi/`
2. 宝塔 → Python 项目 → 点【重启】

---

## 🛡 安全加固（上线后必做）

1. **改默认管理员 Token**（部署时已经做了）

2. **限制后台访问 IP**（可选）
   - Nginx 配置里加（只允许你的办公 IP 访问 /admin）：
   ```nginx
   location /admin {
       allow 你的固定IP;
       deny all;
       proxy_pass http://127.0.0.1:5000;
       ...
   }
   ```

3. **fail2ban 防爆破**（宝塔自带）
   - 宝塔 → 安全 → SSH防爆破 / 网站防爆破 都开起来

4. **关闭 sqlite 文件的 web 访问**
   - 默认 Flask 不会暴露 `.db` 文件，但反代要确保也不会
   - Nginx 加：
   ```nginx
   location ~ \.(db|log|json)$ { deny all; }
   ```

5. **定期升级宝塔到最新版**

---

## 📋 部署清单（打勾确认）

- [ ] 宝塔面板 ≥ 9.0
- [ ] 已安装 Python 3.10
- [ ] 项目代码已上传到 `/www/wwwroot/qiqi/`
- [ ] Python 项目已创建并启动（状态绿）
- [ ] 环境变量 ADMIN_TOKEN 已设为强随机串
- [ ] 防火墙 5000 端口已开（或反代 80/443）
- [ ] 域名已绑定并配 SSL 证书
- [ ] Nginx 反代里 X-Real-IP 头已传
- [ ] 后台 /admin 已能登录
- [ ] 后台已填 DeepSeek 默认 Key
- [ ] 客户端 `license_server.txt` 已指向新服务器
- [ ] 已测试：本地客户端能激活卡密、拉到 AI Key、心跳能在监控面板看到自己

完成！🎉
