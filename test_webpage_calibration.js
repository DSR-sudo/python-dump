// 校准模式前端冒烟测试: 用最小 DOM 桩运行 webpage.html 的内联脚本，
// 断言点地图取点会按 offset/scale 换算成地图像素写进当前地图的 point2D、
// 拖拽不取点、「用当前位置」填世界坐标、「用选中实体」填实体世界坐标（点实体标记
// 是选中而不是取点）、代码块可粘回 mapConverters，关闭校准后不再画十字标记。
// 用法: node test_webpage_calibration.js <webpage.html 绝对路径>
const fs = require('fs');
const vm = require('vm');

const pagePath = process.argv[2];
if (!pagePath) { console.error('usage: node test_webpage_calibration.js <webpage.html>'); process.exit(2); }
const html = fs.readFileSync(pagePath, 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function makeEl(tag) {
    const el = {
        tagName: tag, children: [], dataset: {}, _class: new Set(),
        style: { setProperty(k, v) { this[k] = v; }, getPropertyValue(k) { return this[k]; } },
        title: '', textContent: '', value: '', disabled: false, className: '',
        appendChild(c) { el.children.push(c); c.parent = el; c.parentNode = el; return c; },
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
             'resource-toggle', 'clean-toggle', 'quality-filter', 'calibration-status',
             'map-buttons', 'player-buttons', 'auth-submit',
             'calibrate-toggle', 'calibrate-panel', 'cal-hint', 'cal-code',
             'cal-pick-1', 'cal-pick-2', 'cal-here-1', 'cal-here-2', 'cal-copy', 'cal-revert',
             'cal-entity', 'cal-ent-1', 'cal-ent-2',
             'cal-w1x', 'cal-w1y', 'cal-m1x', 'cal-m1y', 'cal-w2x', 'cal-w2y', 'cal-m2x', 'cal-m2y'];
const byId = {};
ids.forEach(id => { byId[id] = makeEl('div'); });
byId['auth-input'].value = 'test-pw';

// 记录 map-container 上的监听器，测试里模拟点击/拖拽
const listeners = {};
byId['map-container'].addEventListener = (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); };
const fire = (type, event) => (listeners[type] || []).forEach(fn => fn({ preventDefault() {}, ...event }));
const tap = (x, y) => { fire('mousedown', { clientX: x, clientY: y }); fire('click', { clientX: x, clientY: y }); };

function walk(root, out) { root.children.forEach(c => { out.push(c); walk(c, out); }); return out; }

const store = { authToken: 'test-pw' };
// 每轮 updateGameData 的快照内容；测试中途可改（例如让实体消失）
const payload = {
    entities: [
        // 队友站在「后处理厂」附近，供「用选中实体」取世界坐标
        { id: 'record:42', type: 'player', team_id: 1, position: { x: 175587.44, y: -240315.06 },
          has_health: true, health: 88, max_health: 100, has_hero: false, has_weapon: false },
    ],
    // 玩家站在「后处理厂」附近，用于「用当前位置」取世界坐标
    local_player: { id: 'local', team_id: 1, yaw: 0, position: { x: 175587.44, y: -240315.06 } },
    teammates: [],
    items: [
        { id: 'record:77', type: 'item', item_quality: 3, item_name: '蓝物资', position: { x: 3000, y: 4000 } },
    ],
};
const sandbox = {
    console, Math, JSON, Number, String, Object, Array, Set, Promise, isFinite, Date,
    setInterval: () => 0, clearInterval: () => {}, setTimeout: () => 0,
    localStorage: {
        getItem: k => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: k => { delete store[k]; },
    },
    fetch: async () => ({
        status: 200,
        headers: { get: () => null },
        json: async () => payload,
    }),
    window: { addEventListener() {} },
    document: {
        getElementById: id => byId[id] || makeEl('div'),
        createElement: makeEl,
        querySelectorAll: sel => {
            const token = sel.replace(/^\./, '');
            return walk(byId['map'], []).filter(e => e._class.has(token) || String(e.className).split(/\s+/).includes(token));
        },
    },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(script, sandbox, { filename: 'webpage.html:inline-script' });
const evalIn = expr => vm.runInContext(expr, sandbox);

const map = byId['map'];
const markers = () => map.children.filter(c => String(c.className).includes('calibrate-marker'));
const converterOf = id => JSON.parse(evalIn(`JSON.stringify(mapConverters[${JSON.stringify(id)}])`));

let failures = 0;
function check(name, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failures++;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}: got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
}

(async () => {
    await sandbox.updateGameData(true);
    check('默认: 未画校准标记', markers().length, 0);

    // 原文件里 db.png 的校准值，作为「还原」的基准
    const dbOriginal = converterOf('db.png');

    sandbox.setCalibrateMode(true);
    check('开启: 面板打开', byId['calibrate-panel']._class.has('open'), true);
    check('开启: 按钮高亮', byId['calibrate-toggle']._class.has('active'), true);
    check('开启: 标记 1 落在 point2D_1 上', [markers()[0].style.left, markers()[0].style.top], ['470px', '245px']);
    check('开启: 标记 2 落在 point2D_2 上', [markers()[1].style.left, markers()[1].style.top], ['1025px', '946px']);
    check('开启: 输入框回填世界坐标', [byId['cal-w1x'].value, byId['cal-w1y'].value], [-16207, -27879]);
    check('开启: 默认取点 1', byId['cal-pick-1']._class.has('active'), true);

    // 屏幕(210,220) 在 scale=2 / offset(10,20) 下对应地图(100,100)
    evalIn('scale = 2; offsetX = 10; offsetY = 20;');
    tap(210, 220);
    check('取点: point2D_1 更新', converterOf('db.png').point2D_1, { x: 100, y: 100 });
    check('取点: 输入框回填', [byId['cal-m1x'].value, byId['cal-m1y'].value], [100, 100]);
    check('取点: 标记跟随', [markers()[0].style.left, markers()[0].style.top], ['100px', '100px']);

    // 拖拽（按下与抬起间隔超过阈值）不应取点
    fire('mousedown', { clientX: 100, clientY: 100 });
    fire('click', { clientX: 300, clientY: 300 });
    check('拖拽不取点', converterOf('db.png').point2D_1, { x: 100, y: 100 });

    sandbox.armCalibrateSlot(2);
    check('切换: 取点 2 高亮', byId['cal-pick-2']._class.has('active'), true);
    tap(410, 420);
    check('取点: point2D_2 更新', converterOf('db.png').point2D_2, { x: 200, y: 200 });

    sandbox.useCurrentPositionForPoint(1);
    check('当前位置: point3D_1 = 自己位置(一位小数)', converterOf('db.png').point3D_1, { x: 175587.4, y: -240315.1 });

    // ---- 选中实体：点地图上的实体标记是「选中」，不是取点 ----
    await sandbox.updateGameData(true);
    const entityEl = map.children.find(c => c.dataset && c.dataset.entityId === 'record:42');
    const itemEl = map.children.find(c => c.dataset && c.dataset.entityId === 'record:77');
    check('实体标记: 带 data-entity-id', !!entityEl, true);
    check('物资标记: 带 data-entity-id', !!itemEl, true);
    check('选中提示: 初始为空', byId['cal-entity'].textContent.includes('无 —'), true);

    const point1Before = converterOf('db.png').point2D_1;
    fire('mousedown', { clientX: 500, clientY: 500 });
    fire('click', { clientX: 500, clientY: 500, target: entityEl.children[0] });
    check('点实体: 不取点', converterOf('db.png').point2D_1, point1Before);
    check('点实体: 选中提示带坐标', byId['cal-entity'].textContent, '队友 (175587, -240315)');
    check('点实体: 标记高亮', entityEl._class.has('calibrate-entity'), true);

    fire('click', { clientX: 500, clientY: 500, target: itemEl.children[0] });
    check('点物资: 选中提示', byId['cal-entity'].textContent, '蓝物资 (3000, 4000)');

    fire('click', { clientX: 500, clientY: 500, target: entityEl.children[0] });
    sandbox.useSelectedEntityForPoint(1);
    check('用选中实体: point3D_1 = 实体坐标(一位小数)', converterOf('db.png').point3D_1, { x: 175587.4, y: -240315.1 });

    // 实体从数据里消失：保留选中，但按钮不再生效
    const keepEntities = payload.entities;
    payload.entities = [];
    await sandbox.updateGameData(true);
    check('实体消失: 提示', byId['cal-entity'].textContent, '队友 — 已不在数据里');
    const point2WorldBefore = converterOf('db.png').point3D_2;
    sandbox.useSelectedEntityForPoint(2);
    check('实体消失: 按钮不生效', converterOf('db.png').point3D_2, point2WorldBefore);
    payload.entities = keepEntities;
    await sandbox.updateGameData(true);
    check('实体回来: 提示恢复', byId['cal-entity'].textContent, '队友 (175587, -240315)');

    const snippet = byId['cal-code'].value;
    check('代码块: 含地图键', snippet.includes('"db.png": {'), true);
    check('代码块: 含新取的点', snippet.includes('point2D_1: { x: 100, y: 100 }'), true);

    sandbox.setCalibrateMode(false);
    check('关闭: 标记清除', markers().length, 0);
    check('关闭: 面板收起', byId['calibrate-panel']._class.has('open'), false);
    check('关闭: 取点无效', (tap(210, 220), converterOf('db.png').point2D_1), { x: 100, y: 100 });

    // 还原：回到进入校准模式时的快照
    sandbox.setCalibrateMode(true);
    sandbox.revertCalibration();
    check('还原: 回到文件值', converterOf('db.png'), dbOriginal);
    sandbox.setCalibrateMode(false);

    // jy.png 在文件里没有校准条目，校准模式应能按需创建
    sandbox.selectMap('jy.png');
    await sandbox.updateGameData(true);
    sandbox.setCalibrateMode(true);
    evalIn('scale = 1; offsetX = 0; offsetY = 0;');
    tap(120, 240);
    check('无校准图: 自动建条目', converterOf('jy.png').point2D_1, { x: 120, y: 240 });
    check('无校准图: 代码块', byId['cal-code'].value.includes('"jy.png": {'), true);
    check('无校准图: 标记跟随', [markers()[0].style.left, markers()[0].style.top], ['120px', '240px']);

    console.log(failures === 0 ? '\nALL PASS' : `\n${failures} FAILURE(S)`);
    process.exit(failures === 0 ? 0 : 1);
})();
