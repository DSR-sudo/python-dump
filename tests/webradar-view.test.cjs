const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const html = fs.readFileSync(`${__dirname}/../web/webpage.html`, 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);

function poseSample(t, x, y = 0, z = 0) {
    return { t, x, y, z, yaw: 0, pitch: 0 };
}

function poseFixture(samples) {
    const context = vm.createContext({
        samples, Math,
        clampNumber: (value, min, max) => Math.max(min, Math.min(max, value)),
    });
    vm.runInContext(script.slice(script.indexOf('    const EGO_INTERP_HISTORY_MS'),
        script.indexOf('    // rAF 渲染循环')), context);
    vm.runInContext("egoPoseHistory.set('test', { samples, lastRender: null, blend: null });", context);
    return time => context.egoInterpPose({ key: 'test' }, time);
}

test('a large pose correction progresses and converges instead of freezing', () => {
    const samples = [poseSample(1000, 0)];
    const render = poseFixture(samples);
    assert.equal(render(1000).x, 0);
    samples.push(poseSample(1001, 200));
    assert.equal(render(1001).x, 0);
    assert.equal(render(1017).x, 20);
    assert.equal(render(1081).x, 100);
    assert.equal(render(1161).x, 200);
    assert.equal(render(1321).x, 200);
    // A later correction must also be able to start and complete.
    samples.push(poseSample(1400, 400));
    assert.equal(render(1400).x, 200);
    samples.push(poseSample(1560, 400));
    assert.equal(render(1560).x, 400);
});

test('new samples during a correction update its target without restarting it', () => {
    const samples = [poseSample(1000, 0)];
    const render = poseFixture(samples);
    render(1000);
    samples.push(poseSample(1001, 200));
    render(1001);
    samples.push(poseSample(1081, 300));
    assert.equal(render(1081).x, 150);
    samples.push(poseSample(1161, 400));
    assert.equal(render(1161).x, 400);
});

test('extrapolation caps vector speed at 1500 cm/s and stops after 350ms', () => {
    for (const [x, y, z] of [[1000, 0, 0], [0, 0, -1000], [600, 800, 0]]) {
        for (const time of [200, 500, 1000]) {
            const render = poseFixture([poseSample(100, 0), poseSample(150, x, y, z)]);
            const pose = render(time);
            const distance = 1500 * Math.min(time - 150, 350) / 1000;
            assert.ok(Math.abs(pose.x - x - distance * x / 1000) < 1e-9);
            assert.ok(Math.abs(pose.y - y - distance * y / 1000) < 1e-9);
            assert.ok(Math.abs(pose.z - z - distance * z / 1000) < 1e-9);
        }
    }
});

test('extrapolation preserves motion below the speed limit and stationary poses', () => {
    const render = poseFixture([poseSample(100, 0), poseSample(150, 30, 40)]);
    const pose = render(500);
    assert.equal(pose.x, 240);
    assert.equal(pose.y, 320);
    const stationary = poseFixture([poseSample(100, 10), poseSample(150, 10)]);
    assert.equal(stationary(500).x, 10);
});

function fixture() {
    let timer, now = 1000;
    const selected = [], entered = [];
    const a = { id: 'record:1', key: 'mesh:a' };
    const b = { id: 'record:2', key: 'mesh:b' };
    const context = vm.createContext({
        Date: { now: () => now }, Math,
        clearTimeout: () => { timer = null; },
        setTimeout: fn => { timer = fn; return 1; },
        egoMode: false, calibrateMode: false, pressX: 20, pressY: 20,
        entitiesToRender: [a, b],
        selectPlayer: id => selected.push(id),
        enterEgoMode: () => { entered.push(selected.at(-1)); context.egoMode = true; },
    });
    vm.runInContext(script.match(/    function egoPoseKeyOf[^\n]+/)[0], context);
    vm.runInContext(script.slice(script.indexOf('    const ENTITY_DOUBLE_CLICK_MS'),
        script.indexOf('    // 捕获在不会被快照替换')), context);
    return { context, a, b, selected, entered,
        click: (entity, x = 20, y = 20) => context.handleEntityClick(entity, x, y),
        tick: () => { now += 100; },
        flush: () => { if (timer) timer(); },
    };
}

