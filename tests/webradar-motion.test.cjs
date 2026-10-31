const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const html = fs.readFileSync(`${__dirname}/../web/webpage.html`, 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function fixture() {
    const context = vm.createContext({
        entitiesToRender: [], currentMap: 'db.png',
        getTransformedData: entity => ({ position: entity.position, rotation: entity.orientation }),
    });
    vm.runInContext(script.match(/    function egoPoseKeyOf[^\n]+/)[0], context);
    vm.runInContext(script.slice(script.indexOf('    function egoAngleLerp('),
        script.indexOf('    // updateGameData 每收到')), context);
    vm.runInContext(script.slice(script.indexOf('    const radarMotion ='),
        script.indexOf('    function getTeamColor(')), context);
    const entity = { key: 'mesh:1', id: 'record:1', position: { x: 0, y: 0, z: 0 }, orientation: 0 };
    return {
        context, entity,
        feed(time, x, y = 0) {
            entity.position = { x, y, z: 0 };
            context.entitiesToRender = [entity];
            context.pushRadarSamples(time);
        },
        pose: time => context.getRadarTransformedData(entity, time),
    };
}

test('radar moves between snapshots, retargets continuously, and stops at the latest position', () => {
    const f = fixture();
    f.feed(0, 0);
    f.feed(200, 200);
    assert.equal(f.pose(200).position.x, 0);
    assert.equal(f.pose(216).position.x, 100);
    f.feed(216, 300);
    assert.equal(f.pose(216).position.x, 100);
    assert.equal(f.pose(224).position.x, 200);
    assert.equal(f.pose(232).position.x, 300);
    assert.equal(f.pose(5000).position.x, 300);
});

test('an isolated jump and non-finite position never reach the renderer', () => {
    const f = fixture();
    f.feed(0, 0);
    f.feed(200, 200);
    f.feed(300, 100000);
    assert.equal(f.pose(400).position.x, 200);
    f.feed(400, 300);
    f.feed(500, NaN);
    assert.equal(f.pose(600).position.x, 300);
    f.feed(700, Infinity);
    assert.equal(f.pose(800).position.x, 300);
});

test('three consistent relocation samples recover smoothly instead of freezing forever', () => {
    const f = fixture();
    f.feed(0, 0);
    f.feed(100, 100000);
    f.feed(200, 100050);
    assert.equal(f.pose(200).position.x, 0);
    f.feed(300, 100100);
    assert.equal(f.pose(300).position.x, 0);
    assert.ok(f.pose(316).position.x > 0 && f.pose(316).position.x < 100100);
    assert.equal(f.pose(332).position.x, 100100);
});

test('alternating outliers cannot confirm each other; fast duplicate responses cannot confirm a jump', () => {
    const f = fixture();
    f.feed(0, 0);
    for (let time = 100; time <= 1000; time += 100) f.feed(time, time % 200 ? 100000 : -100000);
    assert.equal(f.pose(1100).position.x, 0);
    f.feed(1100, 50000);
    f.feed(1101, 50000);
    f.feed(1102, 50000);
    assert.equal(f.pose(1200).position.x, 0);
});

test('stable mesh identity survives record ID replacement, old and missing identities reset', () => {
    const f = fixture();
    f.feed(0, 0);
    f.entity.id = 'record:2';
    f.feed(200, 200);
    assert.equal(f.pose(216).position.x, 100);
    f.feed(3000, 50000);
    assert.equal(f.pose(3000).position.x, 50000);
    f.context.entitiesToRender = [];
    f.context.pushRadarSamples(6100);
    assert.equal(f.pose(6100), null);
    f.feed(6200, 60000);
    assert.equal(f.pose(6200).position.x, 60000);
});

test('direction follows the short arc and repeated snapshots do not restart a transition', () => {
    const f = fixture();
    f.entity.orientation = 359;
    f.feed(0, 0);
    f.entity.orientation = 1;
    f.feed(200, 200);
    assert.equal(f.pose(216).rotation, 360);
    f.feed(216, 200);
    assert.equal(f.pose(232).position.x, 200);
    assert.equal(f.pose(232).rotation, 361);
});

test('following camera consumes the same smooth position as the actor marker', () => {
    const f = fixture();
    f.feed(0, 0);
    f.feed(200, 200);
    Object.assign(f.context, { selectedPlayerId: f.entity.id, selectedEntityKey: f.entity.key,
        localPlayer: f.entity, teammates: [], scale: 2, offsetX: 0, offsetY: 0,
        mapContainer: { getBoundingClientRect: () => ({ width: 800, height: 600 }) },
        applyMapTransform() {},
    });
    vm.runInContext(script.slice(script.indexOf('    function recenterView('),
        script.indexOf('    function loadSelection(')), f.context);
    f.context.recenterView(216);
    assert.equal(f.context.offsetX + f.pose(216).position.x * 2, 400);
});

test('slider idles after 8 seconds, wakes, and recovers from lost pointer capture', () => {
    const events = {}, windowEvents = {};
    let timer, idle = false;
    const context = vm.createContext({
        document: { getElementById: () => ({
            classList: { add: () => { idle = true; }, remove: () => { idle = false; } },
            addEventListener: (type, fn) => { events[type] = fn; },
        }) },
        window: { addEventListener: (type, fn) => { windowEvents[type] = fn; } },
        clearTimeout: () => { timer = null; },
        setTimeout: (fn, delay) => { assert.equal(delay, 8000); timer = fn; return 1; },
    });
    vm.runInContext(script.slice(script.indexOf('    function initMarkerSizeIdle('),
        script.indexOf('    function setMapFullscreen(')), context);
    context.initMarkerSizeIdle();
    timer(); assert.equal(idle, true);
    events.pointerenter(); assert.equal(idle, false);
    events.pointerdown(); assert.equal(timer, null);
    events.input(); assert.equal(timer, null);
    events.lostpointercapture(); assert.equal(typeof timer, 'function');
    timer(); assert.equal(idle, true);
    events.keydown(); assert.equal(idle, false);
    events.pointerdown(); events.pointermove({ buttons: 0 });
    assert.equal(typeof timer, 'function');
    events.pointerdown(); windowEvents.pointerup();
    assert.equal(typeof timer, 'function');
});


test('normal radar updates settle within 32ms and under 50ms including a 60Hz frame wait', () => {
    const frameMs = 1000 / 60;
    for (const interval of [8, 16, 32, 100, 250, 1000]) {
        for (const phase of [0, 4, 8, 12, 16]) {
            const f = fixture();
            f.feed(0, 0);
            f.feed(interval, 100);
            assert.equal(f.pose(interval + 32).position.x, 100);
            let elapsed = phase;
            while (f.pose(interval + elapsed).position.x !== 100) elapsed += frameMs;
            assert.ok(elapsed < 50, `interval=${interval}, phase=${phase}, latency=${elapsed}`);
        }
    }
});
