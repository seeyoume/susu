"""绮绮采集器 - 浏览器指纹随机化
为每个账号生成稳定的指纹（UA / viewport / timezone / 平台 / WebGL hint）
- 同一账号每次启动指纹固定（避免风控）
- 不同账号指纹独立（防多号关联）

存储：accounts/{alias}.fp.json
"""
import hashlib
import json
import random
from pathlib import Path

ACCOUNTS_DIR = Path(__file__).parent / "accounts"

# 真实 Chrome / Edge UA 池（Windows 11 + macOS 14）
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# 常见 viewport（避开极端值）
VIEWPORT_POOL = [
    (1920, 1080), (1536, 864), (1440, 900), (1366, 768),
    (1600, 900), (1680, 1050), (1280, 800), (2560, 1440),
]

# 中国主要时区都是 +8，但写法不同
TIMEZONE_POOL = ["Asia/Shanghai", "Asia/Hong_Kong"]

# 语言
LOCALE_POOL = ["zh-CN", "zh-CN", "zh-CN", "zh-TW"]  # 偏 zh-CN

# 屏幕色深
COLOR_DEPTH_POOL = [24, 30]


def _fp_file(account):
    ACCOUNTS_DIR.mkdir(exist_ok=True)
    return ACCOUNTS_DIR / f"{account}.fp.json"


