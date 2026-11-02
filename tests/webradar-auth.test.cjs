const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const html = fs.readFileSync(`${__dirname}/../web/webpage.html`, 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function stubElement(value = '') {
    const classes = new Set();
    return {
        value,
        style: {},
        classList: {
            add: name => classes.add(name),
            remove: name => classes.delete(name),
            contains: name => classes.has(name),
        },
    };
}

function stubStorage() {
    const store = {};
    return {
        getItem: key => (key in store ? store[key] : null),
        setItem: (key, value) => { store[key] = String(value); },
        removeItem: key => { delete store[key]; },
        _store: store,
    };
}

// 组装一个可运行密码门相关切片的上下文：默认锁定，密码输入框值为 password。
function gateFixture({ password = '', fetchResponse = null } = {}) {
    const fetchCalls = [];
    const timers = [];
    const spies = { selectMap: [], updateGameData: [], pushSubscription: [] };
    const elements = {
        'auth-input': stubElement(password),
        'auth-gate': stubElement(),
        'auth-error': stubElement(),
    };
    const context = vm.createContext({
        Math, JSON, Promise, Date, console,
        document: { getElementById: id => elements[id] },
        localStorage: stubStorage(),
        fetch: async (url, options) => { fetchCalls.push({ url, options }); return fetchResponse; },
        setTimeout: fn => { timers.push(fn); return timers.length; },
        clearTimeout: () => {},
        clientId: 'test-client',
        DATA_POLL_INTERVAL_MS: 16, DATA_REFRESH_FALLBACK_MS: 30000,
        currentMap: 'db.png',
        radarAuthed: false, pollLoopRunning: false,
        dataToken: null, dataChangedAt: 0, dataRequestSerial: 0, dataAppliedSerial: 0,
        // 解锁后 selectMap 走完整路径所需的本地桩
        cancelMapGesture: () => {},
        mapDimensions: { 'db.png': { width: 1000, height: 1000 } },
        map: { style: {}, innerHTML: '' },
        syncSelectionSummary: () => {}, checkCalibration: () => {}, saveSelection: () => {},
        transformerCache: {}, calibrateMode: false,
        radarMotion: new Map(), radarMarkers: [],
        // 订阅路径的本地桩
        currentSubscription: () => ({}), lastSubscriptionSig: '',
        subscriptionTimer: null, subscriptionInFlight: false,
    });
    Object.defineProperty(context, 'spies', { value: spies });
    // 门控函数（authPassword/mapImageUrl/lockRadar/unlockRadar/tryAuth/loadAuth）
    vm.runInContext(script.slice(script.indexOf('    function authPassword('),
        script.indexOf('    loadSelection();')), context);
    // 轮询调度（解锁后才启动）
    vm.runInContext(script.slice(script.indexOf('    function scheduleGameDataPoll'),
        script.indexOf('    // 用上次保存的密码自动验证')), context);
    // 真实的 updateGameData / selectMap / pushSubscription（用于验证锁定态为空操作）
    vm.runInContext(script.slice(script.indexOf('    async function updateGameData'),
        script.indexOf('    function renderRadarItemMarker')), context);
    vm.runInContext(script.slice(script.indexOf('    function selectMap('),
        script.indexOf('    function selectPlayer(')), context);
    vm.runInContext(script.slice(script.indexOf('    function pushSubscription('),
        script.indexOf('    function entityFromTarget(')), context);
    return { context, elements, fetchCalls, timers, spies };
}

test('locked page sends no requests: updateGameData, pushSubscription and selectMap are no-ops', async () => {
    const f = gateFixture({ password: '123456' });
    await vm.runInContext('updateGameData(true)', f.context);
    vm.runInContext('pushSubscription(true)', f.context);
    vm.runInContext("selectMap('ht.png')", f.context);
    assert.equal(f.fetchCalls.length, 0);                       // 不发任何请求
    assert.equal(f.elements['auth-gate'].classList.contains('hidden'), false); // 密码门保持可见
    assert.equal(f.context.currentMap, 'db.png');               // 地图未切换
    assert.notEqual(f.context.map.style.backgroundImage, "url('/image/db.png?token=123456')"); // 未加载地图
});

test('empty password keeps the gate up without any request', async () => {
    const f = gateFixture({ password: '' });
    await vm.runInContext('tryAuth()', f.context);
    assert.equal(f.fetchCalls.length, 0);
    assert.equal(f.context.radarAuthed, false);
});

test('wrong password stays locked, shows the error and forgets the saved token', async () => {
    const f = gateFixture({ password: '000000', fetchResponse: { ok: false, status: 401 } });
    f.context.localStorage.setItem('authToken', '000000');
    await vm.runInContext('tryAuth()', f.context);
    assert.equal(f.context.radarAuthed, false);
    assert.equal(f.elements['auth-error'].style.display, 'block');
    assert.equal(f.context.localStorage.getItem('authToken'), null);
    assert.equal(f.fetchCalls.length, 1); // 只有验证这一次请求
    assert.equal(f.timers.length, 0);     // 轮询循环没有启动
});

test('correct password unlocks: gate hides, map loads with token, poll loop starts', async () => {
    const f = gateFixture({ password: '123789', fetchResponse: { ok: true, status: 200 } });
    await vm.runInContext('tryAuth()', f.context);
    assert.equal(f.context.radarAuthed, true);
    assert.equal(f.elements['auth-gate'].classList.contains('hidden'), true);
    assert.equal(f.elements['auth-error'].style.display, 'none');
    assert.equal(f.context.localStorage.getItem('authToken'), '123789');
    // 解锁后才加载地图文件，且 URL 带鉴权 token
    assert.equal(f.context.map.style.backgroundImage, "url('/image/db.png?token=123789')");
    assert.equal(f.context.mapImageUrl('ht.png'), '/image/ht.png?token=123789');
    assert.equal(f.context.pollLoopRunning, true); // 60Hz 轮询循环已启动
    assert.equal(f.timers.length, 1);
    // 验证请求带了密码头
    assert.equal(f.fetchCalls[0].options.headers['X-Auth-Token'], '123789');
});

test('a 401 during polling re-locks the radar: map dropped, loop stops itself', async () => {
    const f = gateFixture({ password: '123789', fetchResponse: { ok: false, status: 401 } });
    f.context.radarAuthed = true; // 会话中途服务端重启换了密码
    f.context.pollLoopRunning = true;
    f.context.map.style.backgroundImage = "url('/image/db.png?token=123789')";
    await vm.runInContext('updateGameData()', f.context);
    assert.equal(f.context.radarAuthed, false);
    assert.equal(f.context.map.style.backgroundImage, '');                       // 不再持有地图
    assert.equal(f.elements['auth-gate'].classList.contains('hidden'), false);   // 回到密码门
    assert.equal(f.elements['auth-error'].style.display, 'block');
    vm.runInContext('scheduleGameDataPoll()', f.context); // 循环下一跳发现已锁定，自停
    assert.equal(f.context.pollLoopRunning, false);
    assert.equal(f.timers.length, 0);
});

test('scheduleGameDataPoll idles while locked and re-arms each round while unlocked', async () => {
    const f = gateFixture({ password: '123789', fetchResponse: { ok: true, status: 204 } });
    vm.runInContext('scheduleGameDataPoll()', f.context);
    assert.equal(f.timers.length, 0); // 锁定时不起定时器
    f.context.radarAuthed = true;
    let calls = 0;
    f.context.updateGameData = async () => { calls += 1; };
    vm.runInContext('scheduleGameDataPoll()', f.context);
    assert.equal(f.timers.length, 1);
    await f.timers.shift()();          // 一轮结束（含等待响应）后再排下一轮
    assert.equal(calls, 1);
    assert.equal(f.timers.length, 1);  // 解锁期间持续轮询
    f.context.radarAuthed = false;     // 中途锁定：下一跳不再续排
    await f.timers.shift()();
    assert.equal(f.timers.length, 0);
});
