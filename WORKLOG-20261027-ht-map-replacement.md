# 官网地图替换与校准工作记录

## 目标

- 从腾讯《三角洲行动》官方地图目录页面 `https://df.qq.com/cp/a20240729directory/` 获取“航天基地”地图图片。
- 用官方图片替换 `image/ht.png`，保持项目可正常读取。
- 2026-10-28 接续：更新大坝 `db.png`、长弓溪谷 `cg.png`、巴克什 `bks.png`，同步迁移各图校准。

## 已知信息

- 目标文件：`image/ht.png`
- 替换前格式：PNG，RGB，`921 x 989`
- 替换前 SHA-256：`e6ae9a7bcf0c1eb8bd87086b168a986d2bcb1d7ac5ee198a10e4326e7000975b`
- `web/webpage.html` 中存在 `ht.png` 的尺寸和地图配置引用。

## 调查

### 官方网页资源

已通过官网 HTML、官网 JavaScript 和实际 HTTP 响应确认：该页面没有提供一张可直接下载的“航天基地”整图，而是使用 Leaflet JPEG 瓦片。

- 页面 `https://df.qq.com/cp/a20240729directory/` 的地图列表将 `data-index="2"` 对应到“航天基地”；HTML 同时加载官方脚本 `js/lib/map_htjd.js`。
- `js/lib/main.js` 中 `IMG_PRE` 在 qq.com 页面上为 `//game.gtimg.cn/images/dfm/cp/a20240729directory`，默认图层根目录为 `${IMG_PRE}/img/`，Leaflet 模板为 `${href}${mapName}/{z}_{x}_{y}.jpg`，普通图层的 `tileSize` 是 256、`maxNativeZoom` 是 4。
- `main.js` 的航天基地配置：`21`（机密）和 `22`（绝密）使用 `layer: 'map_htjd'`；`21_s`、`22_s` 是断桥事件变体，使用 `layer: 'map_htjd2'`。因此普通航天基地的官方资源 URL 模板为：
  `https://game.gtimg.cn/images/dfm/cp/a20240729directory/img/map_htjd/{z}_{x}_{y}.jpg`
  断桥事件变体模板为：
  `https://game.gtimg.cn/images/dfm/cp/a20240729directory/img/map_htjd2/{z}_{x}_{y}.jpg`
- 实际验证普通图层代表资源 `https://game.gtimg.cn/images/dfm/cp/a20240729directory/img/map_htjd/4_0_0.jpg`：HTTP 200，`Content-Type: image/jpeg`，图像尺寸 256 x 256；越界示例 `.../map_htjd/4_16_0.jpg` 返回 HTTP 404。
- 实际批量验证普通图层 z=4 的 x、y 范围为 0..15，共 256 个 JPEG 瓦片，全部为 256 x 256；按 x/y 拼接得到 4096 x 4096 的 JPEG 覆盖图。z=3 的范围为 0..7，共 64 个瓦片，拼接尺寸为 2048 x 2048。这里的 4096 x 4096 是根据官方瓦片拼接得到的地图像素尺寸，不是网页声明的单文件尺寸。
- `map_htjd.js` 另声明 `htjdInfo.width` 和 `height` 均为 `65442.871094`，这是地图坐标/边界信息，不应直接当作图片像素尺寸。

推荐使用普通图层 z=4 的瓦片模板（或先下载 16 x 16 块再按 `x*256, y*256` 拼接成 4096 x 4096 JPEG），不要只下载 `4_0_0.jpg` 作为整图。若目标是断桥事件版本，必须改用 `map_htjd2`；它同样是 JPEG 256 x 256 瓦片，但内容不同。下载时保留 `.jpg` 扩展名，并检查响应状态和 `Content-Type`，因为不存在的瓦片会返回 404 HTML。

### 本地引用与约束

- 主工作树中对 `ht.png` 的显式引用只有 `web/webpage.html`：`mapDimensions.ht.png` 固定为 `width: 921, height: 989`（约第 163 行），`mapConverters.ht.png` 的校准点为 `point2D_1=(104,180)`、`point2D_2=(686,789)`，对应世界点 `(-27015,-18887)`、`(17252,27568)`（约第 187-192 行）。这些 `point2D` 是图像像素坐标，替换图必须保持同一画布尺寸和地图内容对齐，否则角色/物资位置换算会偏移。
- 前端通过 `selectMap()` 将背景设为 `/image/${currentMap}`，并用 `mapDimensions` 直接设置 `#map` 的 CSS 宽高；视角小地图也通过 `/image/${name}` 加载。因此图像自然尺寸不会自动成为布局尺寸，`ht.png` 改尺寸而不同时改 `mapDimensions`/校准点会被强制拉伸并破坏校准。页面背景使用 `background-size: 100% 100%`。
- 服务端 `web_radar.py` 的 `/image/<name>` 路由按原始字节返回文件，MIME 由扩展名推断；实际目录优先级是环境变量 `DMA_WEBRADAR_IMAGE_DIR`，其次是存在时的 `/root/python-dump/image`，最后才是当前代码根目录下的 `image/`。部署时应确认替换的是运行时实际使用的目录，而不只是假定的仓库路径。
- 同目录地图共六张，当前均为 8-bit/color RGB、非交错 PNG，且文件尺寸与 `mapDimensions` 一致：`db 1244x956`、`cg 1333x936`、`bks 833x950`、`ht 921x989`、`jy 1013x1009`、`az3 2048x2048`。`ht.png` 当前 SHA-256 为 `e6ae9a7bcf0c1eb8bd87086b168a986d2bcb1d7ac5ee198a10e4326e7000975b`。
- 本地还有 `.claude/worktrees/view-pitch-wire/` 的工作树镜像，含同名 `web/webpage.html` 和六张逐字节相同的地图文件；它不是主工作树的运行目录，但若该工作树单独运行，也需要同步替换和校准配置。
- 现有 `tests/webradar-view.test.cjs` 只覆盖前端交互/插值行为，没有检查图像存在性、PNG 元数据、尺寸或 `/image/ht.png` HTTP 响应。可用 `file image/*.png`（尺寸/色彩/交错）、`sha256sum image/ht.png`（内容固定性）、`cmp`（工作树镜像一致性）以及启动 `WebRadarService` 后用 GET（例如 `curl -sS -D headers.txt -o /tmp/ht.png http://127.0.0.1:<port>/image/ht.png`）校验 `/image/ht.png` 的状态、`Content-Type`、响应字节和 SHA-256；服务端当前只实现 GET，不应使用 HEAD 检查。另运行 `node --test tests/webradar-view.test.cjs` 检查页面脚本未被破坏。