def _generate(account):
    """ 用 account 名作种子生成稳定指纹 """
    seed = int(hashlib.md5(account.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)
    w, h = rng.choice(VIEWPORT_POOL)
    return {
        "user_agent": rng.choice(UA_POOL),
        "viewport": {"width": w, "height": h},
        "screen": {"width": w, "height": h, "color_depth": rng.choice(COLOR_DEPTH_POOL)},
        "timezone": rng.choice(TIMEZONE_POOL),
        "locale": rng.choice(LOCALE_POOL),
        "platform": "Win32" if "Windows" in rng.choice(UA_POOL) else "MacIntel",
        "hardware_concurrency": rng.choice([4, 6, 8, 12, 16]),
        "device_memory": rng.choice([4, 8, 16]),
    }


def get_fingerprint(account):
    """ 读取或生成账号指纹（每个账号稳定） """
    fp = _fp_file(account)
    if fp.exists():
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            data["_account_name"] = account  # 注入账号名（运行时用作噪声种子）
            return data
        except Exception:
            pass
    data = _generate(account)
    try:
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    except Exception:
        pass
    data["_account_name"] = account
    return data


def regenerate(account):
    """ 强制重新生成（更换指纹） """
    fp = _fp_file(account)
    # 加点扰动让结果变化
    import time
    seed_str = f"{account}-{time.time()}"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    w, h = rng.choice(VIEWPORT_POOL)
    data = {
        "user_agent": rng.choice(UA_POOL),
        "viewport": {"width": w, "height": h},
        "screen": {"width": w, "height": h, "color_depth": rng.choice(COLOR_DEPTH_POOL)},
        "timezone": rng.choice(TIMEZONE_POOL),
        "locale": rng.choice(LOCALE_POOL),
        "platform": "Win32",
        "hardware_concurrency": rng.choice([4, 6, 8, 12, 16]),
        "device_memory": rng.choice([4, 8, 16]),
    }
    fp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return data


def make_init_script(fp):
    """ 生成注入到页面前置的 JS，覆盖 navigator 属性，规避 WebGL/Canvas 等指纹检测 """
    hw = fp.get('hardware_concurrency', 8)
    mem = fp.get('device_memory', 8)
    platform = fp.get('platform', 'Win32')
    color_depth = fp.get('screen', {}).get('color_depth', 24)
    locale = fp.get('locale', 'zh-CN')
    ua = fp.get('user_agent', '')
    # 从 UA 解析 Chrome 大版本号用于 client hints
    import re as _re
    _m = _re.search(r'Chrome/(\d+)', ua)
    chrome_major = _m.group(1) if _m else '131'
    is_edge = 'Edg/' in ua
    brand_list = (
        f'[{{"brand":"Microsoft Edge","version":"{chrome_major}"}},'
        f'{{"brand":"Chromium","version":"{chrome_major}"}},'
        f'{{"brand":"Not-A.Brand","version":"99"}}]'
        if is_edge else
        f'[{{"brand":"Google Chrome","version":"{chrome_major}"}},'
        f'{{"brand":"Chromium","version":"{chrome_major}"}},'
        f'{{"brand":"Not-A.Brand","version":"99"}}]'
    )

    # 用 account 名当种子（不用 UA，避免多账号同 UA 撞同一个噪声）
    import hashlib as _hl
    _seed_src = fp.get('_account_name') or fp.get('account') or ua
    _seed_hex = _hl.md5(str(_seed_src).encode()).hexdigest()[:8]
    canvas_r = int(_seed_hex[0:2], 16)      # 0-255
    canvas_g = int(_seed_hex[2:4], 16)
    canvas_b = int(_seed_hex[4:6], 16)
    audio_shift = round(1e-6 + int(_seed_hex[6:8], 16) * 3e-9, 12)  # 极小偏移
    avail_w = fp.get("viewport", {}).get("width", 1920) - random.randint(0, 2)
    avail_h = fp.get("viewport", {}).get("height", 1080) - random.randint(40, 48)  # 任务栏

    return f"""
(() => {{
    // ── 基础属性 ──────────────────────────────────────────────────
    try {{
        const def = (obj, k, v) => Object.defineProperty(obj, k, {{get: () => v, configurable: true}});
        def(navigator, 'hardwareConcurrency', {hw});
        def(navigator, 'deviceMemory', {mem});
        def(navigator, 'platform', {json.dumps(platform)});
        def(navigator, 'languages', [{json.dumps(locale)}, "zh", "en"]);
        def(navigator, 'maxTouchPoints', 0);
        def(screen, 'colorDepth', {color_depth});
        def(screen, 'pixelDepth', {color_depth});
        def(screen, 'availWidth',  {avail_w});
        def(screen, 'availHeight', {avail_h});

        // ① webdriver 完全抹除
        try {{ delete navigator.__proto__.webdriver; }} catch(e) {{}}
        def(navigator, 'webdriver', undefined);

        // ② navigator.plugins：真实 Chrome 至少有 3 个 plugin
        const fakePDF = {{
            0: {{type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format'}},
            name: 'PDF Viewer', description: 'Portable Document Format', filename: 'internal-pdf-viewer',
            length: 1
        }};
        const fakePlugins = [
            Object.assign(Object.create(Plugin.prototype), {{...fakePDF, name: 'PDF Viewer'}}),
            Object.assign(Object.create(Plugin.prototype), {{...fakePDF, name: 'Chrome PDF Viewer'}}),
            Object.assign(Object.create(Plugin.prototype), {{...fakePDF, name: 'Chromium PDF Plugin'}}),
        ];
        fakePlugins.length = 3;
        fakePlugins.refresh = () => {{}};
        def(navigator, 'plugins', fakePlugins);
        def(navigator, 'mimeTypes', []);

        // ③ permissions.query：将 'notifications' 返回 'default' 而非 'denied'
        const _origPerms = navigator.permissions && navigator.permissions.query.bind(navigator.permissions);
        if (_origPerms) {{
            navigator.permissions.query = (params) => {{
                if (params && params.name === 'notifications') {{
                    return Promise.resolve({{state: 'default', onchange: null}});
                }}
                return _origPerms(params);
            }};
        }}

        // ④ window.chrome 对象（Playwright 下缺失）
        if (!window.chrome) {{
            window.chrome = {{
                app: {{isInstalled: false, InstallState: {{}}, RunningState: {{}}}},
                runtime: {{
                    PlatformOs: {{}}, PlatformArch: {{}}, PlatformNaclArch: {{}},
                    RequestUpdateCheckStatus: {{}}, OnInstalledReason: {{}}, OnRestartRequiredReason: {{}},
                    connect: () => ({{postMessage: () => {{}}, disconnect: () => {{}}, onMessage: {{addListener: () => {{}}}}, onDisconnect: {{addListener: () => {{}}}}}}),
                    sendMessage: () => {{}},
                }},
                loadTimes: () => ({{
                    requestTime: Date.now()/1000 - Math.random()*0.3,
                    startLoadTime: Date.now()/1000 - Math.random()*0.2,
                    commitLoadTime: Date.now()/1000 - Math.random()*0.1,
                    finishDocumentLoadTime: Date.now()/1000,
                    finishLoadTime: Date.now()/1000,
                    firstPaintTime: Date.now()/1000,
                    firstPaintAfterLoadTime: 0,
                    navigationType: 'Other',
                    wasFetchedViaSpdy: false,
                    wasNpnNegotiated: true,
                    npnNegotiatedProtocol: 'h2',
                    wasAlternateProtocolAvailable: false,
                    connectionInfo: 'h2',
                }}),
                csi: () => ({{startE: Date.now(), onloadT: Date.now(), pageT: Math.random()*3000+500, tran: 15}}),
            }};
        }}

        // ⑤ Navigator.userAgentData（Client Hints API）
        try {{
            const brands = {brand_list};
            const uaData = {{
                brands: brands,
                mobile: false,
                platform: {json.dumps(platform.replace('Win32','Windows').replace('MacIntel','macOS'))},
                getHighEntropyValues: (hints) => Promise.resolve({{
                    brands: brands,
                    mobile: false,
                    platform: {json.dumps(platform.replace('Win32','Windows').replace('MacIntel','macOS'))},
                    architecture: 'x86',
                    bitness: '64',
                    model: '',
                    platformVersion: '15.0.0',
                    uaFullVersion: '{chrome_major}.0.0.0',
                    fullVersionList: brands,
                }}),
            }};
            def(navigator, 'userAgentData', uaData);
        }} catch(e) {{}}

        // ⑥ WebGL vendor/renderer 覆盖（防 SwiftShader / ANGLE 泄露）
        try {{
            const _getCtx = HTMLCanvasElement.prototype.getContext;
            HTMLCanvasElement.prototype.getContext = function(type, ...args) {{
                const ctx = _getCtx.call(this, type, ...args);
                if (!ctx) return ctx;
                if (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl') {{
                    const _orig = ctx.getParameter.bind(ctx);
                    ctx.getParameter = function(param) {{
                        if (param === 37445) return 'Intel Inc.';
                        if (param === 37446) return 'Intel Iris OpenGL Engine';
                        return _orig(param);
                    }};
                }}
                return ctx;
            }};
        }} catch(e) {{}}

        // ⑦ Canvas 2D 指纹噪声（每个账号稳定但唯一的像素偏移）
        try {{
            const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
            const _getImageData = CanvasRenderingContext2D.prototype.getImageData;
            const _noise = [{canvas_r}, {canvas_g}, {canvas_b}];
            HTMLCanvasElement.prototype.toDataURL = function(...args) {{
                const ctx2d = this.getContext('2d');
                if (ctx2d) {{
                    const w = this.width, h = this.height;
                    if (w > 0 && h > 0) {{
                        const id = ctx2d.getImageData(0, 0, 1, 1);
                        // 仅对像素 (0,0) 微调，视觉无感
                        id.data[0] = (id.data[0] + _noise[0]) & 0xff;
                        id.data[1] = (id.data[1] + _noise[1]) & 0xff;
                        id.data[2] = (id.data[2] + _noise[2]) & 0xff;
                        ctx2d.putImageData(id, 0, 0);
                    }}
                }}
                return _toDataURL.apply(this, args);
            }};
        }} catch(e) {{}}

        // ⑧ AudioContext 指纹噪声（极小偏移，改变 getChannelData 输出）
        try {{
            const _AudioContext = window.AudioContext || window.webkitAudioContext;
            if (_AudioContext) {{
                const _getChData = AudioBuffer.prototype.getChannelData;
                AudioBuffer.prototype.getChannelData = function(channel) {{
                    const arr = _getChData.call(this, channel);
                    // 仅改首尾两个采样，幅度在 1e-9 量级，听觉完全无感
                    if (arr.length > 2) {{
                        arr[0] += {audio_shift};
                        arr[arr.length - 1] -= {audio_shift};
                    }}
                    return arr;
                }};
            }}
        }} catch(e) {{}}

        // ⑨ document.visibilityState 始终 'visible'（防后台检测）
        try {{
            Object.defineProperty(document, 'visibilityState', {{get: () => 'visible'}});
            Object.defineProperty(document, 'hidden', {{get: () => false}});
        }} catch(e) {{}}

        // ⑩ WebRTC 屏蔽（防代理场景下真实 IP 泄露）
        try {{
            const _block = function() {{
                throw new Error('WebRTC blocked');
            }};
            // 直接 undefined 太明显，改为返回一个"看似正常但拿不到 candidate"的对象
            const _RTC = window.RTCPeerConnection || window.webkitRTCPeerConnection;
            if (_RTC) {{
                const _Wrap = function(...args) {{
                    const pc = new _RTC(...args);
                    const _addIce = pc.addIceCandidate ? pc.addIceCandidate.bind(pc) : null;
                    // 仅过滤掉 srflx/host 类型的 candidate（暴露 IP 的）
                    Object.defineProperty(pc, 'onicecandidate', {{
                        set: function(handler) {{
                            this._origHandler = handler;
                            this._wrapped = function(ev) {{
                                if (ev && ev.candidate && ev.candidate.candidate) {{
                                    const c = ev.candidate.candidate;
                                    if (c.includes('typ srflx') || c.includes('typ host')) return;
                                }}
                                if (handler) handler(ev);
                            }};
                            _RTC.prototype.__lookupSetter__('onicecandidate').call(this, this._wrapped);
                        }},
                        get: function() {{ return this._origHandler; }}
                    }});
                    return pc;
                }};
                _Wrap.prototype = _RTC.prototype;
                window.RTCPeerConnection = _Wrap;
                if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = _Wrap;
            }}
        }} catch(e) {{}}

        // ⑪ Function.prototype.toString 代理（防"看 toString 是否 native"的检测）
        try {{
            const _nativeToString = Function.prototype.toString;
            const _toStringStr = _nativeToString.call(_nativeToString);
            const _markedFns = new WeakSet();
            Function.prototype.toString = new Proxy(_nativeToString, {{
                apply: function(target, thisArg, args) {{
                    if (_markedFns.has(thisArg)) {{
                        // 被我们改写过的函数，返回伪造的 native code 字符串
                        return 'function ' + (thisArg.name || '') + '() {{ [native code] }}';
                    }}
                    return Reflect.apply(target, thisArg, args);
                }}
            }});
            // 标记几个常见被检测的函数
            try {{ _markedFns.add(navigator.permissions.query); }} catch(e) {{}}
            try {{ _markedFns.add(HTMLCanvasElement.prototype.toDataURL); }} catch(e) {{}}
            try {{ _markedFns.add(HTMLCanvasElement.prototype.getContext); }} catch(e) {{}}
            try {{ _markedFns.add(AudioBuffer.prototype.getChannelData); }} catch(e) {{}}
            try {{ _markedFns.add(Function.prototype.toString); }} catch(e) {{}}
        }} catch(e) {{}}

        // ⑫ 字体指纹：让字体探测返回稳定的"普通 Windows/Mac"字体集
        try {{
            if (document.fonts && document.fonts.check) {{
                const _commonFonts = new Set([
                    'Arial','Arial Black','Calibri','Cambria','Comic Sans MS','Consolas',
                    'Courier New','Georgia','Helvetica','Impact','Lucida Console',
                    'Microsoft YaHei','Microsoft Sans Serif','PingFang SC','SimSun','SimHei',
                    '宋体','黑体','微软雅黑','Tahoma','Times New Roman','Trebuchet MS',
                    'Verdana','sans-serif','serif','monospace'
                ]);
                const _origCheck = document.fonts.check.bind(document.fonts);
                document.fonts.check = function(spec, text) {{
                    try {{
                        for (const f of _commonFonts) {{
                            if (spec.includes('"' + f + '"') || spec.includes(f)) return true;
                        }}
                    }} catch(e) {{}}
                    return false;
                }};
            }}
        }} catch(e) {{}}

        // ⑬ Notification.permission 默认值（自动化下常返回 'denied'，真实浏览器是 'default'）
        try {{
            if (window.Notification) {{
                Object.defineProperty(Notification, 'permission', {{get: () => 'default'}});
            }}
        }} catch(e) {{}}

        // ⑭ Battery API（部分检测看 charging 是否永远 true）
        try {{
            if (navigator.getBattery) {{
                navigator.getBattery = () => Promise.resolve({{
                    charging: true,
                    chargingTime: Infinity,
                    dischargingTime: Infinity,
                    level: 0.84 + Math.random() * 0.15,
                    addEventListener: () => {{}},
                    removeEventListener: () => {{}},
                }});
            }}
        }} catch(e) {{}}

        // ⑮ Connection API 一致性（移动 vs PC 时网络类型不应是 cellular）
        try {{
            if (navigator.connection) {{
                Object.defineProperty(navigator.connection, 'rtt',           {{get: () => 50}});
                Object.defineProperty(navigator.connection, 'downlink',       {{get: () => 10}});
                Object.defineProperty(navigator.connection, 'effectiveType',  {{get: () => '4g'}});
                Object.defineProperty(navigator.connection, 'saveData',       {{get: () => false}});
            }}
        }} catch(e) {{}}

    }} catch (e) {{}}
}})();
"""