test('two clicks enter the same actor after snapshot replaces its record ID', () => {
    const f = fixture();
    f.click(f.a);
    assert.deepEqual(f.selected, []); // first click must not move the map
    f.tick();
    const updated = { ...f.a, id: 'record:99' };
    f.context.entitiesToRender = [updated];
    f.click(updated);
    f.flush();
    assert.deepEqual(f.selected, ['record:99']);
    assert.deepEqual(f.entered, ['record:99']);
});

test('single click selects latest record after the double-click window', () => {
    const f = fixture();
    f.click(f.a);
    f.context.entitiesToRender = [{ ...f.a, id: 'record:99' }];
    f.flush();
    assert.deepEqual(f.selected, ['record:99']);
    assert.deepEqual(f.entered, []);
});

test('different actors, blank space, drags and missing targets do not enter view', () => {
    for (const scenario of ['different', 'blank', 'drag', 'missing', 'calibration']) {
        const f = fixture();
        f.click(f.a);
        f.tick();
        if (scenario === 'different') f.click(f.b);
        if (scenario === 'blank') f.click(null);
        if (scenario === 'drag') f.click(f.a, 50, 20);
        if (scenario === 'missing') f.context.entitiesToRender = [];
        if (scenario === 'calibration') f.context.calibrateMode = true;
        f.flush();
        assert.deepEqual(f.entered, [], scenario);
        assert.deepEqual(f.selected, scenario === 'different' ? ['record:2'] : [], scenario);
    }
});

test('entity hit lookup accepts numeric IDs from DOM datasets', () => {
    const f = fixture();
    f.context.mapContainer = {};
    f.context.entitiesToRender = [{ id: 42 }];
    vm.runInContext(script.slice(script.indexOf('    function entityFromTarget'),
        script.indexOf('    function resolveEgoTarget')), f.context);
    assert.equal(f.context.entityFromTarget({
        classList: { contains: () => true }, dataset: { entityId: '42' },
    }).id, 42);
});