## 决定

- 使用普通航天基地图层 `map_htjd`，不使用断桥事件变体 `map_htjd2`。
- 下载最高原生缩放层级 `z=4` 的全部 256 个瓦片，按 `x * 256, y * 256` 拼成官方完整 `4096 x 4096` 地图，并转换为项目现有的 PNG 格式。
- 不把完整地图压缩或裁剪进旧 `921 x 989` 画布。旧图只是完整官网底图的局部裁剪，强行沿用旧画布会丢失官网完整地图或产生非等比变形。
- 同步更新 `web/webpage.html` 的地图尺寸和校准点。通过 SIFT 特征匹配和 RANSAC 相似变换得到旧图到官网整图的变换：约 `new = old * 2.381161 + (932.802, 833.174)`；463 个匹配内点的平均重投影误差约 1.1 像素。
- 由该变换将原有两个 2D 校准点换算为 `(1180.47,1261.77)` 和 `(2566.39,2711.82)`，保持既有世界坐标不变。

## 修改

- 将官网 `map_htjd` 的 `z=4` 全部 256 个 JPEG 瓦片下载到临时目录，逐块检查响应类型和 `256 x 256` 尺寸，按 `y` 行、`x` 列拼接。
- 将拼接结果以 RGB PNG 写入 `image/ht.png`。新文件尺寸为 `4096 x 4096`，大小为 23,277,810 字节，SHA-256 为 `8ca114221bd4f85280e1eaa68246bbd95d3ab71cceed5c0fd6cbccd86cf19153`。
- 更新 `web/webpage.html` 中 `ht.png` 的画布尺寸为 `4096 x 4096`。
- 更新 `ht.png` 的两个 2D 校准点为 `(1180.47,1261.77)` 和 `(2566.39,2711.82)`。

## 验证

- 逐一解码 256 个下载瓦片，与最终 PNG 对应的 `256 x 256` 区域比较：所有像素完全一致，确认无错位、漏块或拼接顺序错误。
- `file image/ht.png`：PNG、`4096 x 4096`、8-bit RGB、非交错。
- `node --test tests/webradar-view.test.cjs`：13 项全部通过。
- 使用临时端口和显式 `DMA_WEBRADAR_IMAGE_DIR` 启动 `WebRadarService`，实际请求 `GET /image/ht.png`：HTTP 200、`Content-Type: image/png`、`Content-Length: 23277810`；响应字节与磁盘文件完全一致，SHA-256 为 `8ca114221bd4f85280e1eaa68246bbd95d3ab71cceed5c0fd6cbccd86cf19153`。
- 特征匹配校准验证：463 个 RANSAC 内点，平均重投影误差约 1.1 像素；新校准点由同一个等比变换计算，未改变对应世界坐标。

## 剩余问题

- 仓库内 `.claude/worktrees/view-pitch-wire/` 是独立工作树镜像，本任务未修改；若后续从该工作树单独运行服务，需要另行同步本次图片和配置。
- 实际部署若设置了 `DMA_WEBRADAR_IMAGE_DIR` 或存在 `/root/python-dump/image`，运行时可能优先读取外部目录；本任务已完成当前仓库 `python-dump` 的替换。

## 2026-10-28 三图官网资源调查

### 结论

官网页面入口：`https://df.qq.com/cp/a20240729directory/`。当前页面使用的公共 CDN 根目录是：
`https://game.gtimg.cn/images/dfm/cp/a20240729directory/img/`。
`main.js` 的统一模板（约 1408 行）为：
`{IMG_PRE}/img/{layer}/{z}_{x}_{y}.jpg`，普通图层 `tileSize: 256`、`maxZoom: 8`、`maxNativeZoom: 4`；因此网页实际展示普通地图时以 z=4 为原生上限。

| 地图 | main.js 名称/层级映射 | 普通官方模板 | 普通配置最高原生层级 |
|---|---|---|---|
| 零号大坝 | `00` 常规、`01` 机密、`02` 终夜、`03` 永夜均 `layer: 'map_db'` | `https://game.gtimg.cn/images/dfm/cp/a20240729directory/img/map_db/{z}_{x}_{y}.jpg` | z=4（脚本配置） |
| 长弓溪谷 | `10` 常规、`11` 机密均 `layer: 'map_yc'` | `https://game.gtimg.cn/images/dfm/cp/a20240729directory/img/map_yc/{z}_{x}_{y}.jpg` | z=4（脚本配置） |
| 巴克什 | `30` 常规、`31` 机密、`32` 绝密均 `layer: 'map_bks2'` | `https://game.gtimg.cn/images/dfm/cp/a20240729directory/img/map_bks2/{z}_{x}_{y}.jpg` | z=4（脚本配置） |

### 版本/事件区别

