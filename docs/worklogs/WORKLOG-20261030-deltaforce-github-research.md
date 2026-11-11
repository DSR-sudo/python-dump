# DeltaForce GitHub 开源项目调查

## 目标

- 按用户要求通过 hk-tunnel 搜索 GitHub 上的 DeltaForce 开源项目。
- 结合此前关于 2D 地图展示 3D 高度的讨论，关注地图、渲染、可视化相关实现；区分源码公开与有明确开源许可证。

## 环境与决定

- hk-tunnel 已运行，代理为 socks5h://127.0.0.1:1080。
- 使用 GitHub 公共搜索 API 和项目仓库公开文件，记录实际结果；不运行第三方项目。
- 原页面平滑延迟调整仍保留在工作区，本次搜索不修改页面代码。

## 发现、验证与剩余问题

### GitHub 公共项目核查（2026-10-30）

以下结果均通过 `socks5h://127.0.0.1:1080` 访问 GitHub API/raw 页面得到；未克隆、未运行第三方代码。

#### 高相关：2D/3D 地图与高度表现

- [QH190311/delta-force-3d-map](https://github.com/QH190311/delta-force-3d-map)：仓库描述为“把三角洲行动导出的 PhysX 场景数据还原成可交互的 3D 地图”，并支持实时喂入坐标标注玩家/物资。README 明确写出浏览器内解析 `TIMEPX01`、瓦片拼接地形、自由相机、坐标为厘米且 `Z` 轴向上；目录有 `web/index.html`、本地 three.js 和 `Physx/` 数据，属于可读源码实现。GitHub license 字段为 `NOASSERTION`，README 仅写“仅供学习交流”，没有标准开源许可证，不能按开源许可复用。
- [MapleEve/DF3dMapTool](https://github.com/MapleEve/DF3dMapTool)：TypeScript/React/three.js 开源代码结构，README 明确包括 3D 场景、楼层、POI，以及“2D 俯视覆盖层”；2D 与 3D 共享世界坐标，支持 2D 标记传送到 3D、跨层同步和 HUD 小地图。源码目录证据包括 `apps/web/src/engine`、`map`、`bigmap`、`packages/dmap`。GitHub license 字段为 `NOASSERTION`；README 末尾为 Copyright/“未经许可，禁止复制、再分发”，故实际不是可确认的 OSI 开源许可，且地图资产为加密 DMAP，适合参考架构，不宜直接复制资产。
- [Minecraforever/delta-force-tactical-map](https://github.com/Minecraforever/delta-force-tactical-map)：README 称“指挥官三维战术板”，在线版提供低精度 3D 预览、地形和事件数据；有 `LICENSE`，仓库元数据显示 `NOASSERTION`，README 说明网页代码和启动脚本采用 MIT，但游戏模型、贴图和地图数据不在该许可范围内。适合参考 3D 地形/战术板分层，需注意素材许可边界。

#### 相关但主要是 2D 雷达/战术地图

- [yifengxinma/DeltaForce-WebRadar-REpro](https://github.com/yifengxinma/DeltaForce-WebRadar-REpro)：JavaScript，README 明确为 WebSocket 中继 + 互动地图；源码目录含 `mapEngine.js`（Leaflet）、`canvasRenderer.js`、`mapConfig.js` 和 WebSocket 客户端，证实是源码可读的 2D 地图雷达，不是 3D 高度场景。GitHub license 字段为空/无许可证。
- `yifengxinma` 源码进一步核查：`mapEngine.js` 将 UE 坐标的 X/Y 投影到 Leaflet `CRS.Simple`；`canvasRenderer.js` 保留每个实体的 `z`，计算 `dz` 并以米显示高度差标签，同时用 z-index 做图标绘制层级（AI/撤离/容器/物品/队友/敌方等）。因此它有真实 Z 高度数据和 2D 高度提示，但没有 three.js/WebGL/3D 相机或按高度抬升的 3D 几何渲染。
- [coolxi-tech/deltaforce_map](https://github.com/coolxi-tech/deltaforce_map)：Vue 3 + Leaflet + WebSocket 的 2D 实时地图；README 列出多地图、玩家高度位置指示、标记/朝向和缩放平移。仓库有 `LICENSE.txt`，README 声称 CC BY-NC-SA 4.0；GitHub 自动识别为 `NOASSERTION`，应以仓库文本为准并保留非商业限制。配套 [deltaforce_map_api](https://github.com/coolxi-tech/deltaforce_map_api) 是 NestJS/TypeScript 的外挂数据探测 API，不是地图渲染实现，且无许可证。
- `coolxi-tech` 源码核查：`src/components/map.vue` 使用 Leaflet；`storyHeightDiv()` 读取 `player.position.z`，以绿/红箭头和数值显示高低差，坐标投影仍只使用 X/Y 的 Leaflet `LatLng`。无 three.js/WebGL 3D 场景证据，属于 2D 地图 + Z 高度 HUD。
- [Deng0430/delta-force-tactical-map](https://github.com/Deng0430/delta-force-tactical-map)：MIT，TypeScript/Vite 的全面战场 2D 地图标注、兵棋推演、路线和战术编辑器；源码树有 `src/`、`public/`、Android/Electron 工程。README 未显示 3D 场景或高度渲染证据，不能作为 3D 地图参考。

#### 排除项

- [WintryWind7/DeltaForce-MapHelper](https://github.com/WintryWind7/DeltaForce-MapHelper) 虽有 Apache-2.0 文件，但仓库仅有极短 README，未发现地图源码或 2D/3D 证据。
- [deserterTSS/DeltaForceMapTool](https://github.com/deserterTSS/DeltaForceMapTool) 仅有 README 和一个 `三角洲地图制作.zip`，属于不可核验的压缩二进制/无源码项目，排除。

#### 结论

- 若目标是研究“2D 地图展示 3D 高度/楼层”：优先看 `QH190311/delta-force-3d-map` 的坐标约定、PhysX 场景还原和相机/高度交互；其次看 `MapleEve/DF3dMapTool` 的 2D↔3D 共享坐标、楼层和投影模块。
- 两个最相关项目都没有可确认的标准开源许可证（后者明确保留权利）；不能把“公开源码”直接等同于“允许复用”。`coolxi-tech` 和 WebRadar 更适合仅参考 2D 标记/实时数据组织，且前者用途明确涉及外挂观测。

## 主查询与辅助项目

- 通过指定 SOCKS 代理成功 GET GitHub Search API：q=DeltaForce，sort=stars，per_page=25；返回 total_count=410（包含同名及非相关项目，不等于410个可用游戏项目）。
- 已通过同一代理读取以下仓库 README：
  - https://github.com/yi-zelin/DeltaForceSS — 三角洲行动特勤处自动制造；268 stars；许可证元数据 GPL-3.0。
  - https://github.com/jiansenc/DeltaForceData — 三角洲行动 DeltaForce 物品资料数据 json；53 stars；许可证元数据 未识别。
  - https://github.com/luoy-oss/deltaforce_id — 三角洲行动相关id映射表，角色、枪械、积分等。；36 stars；许可证元数据 Apache-2.0。
  - https://github.com/xxxsy11/DeltaForceAgent — 三角洲行动智能体(DeltaForceAgent) + 三角洲图数据(DeltaForceData)；34 stars；许可证元数据 MIT。
- luoy-oss/deltaforce_id README 明确提供角色/地图 ID 与图片地址，并说明地图只有整图，具体分层图片未处理。
- DeltaForceAgent 是 GraphRAG/知识助手，其图数据指知识图谱，不是三维场景模型。
- DeltaForceData 是物品 JSON 数据集，不是3D地图渲染器。
- DeltaForceSS 是OCR与模拟鼠标键盘的特勤处制造自动化，不是地图可视化实现。

## QH190311 项目实现核查

- 按用户圈选项目追查，通过 hk-tunnel 读取 README 和 web/index.html；未运行第三方代码。
- parseBin（index.html:332）解析自定义 TIMEPX01 格式中的顶点/三角索引、127×127 高度瓦片、实例变换。TIMEPX01 是该项目读取的数据格式，不等同于浏览器直接加载任意 PhysX 原生文件。
- buildBuildings（:511）对碰撞几何顶点执行缩放、四元数旋转、平移，按空间块合并为 Three.js BufferGeometry；buildTerrain（:609）把高度瓦片变为网格，结合离线校准的朝向和位移与建筑对齐。
- 透视相机在 :456；场景根节点旋转和镜像在 :485–493，组合将游戏厘米/Z向上坐标 (x,y,z) 映射到渲染坐标 (x,z,y)，没有统一除100转米。
- buildEntities / setEntities（:705附近）用 InstancedMesh 绘制不同颜色的球体，每类最多400个；位置为输入(x,y,z+entR)，与建筑地形共用坐标变换。
- GameMap.push（:738）实际只接收 list 并调用 setEntities，整批替换；README 展示的第二个sourceId参数在当前实现中没有处理。源码未见自动数据采集或WebSocket接入，不应把“可实时喂入”描述为已包含实时数据链路。
- README 说明这些是物理碰撞几何、不是完整美术模型；地形缺少摆放信息，依赖离线拟合并可能存在偏移。导出工具、fine_fit.py 和所称坐标精度验证过程未随已核查仓库提供。
- 对 python-dump 的结论：已有角色XYZ可作为标注输入；要获得真实立体建筑/楼层，还需要对应地图的几何数据与坐标对齐，现有PNG和角色高度不能还原建筑模型。