test('full-screen view remains, with no bottom view toggle', () => {
    assert.doesNotMatch(html, /ego-toggle/);
    assert.match(html, /#ego-view \{ position: fixed; inset: 0;/);
    assert.match(html, /#ego-view\.open \{ display: block;/);
});

function pointerFixture() {
    const f = fixture(), handlers = {}, captures = new Set();
    Object.assign(f.context, {
        mapContainer: {
            addEventListener: (name, fn) => { handlers[name] = fn; },
            setPointerCapture: id => captures.add(id),
            hasPointerCapture: id => captures.has(id),
            releasePointerCapture: id => captures.delete(id),
            getBoundingClientRect: () => ({ left: 0, top: 0 }),
        },
        window: { addEventListener: (name, fn) => { handlers[name] = fn; } },
        entityFromTarget: target => target,
        calibrationEntityFromTarget: target => target,
        selectCalibrationEntity: () => false,
        pickCalibrationPoint: () => {},
        offsetX: 0, offsetY: 0, scale: 1, isFollowing: true, viewFollowRotation: false, radarAuthed: true,
        applyMapTransform: () => {}, setMapRotationTarget: () => {},
    });
    vm.runInContext(script.slice(script.indexOf('    const mapPointers'),
        script.indexOf('    function initMapSelect')), f.context);
    f.event = (type, overrides = {}) => handlers[type]({
        pointerId: 1, button: 0, clientX: 20, clientY: 20,
        target: f.a, preventDefault() {}, ...overrides,
    });
    return f;
}

test('pointer capture preserves clicks when actor DOM and record are replaced mid-press', () => {
    const f = pointerFixture();
    for (const id of ['record:10', 'record:11']) {
        f.event('pointerdown', { target: f.context.entitiesToRender[0] });
        f.context.entitiesToRender = [{ ...f.a, id }];
        f.event('pointerup', { target: null });
        f.tick();
    }
    assert.deepEqual(f.entered, ['record:11']);
});

test('drag out and back, pinch, cancellation, blur and wheel cancel pending selection', () => {
    for (const type of ['drag', 'pinch', 'pointercancel', 'lostpointercapture', 'blur', 'wheel']) {
        const f = pointerFixture();
        f.event('pointerdown'); f.event('pointerup');
        f.event('pointerdown');
        if (type === 'drag') {
            f.event('pointermove', { clientX: 60 });
            f.event('pointermove');
        } else if (type === 'pinch') {
            f.event('pointerdown', { pointerId: 2, clientX: 60 });
            f.event('pointerup', { pointerId: 2, clientX: 60 });
        } else if (type === 'wheel') f.context.cancelMapGesture();
        else f.event(type);
        f.flush();
        f.event('pointerup');
        f.flush();
        assert.deepEqual(f.selected, [], type);
        assert.deepEqual(f.entered, [], type);
    }
});

test('second press pauses delayed recenter until release; right click is ignored', () => {
    const f = pointerFixture();
    f.event('pointerdown', { button: 2 }); f.event('pointerup', { button: 2 });
    f.flush(); assert.deepEqual(f.selected, []);
    f.event('pointerdown'); f.event('pointerup');
    f.tick(); f.event('pointerdown'); f.flush();
    assert.deepEqual(f.selected, []);
    f.event('pointerup');
    assert.deepEqual(f.entered, ['record:1']);
});

test('pinch keeps its midpoint stable and cancels selection', () => {
    const f = pointerFixture();
    f.event('pointerdown');
    f.event('pointerdown', { pointerId: 2, clientX: 60 });
    f.event('pointermove', { pointerId: 2, clientX: 100 });
    assert.equal(f.context.scale, 2);
    assert.equal(f.context.offsetX, -20);
    assert.equal(f.context.offsetY, -20);
    f.event('pointerup', { pointerId: 2, clientX: 100 });
    f.event('pointerup'); f.flush();
    assert.deepEqual(f.selected, []);
});

test('geometric fallback picks the nearest marker inside the tap radius', () => {
    const marker = (entity, x, y) => ({ entity, container: { style: { left: `${x}px`, top: `${y}px` } } });
    const context = vm.createContext({
        Math, offsetX: 0, offsetY: 0, scale: 1, mapRotationDeg: 0,
        mapContainer: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 800 }) },
        radarMarkers: [marker({ id: 'a' }, 100, 100), marker({ id: 'b' }, 300, 100)],
    });
    vm.runInContext(script.slice(script.indexOf('    function nearestEntityAt'),
        script.indexOf('    function resolveEgoTarget')), context);
    const nearest = vm.runInContext('nearestEntityAt', context);
    assert.equal(nearest(120, 100, 32), context.radarMarkers[0].entity); // 20px 偏差仍在触摸半径内
    assert.equal(nearest(280, 100, 32), context.radarMarkers[1].entity); // 取更近的标记
    assert.equal(nearest(150, 100, 32), null);                           // 半径外算点空地
    assert.equal(nearest(160, 100, 64), context.radarMarkers[0].entity); // 半径放宽后可命中
});

test('geometric fallback stays accurate while the map is rotated', () => {
    const marker = (entity, x, y) => ({ entity, container: { style: { left: `${x}px`, top: `${y}px` } } });
    const context = vm.createContext({
        Math, offsetX: 400, offsetY: 300, scale: 1, mapRotationDeg: -90,
        mapContainer: { getBoundingClientRect: () => ({ left: 10, top: 20, width: 1000, height: 800 }) },
        radarMarkers: [marker({ id: 'a' }, 100, 100), marker({ id: 'b' }, 600, 100)],
    });
    vm.runInContext(script.slice(script.indexOf('    function nearestEntityAt'),
        script.indexOf('    function resolveEgoTarget')), context);
    const nearest = vm.runInContext('nearestEntityAt', context);
    // 视图中心 (500,400)：a 转后在中心不动，b 转到 (500,-100)，屏幕坐标再叠加 rect 偏移。
    assert.equal(nearest(510, 415, 32), context.radarMarkers[0].entity);
    assert.equal(nearest(510, -80, 32), context.radarMarkers[1].entity);
    assert.equal(nearest(610, 320, 32), null);
});