- 长弓溪谷 `10_s`、`11_s`（页面标注“坠机事件”）改用 `layer: 'map_yc2'`，模板为 `.../img/map_yc2/{z}_{x}_{y}.jpg`；普通版本不要使用该目录。
- 航天基地（用于对照）`21`/`22` 普通使用 `map_htjd`，`21_s`/`22_s`“断桥事件”使用 `map_htjd2`。抽样 `map_htjd/4_0_0.jpg` 与 `map_htjd2/4_0_0.jpg` SHA-256 分别为 `20b910ecd7811e6a1279436523b669e1cc49d27a97d6e19d2e469a8fc57006c1`、`2784ef002263028a734f68c3b5cee10e7ee50ff19abe21d2fbcd08a5675e6c86`，确认事件层内容不同。
- 大坝在当前 `main.js` 中没有 `_s` 事件层；不要因旧资料或随机事件逻辑将其改成其他目录。
- CDN 仍存在旧目录 `map_bks`（抽样 `4_0_0.jpg` SHA-256=`85175f33a38c646b48152d96ac384516a099cb7de2b90c3c77061bf788471e47`），但当前 `main.js` 的普通巴克什三种难度明确全部映射到 `map_bks2`（抽样 SHA-256=`f63b28ae581ef3514183bcae18b4c37de845cd95f4f7be0d7738db4fc1f9e620`）；`map_bks` 应视为旧/未使用资源，不能替代当前普通图。

### CDN 实测证据（2026-10-28）

- `map_db`、`map_yc`、`map_bks2` 的 `z=4`：`0_0`、`15_15` 均 HTTP 200、`image/jpeg`，`16_16` HTTP 404；即当前网页 z=4 瓦片网格为 x/y=0..15（256 块，拼接画布 4096x4096）。
- 三个普通目录的 `z=5` 也实际存在：`0_0`、`31_31` HTTP 200、`image/jpeg`，`32_32` HTTP 404；文件声明均为 JPEG 256x256。`z=6/0_0` 均 HTTP 404。因此从“CDN 可下载的最高层”看三图均为 z=5（1024 块，理论拼接 8192x8192），但官网 Leaflet 代码把普通层 `maxNativeZoom` 限制为 z=4，页面不会自动使用 z=5。
- 事件层抽样：`map_yc2` 的 z=4 与 z=5 存在、z=6 404；`map_htjd2` 仅 z=4 存在，z=5/6 404。所有响应通过 GET 检查，避免把不存在的瓦片返回的 404 HTML 当成图片。

### 证据定位

- 入口 HTML 当前列出地图名顺序：`data-index=0` 零号大坝、`1` 长弓溪谷、`2` 航天基地、`3` 巴克什；并加载 `js/lib/main.js`、`map_article.js`、`map_cgxg.js`、`map_htjd.js`、`map_bks.js`。
- `main.js`：`queryMap` 将 `daba/cgxg/htjd/bks` 映射为 `00/10/21/31`；`changeMapLv()` 的 `mapConfigs` 明确记录上述 `layer` 与名称/难度；`addLayer()` 明确记录 URL 模板、`tileSize` 和 `maxNativeZoom`。
- 配置文件尺寸/坐标信息（不是图片像素尺寸）：`dabaInfo` 81086.304688 x 80988.5、`cgxgInfo` 112800 x 112800、`bksInfo` 60000 x 60000；这些值不能替代瓦片拼接尺寸。

## 2026-10-28 三图更新、裁剪及校准结果

### 目标和决定

- 按用户要求下载官网当前普通版大坝、长弓溪谷、巴克什，并同步迁移校准。
- 用户追加要求去除周遭无关内容：采用旧图边界在新图中的映射范围裁剪，保留旧图覆盖的地图主体、新图原生像素细节，不做缩放重采样。
- 沿用航天基地更新规格，使用网页配置的最高原生层级 z=4（每图 256 块，共 768 块），完整底图为 4096x4096；未使用 CDN 额外提供的 z=5。
- 新校准由旧校准经过相似变换后，再减去裁剪左上角偏移得到；世界坐标不变。裁剪只改变原点，不改变倍率。

### 匹配数据和最终配置

变换定义：`X=a*x+b*y+tx`，`Y=-b*x+a*y+ty`，从旧图映射到官网完整图。最终坐标再减去裁剪左上角。

| 地图 | a | b | tx | ty | 内点数 | 平均误差（新图像素） |
| --- | --- | --- | --- | --- | --- | --- |
| 大坝 | 2.3702401853 | 0.0002001473 | 505.0543604 | 270.7584312 | 1120 | 0.9675 |
| 长弓 | 2.1563258028 | 0.0001191063 | 628.3827179 | 962.5257683 | 811 | 0.9684 |
| 巴克什 | 2.3809366439 | 0.0002462722 | 1083.7244743 | 979.3209874 | 1873 | 0.7316 |

裁剪框采用 `(left,top,right,bottom)`，右/下边界不包含。

| 文件 | 官网图层 | 裁剪框 | 最终尺寸 | 校准点1 | 校准点2 |
| --- | --- | --- | --- | --- | --- |
| image/db.png | map_db | (505,271,3454,2537) | 2949x2266 | (1114.12,580.37) | (2429.74,2241.80) |
| image/cg.png | map_yc | (628,962,3503,2981) | 2875x2019 | (119.06,1859.36) | (1962.62,508.19) |
| image/bks.png | map_bks2 | (1084,979,3067,3241) | 1983x2262 | (433.23,1657.41) | (1399.73,135.89) |

### 修改与验证

