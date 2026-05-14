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
            return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            pass
    data = _generate(account)
    try:
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    except Exception:
        pass
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

    return f"""
(() => {{
    // ── 基础属性 ──────────────────────────────────────────────────
    try {{
        const def = (obj, k, v) => Object.defineProperty(obj, k, {{get: () => v, configurable: true}});
        def(navigator, 'hardwareConcurrency', {hw});
        def(navigator, 'deviceMemory', {mem});
        def(navigator, 'platform', {json.dumps(platform)});
        def(navigator, 'languages', [{json.dumps(locale)}, "zh", "en"]);
        def(screen, 'colorDepth', {color_depth});
        def(screen, 'pixelDepth', {color_depth});

        // ① webdriver 完全抹除
        def(navigator, 'webdriver', undefined);
        delete navigator.__proto__.webdriver;

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

        // ⑤ Navigator.userAgentData（Client Hints API，比较新的检测点）
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
            const getCtx = HTMLCanvasElement.prototype.getContext;
            HTMLCanvasElement.prototype.getContext = function(type, ...args) {{
                const ctx = getCtx.call(this, type, ...args);
                if (!ctx) return ctx;
                if (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl') {{
                    const origGetParam = ctx.getParameter.bind(ctx);
                    ctx.getParameter = function(param) {{
                        if (param === 37445) return 'Intel Inc.';               // UNMASKED_VENDOR
                        if (param === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER
                        return origGetParam(param);
                    }};
                }}
                return ctx;
            }};
        }} catch(e) {{}}

    }} catch (e) {{}}
}})();
"""