test('a touch that misses the marker DOM still selects via the geometric fallback', () => {
    const f = pointerFixture();
    f.context.entityFromTarget = () => null; // 手指点在地图背景上，没碰到 8px 标记
    f.context.nearestEntityAt = () => f.a;   // 但落在标记的触摸半径内
    f.event('pointerdown', { pointerType: 'touch' });
    f.event('pointerup', { pointerType: 'touch' });
    f.tick(); f.flush();
    assert.deepEqual(f.selected, ['record:1']); // 双击窗口后照常选中，进入跟随
});

test('following keeps the selected actor centered and the map north-up', () => {
    const poses = { p1: { x: 100, y: 200 } };
    const context = vm.createContext({
        Math,
        selectedPlayerId: 'p1', selectedEntityKey: 'mesh:p1',
        entitiesToRender: [{ id: 'p1', key: 'mesh:p1' }],
        teammates: [], localPlayer: null,
        getRadarTransformedData: entity => ({ position: poses[entity.id] }),
        mapContainer: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 800 }) },
        map: { style: {} }, offsetX: 0, offsetY: 0, scale: 2,
        mapRotationDeg: 0, mapRotationTarget: 0, mapRotationAt: 0,
        viewFollowRotation: false, calibrateMode: false,
    });
    vm.runInContext(script.slice(script.indexOf('    function applyMapTransform'),
        script.indexOf("    window.addEventListener('resize'")), context);
    vm.runInContext(script.slice(script.indexOf('    function recenterView'),
        script.indexOf('    function loadSelection')), context);
    vm.runInContext('recenterView(1000);', context);
    assert.equal(context.map.style.transform, 'translate(300px, 0px) scale(2)');
    poses.p1 = { x: 160, y: 260 }; // 角色移动后镜头跟随；未开视角跟随时 transform 只平移缩放、无 rotate
    vm.runInContext('recenterView(1050);', context);
    assert.equal(context.map.style.transform, 'translate(180px, -120px) scale(2)');
    assert.ok(!context.map.style.transform.includes('rotate'));
});