- 替换三个 PNG，并更新 `web/webpage.html` 对应尺寸和六个 2D 校准点。
- 768 个下载瓦片与拼接整图逐像素一致；最终裁剪图与完整图对应区域逐像素一致。
- 已查看三张完整图和裁剪预览，确认外围大面积无关背景被去除。
- 自动核对最终文件尺寸与页面配置一致；校准点与匹配变换加裁剪偏移的计算结果误差不超过 0.005 像素；新旧两点距离倍率与匹配倍率差小于 0.00002。
- 使用临时本地 HTTP 服务和显式图片目录，三个 GET 请求均 HTTP 200、image/png，响应字节与最终磁盘文件完全一致。
- 最终裁剪后重新执行前端测试：13/13 通过；`git diff --check` 通过。
- 大坝最终 SHA-256：`0bf1767cabf0521236592d32ca136bf00780d0e7fe6f30b389f1a2148d35e51e`，9,007,625 字节。
- 长弓最终 SHA-256：`7fdde355005da9a4e0e1e2c1ea94ca63ce0ef5f2c895bc56d54ddf39f306ba40`，4,847,980 字节。
- 巴克什最终 SHA-256：`da5eed50e3ab0a5485cc24fbc15303009f6fbff8c154b895d26f3d39617e4885`，7,823,008 字节。

### 剩余问题

- 校准为基于图像匹配的迁移，尚未在游戏内实时位置验证。
- 本次裁剪范围为新更新的大坝、长弓溪谷和巴克什三张图。

## 2026-10-28 航天基地补充裁剪

- 用户要求航天基地也执行同样裁剪。按旧图范围映射到官网完整图，裁剪框为 `(933,833,3126,3188)`，最终 `image/ht.png` 尺寸为 `2193x2355`，保留原生像素，不缩放。
- `web/webpage.html` 同步更新尺寸，两个校准点各减去 `(933,833)`，最终为 `(247.47,428.77)`、`(1633.39,1878.82)`；世界坐标及约 2.381161 倍的迁移倍率不变。
- 已查看裁剪预览，验证最终图与完整图的对应区域逐像素一致，实际页面尺寸与校准点计算一致；前端测试 13/13 通过，`git diff --check` 通过。
- 此节取代上文航天基地保留完整 4096x4096 画布的决定；四张更新地图均已裁剪。尚未验证游戏内实时位置。

## 2026-10-28 角色标记尺寸、血量环与 Z 字段调查

### 已实施：缩小角色标记

- 用户选择圆点直径 8px、血量环外径 14px/厚 1px，明确要求方向杆不变。
- 修改 `web/webpage.html` 的 `.entity-body` 为 8x8px，`.entity-health-ring` 为 14x14px；标准及 WebKit mask 均调整到 1px。
- 方向杆仍为 1.5x12px，地图缩放行为不变；物资和死亡标记保留各自原有独立尺寸规则。
- 验证：`git diff --check` 通过，前端测试 13/13 通过。尚未进行浏览器视觉验证。

### `web/webpage.html` 行号证据

- 主地图实体是绝对定位的 `.entity-container`，其子 `.entity-body` 为 `12px x 12px`、圆形、`1px` 白边（第 16-18 行）。全局 `box-sizing: border-box`（第 8 行），所以 12px 是含边框外径；方向杆为宽 1.5px、高 12px（第 18 行）。实体位置由 `getTransformedData()` 只取世界 `position.x/y` 转换为地图像素（第 234-240、244-255 行），`position.z` 不参与俯视图定位。
- 血量环 `.entity-health-ring` 为 `22px x 22px` 圆形（第 26 行），`conic-gradient` 按 `--health-ratio` 填充，随后用径向 `mask` 挖空内圈；`calc(100% - 2px)` 使环厚约 2px（含渲染抗锯齿）。创建条件是非 AI、拥有有效 `health/max_health` 的实体（第 539-550 行），因此队友和敌人均有环；AI 没有血量环。
- 环的颜色/比例和选中状态独立：比例为 `health/max_health`，>50% 绿、>25% 黄、否则红（第 544-550 行）。
- 角色颜色：同队实体默认 `.teammate` 绿色；当前自己或被选中的队友加 `.white-player` 白色（第 552-555 行）。敌人走 `getTeamColor(team_id)` 的团队调色板，未因 `selectedPlayerId` 改色（第 556-563 行）；AI 为橙色并显示 `A`，死亡角色是透明填充的有色圆环。地图顶部的“选中”机制实质只针对自己/队友按钮（第 1737 行），敌人没有同等的地图按钮选中样式。
- 地图 DOM 整体以 `translate(offsetX,offsetY) scale(scale)` 变换（第 448 行）；滚轮按 1.1 倍增减，范围 0.2 到 5（第 465-478 行）。因此圆点、血环、文字、方向杆等所有子像素都会随地图缩放同比放大/缩小；不存在单独的 marker 反缩放。
- 可直接把 `.entity-body` 的 12px 改小、`.entity-health-ring` 的 22px 改小，并把 mask 中的 2px 改薄（例如 1px）；但环建议保持外径至少比主体大 2-4px，否则主体会遮盖环。由于全图缩放，改动会在所有缩放级别同比生效。

### Z 字段现状

- 普通地图角色标记和物资标记不显示 `z` 或高度文本；俯视地图只消费 x/y（第 532、605-607 行）。
- 第一人称/Ego 视图保留 z：插值采样读取 `position.z`（第 325、365-406 行），小地图用 z 做 3D 半径过滤（第 987-1000 行），主视图用实体与相机的 `dz`，仅当绝对高度差至少 0.5m 时在标签显示 `↑/↓N.Nm`（第 1273-1279 行）。相机高度还会加 `EGO_EYE_HEIGHT_CM=160`（第 277、1094 行）。

### 航天基地坐标/地标映射核对

