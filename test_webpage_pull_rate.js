// 60Hz 拉取契约冒烟测试: 用最小 DOM/定时器桩运行 webpage.html 的内联脚本，
// 断言轮询间隔约 16ms、请求带上一版本的 X-Data-Token、服务端 204 时不重复渲染，
// 数据变化后重新渲染，且长时间无变化时携带 X-Data-No-Cache 兜底强制重建。
// 用法: node test_webpage_pull_rate.js <webpage.html 绝对路径>
const fs = require('fs');
const vm = require('vm');

const pagePath = process.argv[2];
if (!pagePath) { console.error('usage: node test_webpage_pull_rate.js <webpage.html>'); process.exit(2); }
const html = fs.readFileSync(pagePath, 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function makeEl(tag) {
    const el = {
        tagName: tag, children: [], dataset: {}, _class: new Set(),
        style: { setProperty(k, v) { this[k] = v; }, getPropertyValue(k) { return this[k]; } },
        title: '', textContent: '', value: '', disabled: false, className: '',
        appendChild(c) { el.children.push(c); c.parent = el; return c; },
        setAttribute(k, v) { el['attr_' + k] = v; },
        getAttribute(k) { return el['attr_' + k]; },
        addEventListener() {},
        remove() { if (el.parent) { const i = el.parent.children.indexOf(el); if (i >= 0) el.parent.children.splice(i, 1); } },
        getBoundingClientRect() { return { width: 800, height: 600, left: 0, top: 0 }; },
    };
    el.classList = {
        add: (...c) => c.forEach(x => el._class.add(x)),
        remove: (...c) => c.forEach(x => el._class.delete(x)),
        contains: c => el._class.has(c),
        toggle: (c, on) => {
            const want = on === undefined ? !el._class.has(c) : !!on;
            if (want) el._class.add(c); else el._class.delete(c);
            return want;
        },
    };
    let htmlStr = '';
    Object.defineProperty(el, 'innerHTML', {
        get: () => htmlStr,
        set: v => { htmlStr = v; if (v === '') el.children.length = 0; },
    });
    return el;
}

const ids = ['map-container', 'map', 'auth-input', 'auth-container', 'auth-error',
             'resource-toggle', 'clean-toggle', 'quality-filter', 'quality-filter-title',
             'calibration-status', 'map-buttons', 'player-buttons', 'auth-submit'];
const byId = {};
ids.forEach(id => { byId[id] = makeEl('div'); });
byId['auth-input'].value = 'test-pw';

const qualityInputs = [0, 1, 2, 3, 4, 5, 6].map(q => {
    const el = makeEl('input');
    el.dataset.quality = String(q);
    el.checked = true;
    return el;
});

// ---- 假时钟：脚本只使用 Date.now() 与 setTimeout ----
let fakeNow = 0;
const timers = [];
const delays = [];
function makePayload(snapshotId, enemyX) {
    return {
        entities: [
            { id: 1, team_id: 1, type: 'player', position: { x: 0, y: 0 }, has_health: true, health: 100, max_health: 100 },
            { id: 2, team_id: 2, type: 'player', position: { x: enemyX, y: 100 }, has_health: true, health: 50, max_health: 100 },
        ],
        local_player: { id: 1, team_id: 1, yaw: 0 },
        teammates: [{ id: 1, team_id: 1 }],
        items: [],
        meta: { snapshot_id: snapshotId },
    };
}

// ---- 假服务端：按 X-Data-Token 决定 200/204，按 X-Data-No-Cache 强制重建 ----
let serverSnapshot = 1;
const tokenFor = snapshot => 'tok-' + snapshot;   // 与服务端一致：token 由内容决定
let renderRebuilds = 0;
const requests = [];
let created = 0;

const store = { authToken: 'test-pw' };
const sandbox = {
    console, Math, JSON, Number, String, Object, Array, Set, Promise, isFinite,
    Date: { now: () => fakeNow },
    setTimeout: (fn, ms) => { timers.push(fn); delays.push(ms); return timers.length; },
    clearTimeout: () => {},
    localStorage: {
        getItem: k => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: k => { delete store[k]; },
    },
    fetch: async (url, init) => {
        const headers = (init && init.headers) || {};
        requests.push({ url, headers: { ...headers } });
        const token = tokenFor(serverSnapshot);
        if (headers['X-Data-No-Cache']) renderRebuilds += 1;   // 强制重建：忽略客户端 token
        else if (headers['X-Data-Token'] === token) {
            return { status: 204, headers: { get: () => token }, json: async () => { throw new Error('204 不应被解析'); } };
        }
        const payload = makePayload(serverSnapshot, 100 + serverSnapshot);
        return { status: 200, headers: { get: () => token }, json: async () => JSON.parse(JSON.stringify(payload)) };
    },
    window: { addEventListener() {} },
    document: {
        getElementById: id => byId[id] || makeEl('div'),
        createElement: tag => { created += 1; return makeEl(tag); },
        querySelectorAll: sel => {
            if (sel === '#quality-filter input[data-quality]') return qualityInputs;
            const token = sel.replace(/^\./, '');
            const out = [];
            (function walk(root) {
                root.children.forEach(c => {
                    if (c._class.has(token) || String(c.className).split(/\s+/).includes(token)) out.push(c);
                    walk(c);
                });
            })(byId['map']);
            return out;
        },
    },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(script, sandbox, { filename: 'webpage.html:inline-script' });

let failures = 0;
function check(name, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failures++;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}: got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
}
// 推进假时钟并执行一个到期定时器（脚本每轮只注册一个），返回该轮发出的请求。
async function poll(ms) {
    const index = requests.length;
    fakeNow += ms;
    const fn = timers.shift();
    if (!fn) throw new Error('没有待执行的轮询定时器');
    await fn();
    const sent = requests.slice(index);
    if (sent.length !== 1) throw new Error(`一轮应只发一次请求，实际 ${sent.length}`);
    return sent[0];
}

const flush = () => new Promise(resolve => setTimeout(resolve, 0));

(async () => {
    // 脚本加载时自带首屏渲染（selectMap 与显式调用各一次，未 await），先让它结算。
    await flush();
    check('首屏: 请求都不带 token', requests.every(r => !('X-Data-Token' in r.headers)), true);
    check('首屏: 渲染出 2 个实体', byId['map'].children.length, 2);
    const afterFirst = created;

    // 1) 一次定时轮询：应带上首屏拿到的 token，服务端回 204，因此不再渲染。
    check('轮询定时器已注册', timers.length, 1);
    check('轮询间隔 16ms', delays[0], 16);
    check('轮询: 携带上一版本 token', (await poll(delays[0])).headers['X-Data-Token'], 'tok-1');
    check('204: 未重新渲染', created, afterFirst);
    check('204: 实体数量不变', byId['map'].children.length, 2);

    // 2) 服务端数据变化 -> 前端应在下一轮重新解析并渲染。
    serverSnapshot = 2;
    check('数据变化: 请求仍带上一版本 token', (await poll(16)).headers['X-Data-Token'], 'tok-1');
    check('数据变化: 重新渲染', created > afterFirst, true);
    check('数据变化: 实体数量不变', byId['map'].children.length, 2);
    const afterChange = created;

    // 3) 长时间无变化：兜底请求带 X-Data-No-Cache 强制重建，服务端返回完整负载。
    const fallback = await poll(2001);
    check('兜底: 携带 X-Data-No-Cache', fallback.headers['X-Data-No-Cache'], '1');
    check('兜底: 不携带旧 token', 'X-Data-Token' in fallback.headers, false);
    check('兜底: 服务端强制重建一次', renderRebuilds, 1);
    check('兜底: 收到完整负载后重绘', created > afterChange, true);

    // 4) 兜底之后回到普通轮询，并继续按 60Hz 排程。
    check('兜底后: 恢复携带 token', (await poll(16)).headers['X-Data-Token'], 'tok-2');
    check('兜底后: 未再次强制重建', renderRebuilds, 1);
    check('持续排程: 每轮都注册定时器', timers.length, 1);

    console.log(failures === 0 ? '\nALL PASS' : `\n${failures} FAILURE(S)`);
    process.exit(failures === 0 ? 0 : 1);
})();
