# 网页雷达：点击/触摸选中角色后地图可靠跟随（正北朝上）

## 目标

- `web/webpage.html`（即 webradar）：鼠标点击或触摸选中角色时，地图跟随该角色移动，视角保持正北（正上方，不旋转）。

## 现状结论（改动前）

- 跟随链路已存在：单击/触摸角色 → 350ms 双击窗口后 `selectPlayer()` → `isFollowing = true` → rAF 循环 `renderRadarMotion()` 每帧 `recenterView()`，用平滑插值位置平移地图使选中角色居中；`applyMapTransform()` 只有 `translate + scale`，从无 `rotate`，天然正北朝上。
- 真正缺口在命中：`.entity-body` 仅 8px、血环 14px、姓名条 `pointer-events: none`，手指几乎点不中；点不中即不选中，且 `pointerdown` 会把 `isFollowing` 置 false，表现是"点角色没反应、跟随还被关掉"。

## 改动

- `web/webpage.html`
  - 新增 `nearestEntityAt(clientX, clientY, maxDistance)`：DOM 未命中时，用标记容器当前 `left/top`（地图坐标）按 `offset + pos * scale` 换算屏幕位置，取半径内最近的角色。
  - `mapContainer` 的 `pointerdown` 命中改为 `entityFromTarget(e.target) || nearestEntityAt(...)`，半径触摸 32px、鼠标 16px（`e.pointerType` 区分）。
  - 其余语义不变：位移超 8px 仍是拖拽（取消选中）、双指缩放/滚轮取消待选、双击进 Ego 视角、单击延迟 350ms 后按 mesh key 取最新记录选中。

## 测试

- `tests/webradar-view.test.cjs` 新增 3 例：
  - 几何兜底半径内取最近标记、半径外算空地；
  - 触摸点在地图背景（DOM 未命中）仍可经兜底选中进入跟随；
  - `recenterView()` 随角色移动更新 offset 且 transform 无 `rotate`（跟随 + 正北朝上的回归锁）。
- `node --test tests/webradar-view.test.cjs` 16/16 通过（原 13 例不破坏）；`tests/webradar-motion.test.cjs` 9/9 通过。

## 手动验证建议

- 真机/手机浏览器打开雷达：指尖点角色圆点附近（±30px 内）→ 地图在约 0.35s 后居中并持续跟随该角色，方向杆转动而地图不旋转；
- 快速双击角色仍进入 Ego 第一视角；拖动、双指缩放、滚轮仍会解除跟随。
