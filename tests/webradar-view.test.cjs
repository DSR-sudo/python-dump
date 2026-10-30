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
        offsetX: 0, offsetY: 0, scale: 1, isFollowing: true,
        applyMapTransform: () => {},
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