- 当前配置行 187-191 为世界点 `(-27015,-18887)` -> 图像 `(247.47,428.77)`，`(17252,27568)` -> `(1633.39,1878.82)`。
- 工作文档记录的旧图到官网整图变换约为 `new = old * 2.381161 + (932.802,833.174)`，裁剪左上偏移为 `(933,833)`；因此两个点先得到整图 `(1180.47,1261.77)`、`(2566.39,2711.82)`，再各减 `(933,833)`，正好得到当前配置。裁剪只改变图像原点，没有改变倍率/旋转，故是原图相同地标映射的平移表达，不是重新选取或错配地标。

## 2026-10-30 地图选择下拉菜单

### 目标

- 将页面顶部地图按钮组改为单个下拉菜单，保留地图切换、当前选择持久化和初始化行为。

### 发现

- 页面、样式和脚本都内嵌在 `web/webpage.html`；没有独立地图选择 JS/CSS 文件。
- HTML 第 81-84 行的 `#map-buttons` 是地图控件容器，`#top-controls` 下的第一个 `.button-group` 专用于地图按钮；第 12-13 行的 `.map-btn` 样式只服务于按钮。
- 第 663-680 行的 `loadSelection()`/`saveSelection()`/`selectMap()` 负责 `localStorage.selectedMap`、背景 URL、尺寸、校准和刷新。切换逻辑无需改变；`selectMap()` 中第 674 行的 `.map-btn` active 更新在改成 `<select>` 后应删除或改为同步 select 的 value。
- 第 1737 行的 `updateMapButtons()` 按 `Object.keys(mapDimensions)` 建立按钮并绑定 `onclick`。最小改法是让它建立一个 `select`（建议保留 `id="map-buttons"` 或改为明确的 `map-select`），为每个地图建立 `<option value="...">`，并在 `change` 时调用 `selectMap(event.target.value)`；初始化顺序第 1741-1746 行可保持不变。
- `mapDimensions` 第 159-166 行仍是唯一地图选项来源，当前顺序为 `db/cg/bks/ht/jy/az3`；不应改动地图配置或校准点。
- 现有 `tests/webradar-view.test.cjs` 只用 `vm.Script` 检查内嵌脚本可解析，并覆盖插值、实体点击和指针手势；没有 DOM 渲染测试，也没有地图控件断言。当前环境未发现 Chromium、Playwright 或 Puppeteer。

### 建议修改与验证

- HTML 用 `<select id="map-select" aria-label="选择地图"></select>` 替换 `#map-buttons`；CSS 增加固定高度、最小宽度、深色背景和文字颜色，避免沿用按钮的 flex 规则导致下拉菜单宽度异常。若保持 `#map-buttons` ID，可减少脚本改动但语义较弱。
- `updateMapButtons()` 应清空并填充 options，设置 `select.value = currentMap`，监听 `change` 调用 `selectMap(this.value)`；`selectMap()` 不再查询 `.map-btn`。仍由 `saveSelection()` 写回 `selectedMap`，因此刷新后应恢复选择。
- 静态验证：`node --test tests/webradar-view.test.cjs`、`git diff --check`；可补充测试断言 HTML 含唯一 `select`、无地图按钮生成逻辑及六个 option 来源不变。
- 运行验证：启动 `WebRadarService` 后 GET 页面，确认 HTTP 200 和页面包含下拉菜单；若有浏览器环境，检查初始选项与 localStorage 恢复、切换后背景/尺寸/校准刷新及移动端顶部布局。当前环境只能做脚本解析/静态和 HTTP 验证，不能做真实浏览器交互验证。

## 2026-10-30 自己/队友选择下拉菜单补充调查

### 发现

- `#player-buttons` 位于顶部控件第 85-87 行；`updatePlayerButtons()`（约第 1749 行）按“自己”加动态队友重建按钮。队友来源是 `/api/data` 的 `data.teammates`，在 `updateGameData()` 约第 515-522 行更新。
- `selectPlayer()`（约第 683-700 行）是统一选择入口：地图实体单击延迟确认和双击进入 Ego（约第 1633-1656 行）都会调用它；Ego 画布点击其他角色走 `switchEgoViewpoint()`（约第 846-864 行），目前单独写 `selectedPlayerId` 并刷新 `.player-btn` active。
- `loadSelection()` 恢复 `selectedPlayerId` 到 `pendingPlayerSelection`，首次数据到达后调用 `selectPlayer(pendingPlayerSelection)`；因此下拉菜单应在队友 option 建立后再恢复 value，不能只在页面初始化时设置一次。
- 队友列表是动态的，option 文本（`队友1`、`队友2`）和 value 应随每次列表变化重建；若保存的队友已离队，应回退到“自己”或至少避免 select 显示不存在的 value。注意当前代码先赋值 `teammates = data.teammates`，再用长度比较会失去旧长度；若按变化触发刷新，应在覆盖前记录旧列表/使用 ID 签名，或直接每次数据刷新同步控件。

### 最小修改建议

- 将 `#player-buttons` 改为 `<select id="player-select" aria-label="选择玩家"></select>`，`updatePlayerButtons()` 改为清空并生成 value 为空的“自己” option 和每个队友 option，最后设置 `select.value` 为当前有效 `selectedPlayerId`（无匹配时设为空）。change 事件调用 `selectPlayer(select.value || null)`。
- `selectPlayer()` 不再查询 `.player-btn`，改为直接设置 `player-select.value = playerId || ''`；这样地图角色点击、首次 pending 恢复和菜单选择共用同一同步路径。
- `switchEgoViewpoint()` 的按钮 active 更新也改为 `player-select.value = id || ''`，否则从 Ego 画布切换角色时下拉菜单会显示旧选项。
- 动态队友刷新应在 option 重建后保留仍存在的选择；若当前选择对应队友已消失，调用 `selectPlayer(null)` 清理持久化选择并回到“自己”，避免 `recenterView()` 持续寻找不存在目标。

