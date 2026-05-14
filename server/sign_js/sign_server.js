// 绮绮采集器 - XHS 签名持久服务
// 通过 stdin/stdout 与 Python 通信
const path = require('path');
const readline = require('readline');

// 静默 xhs_main.js 内的 console 噪音（它会打印代理 trap 日志）
const _origLog = console.log;
const _origError = console.error;
console.log = function () {};
console.error = function () {};

const xhs_main = require(path.join(__dirname, 'xhs_main.js'));

let rap_generate = null;
try {
    const xhs_rap = require(path.join(__dirname, 'xhs_rap.js'));
    if (typeof xhs_rap === 'function') rap_generate = xhs_rap;
    else if (xhs_rap && typeof xhs_rap.generate_x_rap_param === 'function') {
        rap_generate = xhs_rap.generate_x_rap_param;
    } else if (typeof generate_x_rap_param === 'function') {
        rap_generate = generate_x_rap_param;
    } else if (global.generate_x_rap_param) {
        rap_generate = global.generate_x_rap_param;
    }
} catch (e) {
    // 不致命
}

// 用 process.stdout.write 直接输出，避免被 console 拦截
function out(obj) {
    process.stdout.write(JSON.stringify(obj) + '\n');
}

const rl = readline.createInterface({ input: process.stdin });

rl.on('line', (line) => {
    let req;
    try {
        req = JSON.parse(line);
    } catch (e) {
        out({ ok: false, err: 'bad json' });
        return;
    }
    try {
        const api = req.api;
        const data = req.data || '';
        const a1 = req.a1 || '';
        const method = req.method || 'POST';

        const sig = xhs_main.get_request_headers_params(api, data, a1, method);
        const o = {
            ok: true,
            'x-s': sig.xs,
            'x-t': String(sig.xt),
            'x-s-common': sig.xs_common,
        };

        if (rap_generate) {
            try {
                const data_str = (typeof data === 'string') ? data : JSON.stringify(data);
                o['x-rap-param'] = rap_generate(api, data_str || '', null);
            } catch (e) {
                // 缺 x-rap-param 不致命
            }
        }
        out(o);
    } catch (e) {
        out({ ok: false, err: e.message || String(e) });
    }
});

rl.on('close', () => process.exit(0));
process.on('SIGTERM', () => process.exit(0));
