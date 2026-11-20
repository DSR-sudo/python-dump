const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('vm');
const { test } = require('node:test');
const html = fs.readFileSync(`${__dirname}/../web/webpage.html`, 'utf8');

const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const panelScript = script.slice(script.indexOf('    const setNorecoilAvailable ='),
    script.indexOf('    loadSelection();'));

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

function panelFixture() {
    const handlers = {}; // head 上按类型收集的监听器
    const captured = [];
    const fetches = [];
    const elements = {
        'norecoil-panel': el({ getBoundingClientRect: () => ({ left: 100, top: 200 }) }),
        'norecoil-panel-head': el({
            addEventListener: (t, fn) => { (handlers[t] = handlers[t] || []).push(fn); },
            removeEventListener: (t, fn) => { if (handlers[t]) handlers[t] = handlers[t].filter(f => f !== fn); },
            setPointerCapture: id => captured.push(id),
        }),
        'norecoil-panel-toggle': el(),
        'norecoil-panel-hide': el(),
        'norecoil-on': el(), 'norecoil-off': el(), 'norecoil-status': el(),
        'norecoil-out': el(),
    };
    const context = vm.createContext({
        Math, JSON, Promise, Date, console, encodeURIComponent,
        document: { getElementById: id => elements[id] },
        fetch: async (url, opts) => {
            fetches.push({ url: String(url), opts });
            return { status: 200, json: async () => ({ reply: 'ok' }) };
        },
        window: { innerWidth: 1920, innerHeight: 1080 },
        radarAuthed: false, authPassword: () => '963007',
    });
    vm.runInContext(panelScript, context);
    return { context, elements, handlers, captured, fetches };
}

test('NoRecoil panel: hide button click is not swallowed by the drag pointer capture', () => {
    const { elements, handlers, captured } = panelFixture();
    assert.equal(typeof elements['norecoil-panel-hide'].onclick, 'function'); // 绑定存在
    // 指针落在隐藏按钮上：不捕获、不进入拖拽（否则合成 click 被重定向，按钮永远点不到）
    const onBtn = { pointerId: 7, button: 0, pointerType: 'mouse', clientX: 300, clientY: 220,
                    target: { closest: sel => sel === '#norecoil-panel-hide' } };
    handlers.pointerdown[0](onBtn);
    assert.equal(captured.length, 0);
    assert.equal((handlers.pointermove || []).length, 0);
});

test('NoRecoil panel: dragging the head moves the panel and clamps to the viewport', () => {
    const { elements, handlers, captured } = panelFixture();
    handlers.pointerdown[0]({ pointerId: 3, button: 0, pointerType: 'mouse', clientX: 120, clientY: 230, target: { closest: () => null } });
    assert.equal(captured[0], 3); // 正常拖拽仍捕获指针
    handlers.pointermove[0]({ pointerId: 3, clientX: -500, clientY: 99999 }); // 越界，应被钳制
    assert.equal(elements['norecoil-panel'].style.left, '0px');
    assert.equal(elements['norecoil-panel'].style.top, '1050px'); // innerHeight 1080 - 30
    assert.equal(elements['norecoil-panel'].style.right, 'auto');
    assert.equal(elements['norecoil-panel'].style.bottom, 'auto');
});

test('NoRecoil panel: hide collapses to the NoRecoil button and the button re-expands the panel', () => {
    const { elements } = panelFixture();
    const panel = elements['norecoil-panel'], chip = elements['norecoil-panel-toggle'];
    panel.style.display = 'block';
    elements['norecoil-panel-hide'].onclick();
    assert.equal(panel.style.display, 'none');
    assert.equal(chip.style.display, 'block');
    chip.onclick();
    assert.equal(panel.style.display, 'block');
    assert.equal(chip.style.display, 'none');
});

test('norecoil panel: independent endpoint and output', async () => {
    const { elements, fetches } = panelFixture();
    elements['norecoil-on'].onclick();
    assert.equal(fetches.length, 1);
    assert.match(fetches[0].url, /^\/api\/norecoil\?token=/);
    assert.deepEqual(JSON.parse(fetches[0].opts.body), { command: 'on' });
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(elements['norecoil-out'].textContent, 'ok');
});

test('NoRecoil panel: losing developer access hides the panel and launcher', () => {
    const { context, elements, fetches } = panelFixture();
    vm.runInContext('setNorecoilAvailable(true)', context);
    assert.deepEqual(JSON.parse(fetches[0].opts.body), { command: 'status' });
    elements['norecoil-panel-hide'].onclick();
    vm.runInContext('setNorecoilAvailable(false)', context);
    assert.equal(elements['norecoil-panel'].style.display, 'none');
    assert.equal(elements['norecoil-panel-toggle'].style.display, 'none');
});