### 建议验证

- 静态/脚本：`node --test tests/webradar-view.test.cjs`、`git diff --check`，并补充 HTML/脚本断言唯一存在 `player-select`、无 `.player-btn` 更新路径。
- 浏览器：首次数据到达后确认“自己”和队友 option；队友增删/顺序变化后选项更新；选择队友后地图实体颜色/跟随和 `localStorage.selectedPlayerId` 同步；点击地图角色及 Ego 视角角色后下拉值同步；已保存但已离队的 ID 回退“自己”。

### 实施与验证

- 将顶部地图按钮组替换为原生 `select`，保留六张地图原有简称和顺序，添加“选择地图”无障碍名称及深色样式。
- `initMapSelect()` 生成选项并绑定 change；继续调用原 `selectMap()`，同步下拉选中值，沿用地图尺寸、校准和本地选择记忆。
- 删除地图按钮专用状态样式；同步更新现有测试中用于截取脚本的函数名边界。
- 首次测试因旧函数名截取边界失效出现 4 项失败；修正边界后 `node --test tests/webradar-view.test.cjs` 13/13 通过，`git diff --check` 通过。
- 未进行真实浏览器视觉及点击验证（当前环境未安装浏览器自动化依赖）。

## 2026-10-30 地图与自己/队友合并为单一下拉菜单

### 目标与状态约束

- 用户要求顶部地图和自己/队友选择合并为一个原生 `<select>`；地图状态 `currentMap` 与角色状态 `selectedPlayerId` 仍须同时保留，不能让一次选择覆盖另一个状态。
- 建议使用一个 `#selection-select`，内部包含两个 `<optgroup>`：地图组选项 value 为 `map:<filename>`，角色组选项 value 为 `player:`（自己）或 `player:<id>`（队友）。不要直接使用原始文件名/ID，避免值空间冲突。
- 原生 select 展开时可选择具体动作；收起后没有同时显示两个独立 value 的能力。建议首项为 disabled 的摘要 option（例如 `当前：大坝 · 自己`），每次动作完成或动态列表刷新后将 `select.value` 恢复为摘要 value，避免控件只显示最近一次动作而隐藏另一状态。摘要文本应由当前地图简称和当前角色标签动态生成。

### 最小同步位置

- 合并现有 `initMapSelect()` 与 `updatePlayerButtons()` 为一个初始化/重建函数：一次清空并生成摘要、地图 optgroup、角色 optgroup；初始化顺序仍在 `loadSelection()`/`loadToggles()` 之后、首次 `selectMap()`/`updateGameData()` 之前。
- change handler 按 value 前缀分派：`map:` 调 `selectMap(mapId)`，`player:` 调 `selectPlayer(id || null)`；处理完成后由统一 `syncSelectionSelect()` 更新摘要并重新选中摘要 option。
- `selectMap()` 现有同步地图 select value 的代码应改为调用统一同步函数；`selectPlayer()` 中 `.player-btn` active 更新改为同一函数。`switchEgoViewpoint()`（Ego 画布切换角色）也必须调用该同步函数，否则摘要会滞后。
- 动态队友刷新发生在 `updateGameData()` 收到 `data.teammates` 后；重建角色 optgroup 时保留当前有效 ID，已离队 ID 调 `selectPlayer(null)` 回退“自己”，然后同步摘要。地图选项不要因队友刷新而重置。
- `pendingPlayerSelection` 恢复必须在角色 option 生成后执行；若保存 ID 已不在当前列表，直接回退自己并清除无效持久化值。`saveSelection()` 协议无需改变。

### 测试影响

- 现有 `tests/webradar-view.test.cjs` 主要以 VM 解析脚本和局部函数，不提供真实 DOM；应更新任何旧的 `map-select`/`player-select` 边界断言，并增加静态断言：单一 `selection-select`、两个 optgroup、`map:`/`player:` 前缀和摘要同步函数存在，且不再依赖 `.map-btn`/`.player-btn`。
- 动态行为至少覆盖：地图动作保留角色摘要、角色动作保留地图摘要、地图角色点击及 Ego 角色点击同步摘要、队友增删重建不丢地图、无效持久化队友回退自己。当前无 Chromium/Playwright，真实展开/触摸布局仍需浏览器环境验证。

### 补充实施与验证

- “自己 / 队友”按钮组已改为独立角色下拉菜单，与地图菜单使用相同深色样式，默认显示“自己”。
- 队友列表更新为选项列表；只有队友 ID 或数量变化才重建选项，避免数据轮询持续替换展开菜单的节点。
- 下拉 change 沿用 `selectPlayer()`；地图点击选角及 `switchEgoViewpoint()` 同步下拉选中值，保留选择记忆和跟随逻辑。
- 未采纳调查中的自动回退并清理已选 ID 建议：地图/Ego 也允许选择非队友，自动回退会改变既有选角行为；此类选择继续不选中任何队友选项。
- `node --test tests/webradar-view.test.cjs` 13/13 通过，`git diff --check` 通过。未进行真实浏览器视觉与点击验证。

## 2026-10-30 左侧角色点大小滑动条调查

### 现状与约束

- 主地图角色点由 `.entity-body`（当前 8x8px）渲染，血量环由 `.entity-health-ring`（当前 14x14px）渲染；死亡角色通过 `.entity-body.dead` 覆盖为 10x10px。方向杆 `.entity-direction` 是独立的 1.5x12px 元素，物资 `.item-body` 是独立的 10x10px 样式。
- 地图整体已有 `#map` 的 `translate/scale`，新滑动条应只改变角色点和血环的 CSS 尺寸，不能改变地图缩放、物资尺寸、方向杆长度或拖拽/双指缩放逻辑。
- 全局 `*` 设置了 `touch-action: none`；原生 range 必须显式设置 `touch-action: auto`，否则移动端拖动可能被地图手势规则拦截。

