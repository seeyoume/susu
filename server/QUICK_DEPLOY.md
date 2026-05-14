# 🚀 5 分钟极速部署（宝塔）

> 详细版看 `DEPLOY_BAOTA.md`，这里是最少步骤版

## 步骤一：服务器准备（1 分钟）

宝塔面板 → 软件商店 → 装这两个：
- ✅ **Python 项目管理器**（3.0+）
- ✅ **Nginx**（已装跳过）

云服务器安全组：开放 `5000`、`80`、`443` 端口

## 步骤二：上传代码（1 分钟）

1. Windows 上把 `D:\bs\xhs_scraper\server\` 整个文件夹**压缩成 zip**
2. 宝塔 → 文件 → 进到 `/www/wwwroot/` → 新建文件夹 `qiqi`
3. 进 `qiqi` 文件夹 → 上传 zip → 右键解压
4. 把解压出来的 `server/` 里的所有文件 **移到外层 `qiqi/`**

最终结构：
```
/www/wwwroot/qiqi/
├── app.py
├── admin_cli.py
├── requirements.txt
├── templates/
└── sign_js/
```

## 步骤三：一键启动（2 分钟）

打开宝塔终端，复制粘贴执行：

```bash
cd /www/wwwroot/qiqi
bash bootstrap.sh
```

脚本会自动：
- ✅ 装 Python 依赖
- ✅ 生成强随机 ADMIN_TOKEN
- ✅ 启动 gunicorn（端口 5000）
- ✅ 设为开机自启
- ✅ 打印你的登录 URL 和 Token

把脚本输出的 **登录地址** 和 **ADMIN_TOKEN** 记下来！

## 步骤四：登录后台并配置（1 分钟）

1. 浏览器打开脚本输出的地址（如 `http://你IP:5000/admin/login`）
2. 输入 ADMIN_TOKEN 登录
3. 进 **🤖 AI 默认 Key** Tab → 填 DeepSeek API Key → 保存

申请 DeepSeek Key：https://platform.deepseek.com/

## 步骤五：让客户端连服务器

在 Windows 编辑 `D:\bs\xhs_scraper\license_server.txt`：
```
http://你的服务器IP:5000
```

或者有域名：
```
https://api.你的域名.com
```

重新打包：
```bat
build_exe.bat
```

完成！现在你可以：
- 后台 → 🔑 生成卡密 → 卖给客户
- 后台 → 👥 实时看到所有用户的 IP / 操作量
- 后台 → 🚫 一键封禁恶意用户

---

## ⚠️ 上线前必做

绑域名 + HTTPS（不然心跳/激活走明文，卡密有可能被嗅探）。
照 `DEPLOY_BAOTA.md` 第 IV 章做。
