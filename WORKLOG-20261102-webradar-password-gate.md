# 网页雷达：密码门——验证通过前不拉地图、各功能函数为空

## 目标

- 只有输入正确密码，才能拉取地图文件和使用雷达全部功能；
- 无密码（或密码错误）时页面只显示全屏密码门，各功能函数为空操作：不发 `/api/data` 轮询、不发订阅、不设置地图背景图、手势/滚轮/校准直接返回。

## 改动

### web/webpage.html

- **密码门 UI**：原侧栏的密码行改为全屏遮罩 `#auth-gate`（z-index 999，盖住所有控件），输入框改 `type=password`；验证通过才隐藏。
- **门控状态**：`radarAuthed` / `pollLoopRunning`。锁定时下列函数开头直接 return（"空函数"）：
  `updateGameData`（不发轮询）、`pushSubscription`（不发订阅）、`selectMap`（不加载地图文件）、
  `mapContainer` 的 `pointerdown`/`wheel`（手势不可用）、`setCalibrateMode`（校准不可用）。
  rAF 动效循环只由 `updateGameData` 启动，锁定时自然不跑。
- **验证流程**：`tryAuth()` 用输入的密码请求一次 `/api/data`——200 即解锁（`unlockRadar`：隐藏密码门、`selectMap` 加载地图、启动 60Hz 轮询、推送订阅）；401 显示"密码错误"并清除已存密码；空密码/服务器不可达保持锁定。开机用 `localStorage.authToken` 自动验证一次。
- **会话中断**：轮询中收到 401（如服务端重启换了密码）→ `lockRadar`：清地图背景、停轮询、回到密码门。
- **地图 URL 带 token**：`mapImageUrl(name)` = `/image/<name>?token=<密码>`，主地图背景与 Ego 小地图取图统一走它（img 请求无法带 header，只能查询参数）。

### web_radar.py

- **`/image/` 补上鉴权**（原来零鉴权，curl 就能拿地图）：与 `/api/*` 相同的 `_is_authorized` 校验（`?token=` 或 `X-Auth-Token` 头，兼容万能密码 `963007`），不通过回 401。目录穿越防护不变。

## 测试（35/35 通过）

- 新增 `tests/webradar-auth.test.cjs`（6 例）：
  - 锁定时 `updateGameData`/`pushSubscription`/`selectMap` 均为空操作——零请求、密码门可见、不切地图；
  - 空密码不发请求；错误密码 401 → 保持锁定、显示错误、忘记已存密码、不起轮询；
  - 正确密码 → 解锁：密码门隐藏、地图背景带 token 加载、轮询循环启动、验证请求带 `X-Auth-Token`；
  - 轮询中 401 → 重新上锁：清地图、循环自停；
  - `scheduleGameDataPoll` 锁定时不起定时器、解锁后逐轮续排、中途锁定下一跳自停。
- 原有 29 例回归通过（手势/滚轮 fixture 补 `radarAuthed: true` 模拟已解锁）。
- 服务端实测（起真实 HTTP 服务，9 项全过）：`/image/` 无密码/错 token 401、正确 token 与 `963007` 200、头携带也 200、`/api/data` 鉴权回归、目录穿越仍 404、文件内容完整。

## 部署与已知取舍

- **服务端 `web_radar.py` 需同步部署**（如 `/root/python-dump/`），否则 `/image/` 仍是旧的无鉴权行为（页面功能不受影响，只是地图文件还能被直接下载）。
- token 走 URL 查询参数，会出现在浏览器地址记录/服务端访问日志（本服务日志已关）；这是 img 标签无法带请求头下的标准做法，对私有雷达可接受。
- 密码本身仍是服务端每次启动随机生成（写在 `web/pwd.txt`），前端不做本地比对，验证始终以服务端 401/200 为准，源码里不含任何密码。
