const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('vm');
const { test } = require('node:test');
const html = fs.readFileSync(`${__dirname}/../web/webpage.html`, 'utf8');

// aim 面板是页面末尾独立的第二个 <script> 块
const blocks = html.match(/<script>([\s\S]*?)<\/script>/g).map(b => b.slice(8, -9));
const aimScript = blocks.find(b => b.includes('aim-panel-head'));
assert.ok(aimScript, '找不到 aim 面板脚本块');

function el(stub = {}) {
    const classes = new Set();
    return Object.assign({
        style: {}, textContent: '', onclick: null, onchange: null,
        addEventListener: () => {}, removeEventListener: () => {},
        setPointerCapture: () => {}, releasePointerCapture: () => {},
        getBoundingClientRect: () => ({ left: 0, top: 0 }),
        classList: { add: n => classes.add(n), remove: n => classes.delete(n), contains: n => classes.has(n) },
    }, stub);
}

function aimFixture() {
    const handlers = {}; // head 上按类型收集的监听器
    const captured = [];
    const elements = {
        'aim-panel': el({ getBoundingClientRect: () => ({ left: 100, top: 200 }) }),
        'aim-out': el(),
        'aim-panel-head': el({
            addEventListener: (t, fn) => { (handlers[t] = handlers[t] || []).push(fn); },
            removeEventListener: (t, fn) => { if (handlers[t]) handlers[t] = handlers[t].filter(f => f !== fn); },
            setPointerCapture: id => captured.push(id),
        }),
        'aim-panel-toggle': el(),
        'aim-panel-hide': el(),
        'aim-on': el(), 'aim-off': el(), 'aim-status': el(),
        'aim-fov': el({ value: '' }), 'aim-fov-set': el(),
        'aim-part': el(), 'aim-sup': el(),
    };
    const context = vm.createContext({
        Math, JSON, Promise, Date, console, encodeURIComponent,
        document: { getElementById: id => elements[id] },
        fetch: async () => {},
        setInterval: () => 1, clearInterval: () => {},
        window: { innerWidth: 1920, innerHeight: 1080 },
        radarAuthed: false, authPassword: () => '963007',
    });
    vm.runInContext(aimScript, context);
    return { context, elements, handlers, captured };
}

test('aim panel: hide button click is not swallowed by the drag pointer capture', () => {
    const { elements, handlers, captured } = aimFixture();
    assert.equal(typeof elements['aim-panel-hide'].onclick, 'function'); // 绑定存在
    // 指针落在隐藏按钮上：不捕获、不进入拖拽（否则合成 click 被重定向，按钮永远点不到）
    const onBtn = { pointerId: 7, button: 0, pointerType: 'mouse', clientX: 300, clientY: 220,
                    target: { closest: sel => sel === '#aim-panel-hide' } };
    handlers.pointerdown[0](onBtn);
    assert.equal(captured.length, 0);
    assert.equal((handlers.pointermove || []).length, 0);
});

test('aim panel: dragging the head moves the panel and clamps to the viewport', () => {
    const { elements, handlers, captured } = aimFixture();
    handlers.pointerdown[0]({ pointerId: 3, button: 0, pointerType: 'mouse', clientX: 120, clientY: 230, target: { closest: () => null } });
    assert.equal(captured[0], 3); // 正常拖拽仍捕获指针
    handlers.pointermove[0]({ pointerId: 3, clientX: -500, clientY: 99999 }); // 越界，应被钳制
    assert.equal(elements['aim-panel'].style.left, '0px');
    assert.equal(elements['aim-panel'].style.top, '1050px'); // innerHeight 1080 - 30
    assert.equal(elements['aim-panel'].style.right, 'auto');
    assert.equal(elements['aim-panel'].style.bottom, 'auto');
});

test('aim panel: hide collapses to the AIM chip and the chip re-expands the panel', () => {
    const { elements } = aimFixture();
    const panel = elements['aim-panel'], chip = elements['aim-panel-toggle'];
    panel.style.display = 'block';
    elements['aim-panel-hide'].onclick();
    assert.equal(panel.style.display, 'none');
    assert.equal(chip.style.display, 'block');
    chip.onclick();
    assert.equal(panel.style.display, 'block');
    assert.equal(chip.style.display, 'none');
});
