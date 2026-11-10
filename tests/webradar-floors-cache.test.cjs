const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const html = fs.readFileSync(`${__dirname}/../web/webpage.html`, 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function stubElement() {
    const classes = new Set();
    const el = {
        style: {}, dataset: {}, children: [],
        addEventListener: () => {},
        appendChild: c => el.children.push(c),
        classList: {
            add: name => classes.add(name),
            remove: name => classes.delete(name),
            contains: name => classes.has(name),
            toggle: (name, force) => { (force === undefined ? !classes.has(name) : force) ? classes.add(name) : classes.delete(name); },
        },
    };
    Object.defineProperty(el, 'innerHTML', {
        get() { return ''; },
        set() { el.children.length = 0; },
    });
    return el;
}

// 楼层配置 + 地图缓存切片（mapFloors ... applyMapBackground，位于密码门区域内）
function floorsCacheFixture() {
    const fetchCalls = [];
    const elements = { 'floor-tabs': stubElement(), 'calibrate-toggle': stubElement() };
    const context = vm.createContext({
        Math, JSON, Promise, Date, console,
        document: {
            getElementById: id => elements[id] || null,
            createElement: () => stubElement(),
        },
        fetch: async (url, options) => { fetchCalls.push({ url, options }); return { ok: false, status: 404 }; },
        currentMap: 'jy.png', radarAuthed: true, egoMode: false,
        map: { style: {} }, egoMapImages: {},
        authPassword: () => 'pw',
        mapImageUrl: name => `/image/${name}?token=pw`,
        selectMap: () => {},
    });
    vm.runInContext(script.slice(script.indexOf('    const mapFloors ='),
        script.indexOf('    function lockRadar(')), context);
    return { context, elements, fetchCalls };
}

test('floor config: every floor image has dimensions, calibration and aim model entries', () => {
    const floorImages = [...script.matchAll(/map:\s*'([\w.]+)'/g)].map(m => m[1]);
    assert.ok(floorImages.length >= 5, '至少应包含 jy 四层与 az3 内部图');
    for (const name of floorImages) {
        const key = name.replace('.', '\\.');
        assert.match(html, new RegExp(`"${key}"\\s*:\\s*\\{\\s*width:\\s*\\d+,\\s*height:\\s*\\d+`), `${name} 缺 mapDimensions 条目`);
        assert.match(html, new RegExp(`"${key}"\\s*:\\s*\\{\\s*point3D_1`), `${name} 缺 mapConverters 条目`);
        assert.match(fs.readFileSync(`${__dirname}/../web_radar.py`, 'utf8'), new RegExp(`"${key}"\\s*:`), `${name} 缺 RADAR_MAP_MODELS 映射`);
    }
});

test('mapLabelOf: main maps show base name, floors show owner·label', () => {
    const { context } = floorsCacheFixture();
    assert.equal(vm.runInContext('mapLabelOf("jy.png")', context), 'JY');
    assert.equal(vm.runInContext('mapLabelOf("jy_2f.png")', context), 'JY·2F');
    assert.equal(vm.runInContext('mapLabelOf("az3_in.png")', context), 'AZ3·内部图');
    assert.equal(vm.runInContext('mapLabelOf("db.png")', context), 'DB');
    assert.equal(vm.runInContext('floorOwnerOf("ht.png")', context), null);
    assert.equal(vm.runInContext('floorOwnerOf("jy_3f.png")', context), 'jy.png');
});

test('updateFloorTabs: hidden without floors or when locked; buttons ordered overview-first', () => {
    const { context, elements } = floorsCacheFixture();
    const tabs = elements['floor-tabs'];
    const made = () => tabs.children;
    vm.runInContext('currentMap = "db.png"; updateFloorTabs()', context);
    assert.equal(tabs.style.display, 'none');           // 无分层地图隐藏
    vm.runInContext('currentMap = "jy.png"; updateFloorTabs()', context);
    assert.equal(tabs.style.display, 'flex');
    assert.deepEqual(made().map(c => c.dataset.map), ['jy.png', 'jy_4f.png', 'jy_3f.png', 'jy_2f.png', 'jy_1f.png']);
    assert.deepEqual(made().map(c => c.textContent), ['总览', '4F', '3F', '2F', '1F']);
    assert.equal(made()[0].classList.contains('active'), true);
    vm.runInContext('currentMap = "jy_2f.png"; updateFloorTabs()', context); // 楼层图上同样显示选项卡
    assert.equal(tabs.style.display, 'flex');
    assert.equal(made()[3].classList.contains('active'), true);
    vm.runInContext('radarAuthed = false; updateFloorTabs()', context);     // 锁定时隐藏
    assert.equal(tabs.style.display, 'none');
});

test('applyMapBackground: cache miss falls back to server URL, hit uses objectURL without network', async () => {
    const { context, fetchCalls } = floorsCacheFixture();
    vm.runInContext('applyMapBackground("jy.png")', context);
    assert.equal(context.map.style.backgroundImage, "url('/image/jy.png?token=pw')");
    assert.equal(fetchCalls.length, 1); // 未命中时后台取图入缓存
    vm.runInContext('applyMapBackground("jy.png")', context);
    assert.equal(fetchCalls.length, 1); // 每会话每图只取一次
    vm.runInContext('mapCacheUrls["jy_3f.png"] = "blob:mock-3f"; mapCachePrimed["jy_3f.png"] = true; applyMapBackground("jy_3f.png")', context);
    assert.equal(context.map.style.backgroundImage, "url('blob:mock-3f')"); // 命中：零网络
    assert.equal(fetchCalls.length, 1);
});

test('probeDevSession: only dev sessions reveal the calibrate button', async () => {
    const { context, elements } = floorsCacheFixture();
    const btn = elements['calibrate-toggle'];
    let reply = { dev: true };
    context.fetch = async () => ({ ok: true, status: 200, json: async () => reply });
    await vm.runInContext('probeDevSession()', context);
    assert.equal(btn.classList.contains('dev-visible'), true);   // dev: 加 class 显示
    reply = { dev: false };
    await vm.runInContext('probeDevSession()', context);
    assert.equal(btn.classList.contains('dev-visible'), false);  // 非 dev: 摘掉 class
    // CSS 必须同时具备默认隐藏与 .dev-visible 显示两条规则（防止回退到 none 的回归）
    assert.match(html, /#calibrate-toggle\s*\{\s*display:\s*none;\s*\}/);
    assert.match(html, /#calibrate-toggle\.dev-visible\s*\{\s*display:\s*inline-block;\s*\}/);
});

test('initMapSelect: dropdown lists main maps only', () => {
    const elements = { 'selection-select': stubElement(), 'map-options': stubElement(), 'selection-summary': stubElement() };
    const context = vm.createContext({
        document: {
            getElementById: id => elements[id],
            createElement: () => stubElement(),
        },
        mapDimensions: {
            'db.png': { width: 1, height: 1 }, 'jy.png': { width: 1, height: 1 },
            'jy_1f.png': { width: 1, height: 1 }, 'jy_4f.png': { width: 1, height: 1 },
            'az3.png': { width: 1, height: 1 }, 'az3_in.png': { width: 1, height: 1 },
        },
        mapFloors: {
            'jy.png': [{ label: '4F', map: 'jy_4f.png' }, { label: '1F', map: 'jy_1f.png' }],
            'az3.png': [{ label: '内部图', map: 'az3_in.png' }],
        },
        floorMaps: { 'jy_4f.png': 'jy.png', 'jy_1f.png': 'jy.png', 'az3_in.png': 'az3.png' },
        selectMap: () => {}, syncSelectionSummary: () => {},
    });
    vm.runInContext(script.slice(script.indexOf('    function initMapSelect('),
        script.indexOf('    function syncSelectionSummary(')), context);
    vm.runInContext('initMapSelect()', context);
    const options = elements['map-options'].children;
    assert.deepEqual(options.map(o => o.value), ['map:db.png', 'map:jy.png', 'map:az3.png']); // 楼层图不进下拉
    assert.deepEqual(options.map(o => o.textContent), ['DB', 'JY', 'AZ3']);
});
