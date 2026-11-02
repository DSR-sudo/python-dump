# 网页雷达：点选角色进入视角跟随（航向朝上），手势退出回正北

## 目标

- `web/webpage.html`（即 webradar）：
  1. 鼠标点击 / 触摸选中角色后，地图跟随其视角**旋转**——选中角色恒在屏幕中心、其白色方向杆恒指正上方（航向朝上 heading-up），世界随角色转向而转动；
  2. 任何手动地图手势（拖拽平移、滚轮、双指缩放）退出旋转，回到常规雷达：每根方向杆恢复各自绝对朝向（角色自己脚下那根也回到真实朝向），**地图正北朝上**；缩放类手势仍保持以选中角色为中心的跟随。

## 改动（web/webpage.html）

- **命中兜底**：新增 `nearestEntityAt()`——标记本体只有 8px，DOM 未命中时按标记屏幕位置取半径内最近者（触摸 32px / 鼠标 16px，`pointerType` 区分）；旋转状态下屏幕位置绕视图中心做同样角度换算。拖拽（>8px）、双击进 Ego、350ms 双击窗口等语义不变。
- **视角跟随（航向朝上）**：
  - 状态 `mapRotationDeg/Target/At` + `viewFollowRotation`；`selectPlayer(id)` 置 `viewFollowRotation = !!playerId`（下拉选回"自己"传 null，保持正北）。
  - `recenterView()` 在居中平移之外，视角跟随且目标来自点选时 `setMapRotationTarget(-visual.rotation)`——旋转角与方向杆同源（同一校准变换），叠加后选中角色方向杆屏幕角度恒为 0；本机默认跟随 / 手动退出 / 校准模式目标角为 0。
  - `applyMapTransform()` 在旋转非 0 时输出 `translate(a,b) rotate(R) scale(s)`，把"绕视图中心旋转"折算进 translate（`#map` 的 transform-origin 是 0,0）：中心点旋转前后不动，被跟随角色自然成为转轴。
  - `advanceMapRotation()` 每帧向目标角指数趋近（时间常数约 60ms，数百毫秒收敛；最短路径防 179°↔−179° 绕远）。
  - `renderRadarMotion()` 顺序：recenterView → 推进旋转角 → 更新标记（方向杆保持绝对朝向；名字/武器标签按 −当前角反向补偿保持水平可读）→ 需要时重写 transform。
- **手势退出（回正北）**：
  - 滚轮 / 双指缩放：`viewFollowRotation=false` + `setMapRotationTarget(0)`，**保持居中跟随**（滚轮不再清 `isFollowing`）；
  - 单指拖动 >8px：手动平移，`isFollowing=false` + 旋转归零（显式 `setMapRotationTarget(0)`——退出跟随后 `recenterView` 不再跑，否则旋转会卡住不回正）；
  - `pointerdown` 不再立刻清 `isFollowing`（纯点击不中断跟随），由 >8px 位移分支判定平移；
  - 校准模式进入时旋转立即归零（`screenToMapPoint` 只做平移/缩放逆变换）。

## 已知取舍

- 视角跟随时资源点/盒子(I/D)的小标签随地图一起倾斜（实体名字标签有逐帧反向补偿，资源标签按需重建、未补偿）——短暂模式内的小瑕疵。
- 视角跟随时选中角色脚下方向杆在屏幕上"不动"（恒指上方），其它角色方向杆随地图转动，体现与选中角色的相对朝向——这正是航向朝上模式语义。

## 测试（tests/webradar-view.test.cjs + webradar-motion.test.cjs，29/29 通过）

- 几何兜底：半径内取最近 / 半径外算空地 / **旋转态下命中换算正确**；
- 触摸点在地图背景（DOM 未命中）仍经兜底选中；
- 未开视角跟随时：跟随居中、transform 无 rotate（回归锁）；
- **视角跟随**：目标角 = −朝向角、补间收敛、transform 含 `rotate(-90deg)`、方向杆绝对 90°、名字标签反向补偿、旋转后角色仍精确落在视图中心（绕中心旋转的数学验证）；
- 手势退出：滚轮退出旋转且目标角归零但保持 `isFollowing`；拖拽停跟随；单击/双指不再顺带停跟随；
- 原有 22 例（单击/双击/指针捕获/捏合/校准等）全部不破坏。

## 手动验证建议

- 点击/触摸任一角色：地图平滑转到其面朝方向朝上，角色居中；角色转身时地图随之转动，其方向杆恒指上方、名字保持水平；
- 滚轮或双指缩放：地图平滑转回正北，角色仍在中心，缩放以角色为轴；
- 单指拖动：自由平移、退出跟随，地图正北；
- 再点一次该角色：重新进入视角跟随。