### 最小实现建议

- 在 `#map` 上定义 CSS 变量，例如 `--entity-marker-scale: 1`；将 `.entity-body` 的 width/height/font-size 改为 `calc(8px * var(--entity-marker-scale, 1))`，`.entity-body.dead` 改为对应的 `calc(10px * ...)`，`.entity-health-ring` 的 width/height 改为 `calc(14px * ...)`。这样 `.item-body`、`.entity-direction`、装备条和地图变换保持不变；若要求环宽也严格同比，可将 mask 的 1px 厚度另设为 `calc(1px * var(--entity-marker-scale, 1))`。
- 页面左侧增加独立的固定覆盖控件（例如 `#marker-size-control` + `input type="range"`），建议放在 `#map-container` 外，避免事件冒泡进入地图 pointer handlers；设置较高 z-index、`pointer-events: auto`、`touch-action: auto`，垂直显示可用 `writing-mode: vertical-lr; direction: rtl`。不要把滑动条放进 `#map`，否则会继承地图 transform 并随地图移动/缩放。
- range 的 `input` 事件只执行 `map.style.setProperty('--entity-marker-scale', value)`；推荐范围约 `0.5..3`、步长 `0.1`、默认 `1`，可选用 `localStorage` 保存。无需重建实体 DOM，现有标记会立即按新尺寸重排。
- 角色标记的文本、死亡圆点和血环均随变量变大；方向杆不变。若不希望 AI/敌人文字随点放大，保持 `.entity-info` 独立不改；角色点内的 `A`/队伍号字体则随 `.entity-body` font-size 同步。

### 验证影响

- 静态测试应断言变量只出现在 `.entity-body`、`.entity-body.dead`、`.entity-health-ring`（以及可选 mask 厚度），不出现在 `.item-body`、`.entity-direction` 或 `#map` 的 transform 逻辑；继续执行 `node --test tests/webradar-view.test.cjs` 和 `git diff --check`。
- 浏览器验证需检查滑块触摸拖动不会平移地图，鼠标/触屏地图拖拽和双指缩放仍工作，角色点与血环按相同比例变化，物资与方向杆尺寸保持不变，以及移动端左侧控件不被顶部栏或地图遮挡。当前环境无浏览器自动化依赖，未进行视觉验证。

### 合并实施与验证

- 根据用户最终确认，顶部仅保留一个 `selection-select`，用“地图”和“角色”两个 optgroup 收纳六张地图及自己/队友。
- 使用 `map:` / `player:` 值前缀分派至原有切换函数；收起时显示当前地图与角色摘要（例如 `DB · 自己`），地图与角色各自的状态和持久化保持独立。
- 地图切换、角色选择、Ego 视角切换和队友更新均同步摘要；非队友选角显示“已选角色”，不改变原跟随目标。
- 删除独立角色菜单及绑定；队友未变化时继续保留选项节点。
- 验证：`node --test tests/webradar-view.test.cjs` 13/13 通过，`git diff --check` 通过。尚未进行真实浏览器交互验证。

## 2026-10-30 地图全屏显示调查

### 现状

- 当前 `#top-controls` 是固定顶部灰色菜单（约第 10 行），`#map-container` 固定为 `top: 60px`、`bottom: 0`（约第 13 行），因此页面总会保留顶部菜单空间。
- 地图拖拽、缩放和校准 pointer handlers 全部绑定在 `#map-container`；改为 fullscreen 不需要改这些事件。现有 `window.resize` 会在全屏尺寸变化后调用 `recenterView()`，Ego 视角另有自己的 canvas/minimap resize 处理。
- 当前没有 Fullscreen API、全屏按钮或退出按钮。Ego 的“退出”仅退出 `#ego-view`，不能替代全屏退出；浏览器 Esc 可退出 Fullscreen API。

### 最小实现建议

- 在顶部菜单增加“全屏地图”按钮，点击优先执行 `document.getElementById('map-container').requestFullscreen()`（检查方法存在并捕获 Promise rejection）；全屏元素应是 `#map-container`，这样顶部灰色菜单及其余页面控件自然不进入全屏，仅显示地图内容。
- 增加 `#map-container:fullscreen { top: 0; left: 0; right: 0; bottom: 0; }`，必要时补 `:-webkit-full-screen` 兼容规则。不要把 `body` 设全屏，否则顶部菜单仍需额外隐藏且会扩大改动面。
- 在 `#map-container` 内放置一个默认隐藏的“退出全屏”按钮，`:fullscreen` 时显示；点击调用 `document.exitFullscreen()`。浏览器原生 Esc 退出后监听 `fullscreenchange` 同步按钮显示状态。全屏按钮应停止冒泡/避免触发地图拖拽，且设置 `touch-action: auto`。
- API 不支持或被权限策略拒绝时，页面内回退可暂时给 `body` 加 `.map-only`：隐藏 `#top-controls`、质量筛选、marker-size 控件和 bottom controls，并将 `#map-container` 的 `top` 改为 0；退出时移除 class 恢复布局。若目标浏览器均支持 Fullscreen API，可只报告不支持而不增加回退状态。

### 交互约束与验证

