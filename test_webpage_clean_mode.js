// 纯净模式 (clean mode) 前端冒烟测试: 用最小 DOM 桩运行 webpage.html 的内联脚本，
// 断言开启后只剩玩家(自己/队友/敌人)，物资与人机不再渲染，血量为零的角色也不再画血环，
// 关闭后恢复。普通模式仍保留血量为零角色的血环。
// 用法: node test_webpage_clean_mode.js <webpage.html 绝对路径>
const fs = require('fs');
const vm = require('vm');

const pagePath = process.argv[2];
if (!pagePath) { console.error('usage: node test_webpage_clean_mode.js <webpage.html>'); process.exit(2); }
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
// 与静态标记一致: <button id="resource-toggle" class="active" aria-pressed="true">
byId['resource-toggle']._class.add('active');

const qualityInputs = [0, 1, 2, 3, 4, 5, 6].map(q => {
    const el = makeEl('input');
    el.dataset.quality = String(q);
    el.checked = true;
    return el;
});

function walk(root, out) { root.children.forEach(c => { out.push(c); walk(c, out); }); return out; }

const store = { authToken: 'test-pw' };
const sandbox = {
    console, Math, JSON, Number, String, Object, Array, Set, Promise, isFinite,
    setInterval: () => 0, clearInterval: () => {}, setTimeout: () => 0,
    localStorage: {
        getItem: k => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: k => { delete store[k]; },
    },
    // 1 名本地玩家(同时是队友) + 2 名敌人 + 2 个人机；物资 1 件 + 带密码容器 1 个
    fetch: async () => ({
        status: 200,
        headers: { get: () => null },
        json: async () => ({
            entities: [
                { id: 1, team_id: 1, type: 'player', position: { x: 0, y: 0 }, has_health: true, health: 100, max_health: 100 },
                { id: 2, team_id: 2, type: 'player', position: { x: 100, y: 100 }, has_health: true, health: 50, max_health: 100 },
                { id: 3, team_id: 2, type: 'player', position: { x: 200, y: 200 }, has_health: true, health: 0, max_health: 100 },
                { id: 4, type: 'ai', position: { x: 300, y: 300 }, ai_type: 'ai' },
                { id: 5, type: 'ai', position: { x: 400, y: 400 }, ai_type: 'ai' },
            ],
            local_player: { id: 1, team_id: 1, yaw: 0 },
            teammates: [{ id: 1, team_id: 1 }],
            items: [
                { item_quality: 3, item_name: '蓝物资', position: { x: 50, y: 50 } },
                { item_quality: 0, item_name: '容器', type: 'container', has_password: true, password: 1234, position: { x: 60, y: 60 } },
            ],
        }),
    }),
    window: { addEventListener() {} },
    document: {
        getElementById: id => byId[id] || makeEl('div'),
        createElement: makeEl,
        querySelectorAll: sel => {
            if (sel === '#quality-filter input[data-quality]') return qualityInputs;
            const token = sel.replace(/^\./, '');
            return walk(byId['map'], []).filter(e => e._class.has(token) || String(e.className).split(/\s+/).includes(token));
        },
    },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(script, sandbox, { filename: 'webpage.html:inline-script' });

const map = byId['map'];
const entityContainers = () => map.children.filter(c => String(c.className).includes('entity-container') && !c._class.has('resource-marker'));
const markers = () => map.children.filter(c => c._class.has('resource-marker'));
const aiBadges = () => entityContainers().filter(c => c.children.some(ch => ch.textContent === 'A'));
const healthRings = () => entityContainers().flatMap(c => c.children.filter(ch => ch._class.has('entity-health-ring') || String(ch.className).split(/\s+/).includes('entity-health-ring')));
const itemLabels = () => markers().map(m => m.children.map(c => c.textContent).join('|'));

let failures = 0;
function check(name, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failures++;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}: got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
}

(async () => {
    await sandbox.updateGameData(true);
    check('默认: 实体数(3 玩家 + 2 人机)', entityContainers().length, 5);
    check('默认: 人机圆点', aiBadges().length, 2);
    check('默认: 物资标记', markers().length, 2);
    check('默认: 血环(含血量为0的角色)', healthRings().length, 3);
    check('默认: 资源按钮 active', byId['resource-toggle']._class.has('active'), true);
    check('默认: 纯净按钮未激活', byId['clean-toggle']._class.has('active'), false);
    check('默认: 容器密码行', itemLabels().some(t => t.includes('密码 1234')), true);

    sandbox.toggleCleanMode();
    await sandbox.updateGameData(true);
    check('纯净: 实体数(仅玩家)', entityContainers().length, 3);
    check('纯净: 人机圆点', aiBadges().length, 0);
    check('纯净: 物资标记', markers().length, 0);
    check('纯净: 血环只留存活角色', healthRings().length, 2);
    check('纯净: 纯净按钮 active', byId['clean-toggle']._class.has('active'), true);
    check('纯净: 资源按钮取消激活', byId['resource-toggle']._class.has('active'), false);
    check('纯净: 资源按钮禁用', byId['resource-toggle'].disabled, true);
    check('纯净: 品质筛选置灰', byId['quality-filter']._class.has('disabled'), true);
    check('纯净: 已持久化', store.cleanMode, '1');
    check('纯净: 资源按钮点击被忽略', (sandbox.toggleResourceVisibility(), markers().length), 0);

    sandbox.toggleCleanMode();
    await sandbox.updateGameData(true);
    check('恢复: 实体数', entityContainers().length, 5);
    check('恢复: 人机圆点', aiBadges().length, 2);
    check('恢复: 物资标记', markers().length, 2);
    check('恢复: 血环', healthRings().length, 3);
    check('恢复: 资源按钮重新启用', byId['resource-toggle'].disabled, false);
    check('恢复: 已持久化', store.cleanMode, '0');

    console.log(failures === 0 ? '\nALL PASS' : `\n${failures} FAILURE(S)`);
    process.exit(failures === 0 ? 0 : 1);
})();