test('view-follow rotates the map with the actor so their stick stays screen-up', () => {
    const pose = { x: 100, y: 200, yaw: 0 };
    const direction = { style: {} }, info = { style: {} };
    const actor = { id: 'p1', key: 'mesh:p1' };
    const context = vm.createContext({
        Math, egoMode: false, isFollowing: true, viewFollowRotation: true, calibrateMode: false,
        radarMarkers: [{ entity: actor, container: { style: {} }, direction, info }],
        getRadarTransformedData: () => ({ position: { x: pose.x, y: pose.y }, rotation: pose.yaw }),
        requestAnimationFrame: () => 0,
        selectedPlayerId: 'p1', selectedEntityKey: 'mesh:p1',
        entitiesToRender: [actor], teammates: [], localPlayer: null,
        mapContainer: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 800 }) },
        map: { style: {} }, offsetX: 0, offsetY: 0, scale: 2,
        mapRotationDeg: 0, mapRotationTarget: 0, mapRotationAt: 0,
    });
    vm.runInContext(script.slice(script.indexOf('    function applyMapTransform'),
        script.indexOf("    window.addEventListener('resize'")), context);
    vm.runInContext(script.slice(script.indexOf('    function recenterView'),
        script.indexOf('    function loadSelection')), context);
    vm.runInContext(script.slice(script.indexOf('    function renderRadarMotion'),
        script.indexOf('    function getTeamColor')), context);
    vm.runInContext('renderRadarMotion(1000);', context);
    assert.equal(context.mapRotationTarget, 0);
    pose.yaw = 90; // 角色向右转 90°：地图目标角变为 −90°，让选中角色的方向杆恒指屏幕正上方
    for (let t = 1016; t < 2000; t += 16) {
        vm.runInContext(`renderRadarMotion(${t});`, context);
        if (context.mapRotationDeg === context.mapRotationTarget) break;
    }
    assert.equal(context.mapRotationTarget, -90);
    assert.equal(context.mapRotationDeg, -90);
    assert.ok(context.map.style.transform.includes('rotate(-90deg)'));
    // 方向杆保持绝对朝向 90°，叠加地图 −90° 后屏幕角度为 0（正上方）；名字标签反向补偿保持水平。
    assert.equal(direction.style.transform, 'translateX(-50%) rotate(90deg)');
    assert.equal(info.style.transform, 'translateX(-50%) rotate(90deg)');
    // 绕视图中心旋转：角色地图坐标 (100,200)、offset (300,0)、scale 2 → 旋转后仍落在中心 (500,400)。
    const m = context.map.style.transform.match(/translate\(([-\d.e+]+)px, ([-\d.e+]+)px\)/);
    const px = Number(m[1]) + (100 * 2) * Math.cos(-Math.PI / 2) - (200 * 2) * Math.sin(-Math.PI / 2);
    const py = Number(m[2]) + (100 * 2) * Math.sin(-Math.PI / 2) + (200 * 2) * Math.cos(-Math.PI / 2);
    assert.ok(Math.abs(px - 500) < 1e-6 && Math.abs(py - 400) < 1e-6);
});

test('wheel zoom and pinch exit view-follow rotation but keep the centered follow', () => {
    const handlers = {};
    const context = vm.createContext({
        Math, cancelMapGesture: () => {},
        mapContainer: {
            addEventListener: (name, fn) => { handlers[name] = fn; },
            getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 800 }),
        },
        map: { style: {} }, offsetX: 50, offsetY: 60, scale: 1,
        mapRotationDeg: -30, mapRotationTarget: -30, mapRotationAt: 0,
        isFollowing: true, viewFollowRotation: true, radarAuthed: true,
    });
    vm.runInContext(script.slice(script.indexOf('    function applyMapTransform'),
        script.indexOf("    window.addEventListener('resize'")), context);
    vm.runInContext(script.slice(script.indexOf("    mapContainer.addEventListener('wheel'"),
        script.indexOf('    var firstData')), context);
    handlers.wheel({ deltaY: -100, preventDefault() {} });
    assert.ok(Math.abs(context.scale - 1.1) < 1e-9);
    assert.equal(context.viewFollowRotation, false); // 缩放退出旋转，地图回正北
    assert.equal(context.mapRotationTarget, 0);      // 目标角显式归零（不依赖 recenterView）
    assert.equal(context.isFollowing, true);         // 但仍居中跟随选中角色
});

test('pan drag stops following; taps and pinches do not stop the centered follow', () => {
    const tap = pointerFixture();
    tap.context.viewFollowRotation = true;
    tap.event('pointerdown'); tap.event('pointerup');
    assert.equal(tap.context.isFollowing, true);   // 单击不再顺带关闭跟随
    assert.equal(tap.context.viewFollowRotation, true);

    const pan = pointerFixture();
    pan.context.viewFollowRotation = true;
    pan.event('pointerdown');
    pan.event('pointermove', { clientX: 60 });
    assert.equal(pan.context.isFollowing, false);  // 单指拖动 = 手动平移
    assert.equal(pan.context.viewFollowRotation, false);

    const pinch = pointerFixture();
    pinch.context.viewFollowRotation = true;
    pinch.event('pointerdown');
    pinch.event('pointerdown', { pointerId: 2, clientX: 60 });
    assert.equal(pinch.context.viewFollowRotation, false); // 双指缩放退出旋转
    assert.equal(pinch.context.isFollowing, true);         // 但保持居中跟随
});