- Fullscreen 进入/退出会触发 resize；验证地图仍可拖拽、滚轮缩放、双指缩放和校准取点，且跟随模式中心不异常跳动。全屏时 `getBoundingClientRect()` 的 top/left 变化会被现有 `screenToMapPoint()` 和 pointer 代码自动使用。
- Ego 视角是 `#ego-view` 的 sibling（z-index 300），而 Fullscreen 只渲染 `#map-container` 子树；进入全屏后若直接双击角色进入 Ego，Ego overlay 可能不可见。建议进入 Ego 前先退出地图全屏，或在 `enterEgoMode()` 检测 `document.fullscreenElement` 后先 `exitFullscreen()` 再打开 Ego。
- 验证 `node --test tests/webradar-view.test.cjs`、`git diff --check`；补充静态断言 `requestFullscreen`、`:fullscreen`、`exitFullscreen` 和 `fullscreenchange`。真实浏览器需检查桌面/移动端全屏权限、Esc/退出按钮、API 拒绝回退及 Ego 视角切换；当前环境未安装浏览器自动化依赖。

### 滑动条实施与验证

- 左侧中部新增竖向“角色大小”滑动条，范围 4–24 px，步长 1 px，默认 8 px；数值表示地图缩放前的普通角色圆点直径。
- 通过 `#map` 的 `--entity-marker-scale` 控制圆点和血量环的同倍 transform 缩放；血量环基准外径 14px、厚度 1px，圆点基准 8px。圆点边框、内部文字及死亡角色圆环一起缩放；物资点与方向杆保持原尺寸。
- 角色说明文字和装备色条的间距随血量环半径变化，避免放大后重叠；物资文字原样式保留。
- input 即时更新 CSS 变量，不重建角色 DOM；change 保存 `entityMarkerSize`，初始化恢复并限制数值范围。
- 控件放在地图容器外，拖动不会进入地图指针事件；竖向 range 使用 writing-mode 和 direction，使上端为大值。保留 touch-action:none，避免触屏拖动被页面手势取消（与调查建议的 auto 不同）。
- 验证：现有前端测试 13/13 通过；Node VM 实际运行大小更新函数，4/8/16/24 px、上下限和无效值回退均通过；`git diff --check` 通过。
- 未进行真实浏览器视觉、竖向滑动和移动端触摸验证。

## 2026-10-30 提交与推送

- 用户要求启动 hk-tunnel 并提交、推送本轮页面修改。
- 已执行 `hk-tunnel start`，状态为运行中，SOCKS 代理 `127.0.0.1:1080`。
- 提交范围为合并地图/角色菜单、左侧角色大小滑动条、现有测试截取边界调整及本文档；提交前 `git diff --check` 通过，前述测试结果有效。
- 推送目标为 `origin/master`，使用命令级 `http.proxy=socks5h://127.0.0.1:1080`，不修改全局 Git 配置。
- 推送结果：HTTPS 失败，`could not read Username for 'https://github.com'`。当前无 Git credential.helper / gh；经隧道尝试现有默认 SSH 认证也返回 `Permission denied (publickey)`，且未运行 SSH agent。需要可用的 GitHub 认证后重试推送。

## 2026-10-30 方向杆同步缩放

- 用户追加要求视角柱（角色方向杆）也随角色点同比变大，此要求取代上文方向杆保持尺寸的决定。
- `.entity-direction` 宽度、高度改为基准 1.5px、12px 乘以现有 `--entity-marker-scale`，共用左侧滑动条和持久化设置；朝向旋转与底部中心锚点保持不变。
- 例如圆点从 8px 调到 16px 时，方向杆从 1.5×12px 变为 3×24px，血量环外径同时从 14px 变为 28px。
- 验证：前端测试 13/13 通过，`git diff --check` 通过。尚未进行浏览器视觉验证。用户随后要求提交一次；本次将方向杆样式与工作记录一起提交。

### 地图全屏实施与验证

- 右下角增加“地图全屏”按钮；进入后地图容器 top 改为 0，隐藏顶部灰色菜单区域、底部控件、大小滑动条、资源筛选和校准面板，只保留地图与右上角小型退出按钮。
- 最终采用 documentElement.requestFullscreen() 而非调查建议的 map-container.requestFullscreen()：全屏根节点包含现有 Ego sibling，保证双击角色仍可进入和退出视角模式。
- 不支持或拒绝 Fullscreen API 时保留页面内铺满；退出按钮和 Esc 均可恢复布局，监听 fullscreenchange 处理浏览器主动退出。进入时关闭校准模式，退出按钮位于地图容器外，不触发地图拖动。
- 沿用 resize 跟随居中逻辑，模式切换时也同步居中；不改变已选地图/角色和大小设置。
- 验证：现有前端测试 13/13 通过；Node VM 实际运行全屏函数，以模拟 API 覆盖成功、不支持、拒绝三种进入/退出流程，全部通过；git diff --check 通过。
- 未进行真实浏览器全屏、布局和移动端触摸验证。本轮修改尚未提交。

### 全屏保留纯净模式与资源筛选

- 用户追加要求全屏也可操作纯净模式和资源筛选。
- 调整全屏隐藏规则：保留底部“资源”“纯净模式”按钮及右侧资源等级筛选，只隐藏底部“地图全屏”入口和“校准”按钮；顶部灰色菜单继续隐藏。
- 复用原有控件、事件和状态，纯净模式下资源控件禁用的原有逻辑保持一致；关闭纯净模式后可继续筛选资源。
- 验证：前端测试 13/13 通过，git diff --check 通过；未进行真实浏览器视觉验证。本轮修改尚未提交。

### 全屏保留角色大小滑动条

- 根据用户追加要求，移除全屏模式对 `#marker-size-control` 的隐藏规则，左侧角色大小条在普通和全屏模式下共用同一控件。
- 继续实时同比调整角色点、血量环和方向杆，并保留现有大小设置。
- 验证：检查差异仅删除一条 CSS 隐藏选择器，git diff --check 通过；未进行浏览器视觉验证。本轮修改尚未提交。
