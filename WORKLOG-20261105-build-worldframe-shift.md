# 2026-11-05 新版本坐标整体偏移（WorldShift）调查与绝对帧改造

## 现象
三角洲 11-05 新版本，qemu-proxy-client 迁移后数据正常，但玩家/AI/物资相对旧版地图整体偏移
（db 图上约 47 m 西、89 m 北）。旧 webpage.html 校准直接套新坐标会整体错位。

## 根因（已用内存实测证实）
1. 发布坐标来自 `CTW = 绝对坐标(AActor+0x38C) − WorldShift`。
   - 实测：关卡里 `0x38C == 0` 的 actor，其 ComponentToWorld.Translation 精确等于 `−WorldShift`。
2. `WorldShift` 在 `UWorld+0x640/0x644/0x648`，**类型是 int32（不是 float）**。
   本局（Dam_Iris_Level1 = db.png）实测 `shift = (362269, −778760, −16499)`，即约 7.8 km 量级
   ——这正是迁移报告里"物资距离 8000 m 异常"的来源：对已经是 CTW 帧的坐标再减一次 shift。
   - 之前把它按 float 读会得到 0.01 / NaN / NaN，从而误判为"死字段"。
3. 这次 build 更新把每张图的世界原点挪了，所以新旧发布帧之间差一个固定平移 S。
   - db：S ≈ (−4739, +8933) cm，由"站在校准点读 CTW"与旧校准值两条独立途径互证。
   - 同一张图跨局不变（用户实测：上局校准本局仍可用）；不同地图各不同。
4. 报告中的相机链实测**正常**，不是坏的：
   - `PCM = PC+0x408`；`PCM+0x370`(PCOwner) == PC；`PCM+0x30D00`(ViewTarget.Target) == 本地 Pawn；
     `PCM+0x30D10` POV=(−42338, 13926, 33.5)、rot=(−6.9,−118.1,0)、FOV=90.1，距 Pawn 3.5 cm。
   - 之前"扫不到 POV"是探针按近距离过滤 + 玩家持续移动造成的假阴性。
5. `kSceneComponentOwnerActor`(0x100) 客户端从未使用，无需修改。

## 改造：客户端输出改为绝对世界帧（已撤回，见文末"最终处理"）
- `GameCoordinateMath.cpp` 新增 `pmu_add_world_shift()`（CTW + shift，位精确、溢出/NaN 安全）。
- **只在序列化出口平移**（`GameSnapshotExport.cpp` 的 JSON 与二进制 v10 记录）：
  - 扫描器内部 `pmu_actor.position` 仍保持 CTW 相对帧，因为相机 POV 也在该帧，
    瞄准引擎的 `world_to_screen` 不能混帧。
  - `shift` 读不到时回退发布原始 CTW bits，不会输出垃圾。
- 收益：绝对帧 = 游戏自己的 0x38C 坐标系，不随渲染原点（WorldShift）变化，
  正常情况下游戏更新后无需重新校准。
- db.png 校准已按本局 shift 平移（pixel 不变）：
  - point3D_1 (330529, −800526) ↔ (836.77, 495.92)
  - point3D_2 (392982, −732150) ↔ (2429.74, 2241.80)
  - 远端备份：`webpage.html.bak-20261105-pre-dbcalib`、`webpage.html.bak-20261105-pre-absframe`
- 客户端二进制：`qemu-proxy-client/build/x64/main` → nika `/root/vm/bin/main`
  （旧版备份 `/root/vm/bin/main.bak-20261105`，md5 0a64537a...）。
- 其余地图（cg/bks/ht/az3/jy）校准仍是旧帧，进图后各重校一次即可（绝对帧下以后不再变）。

## 最终处理（2026-11-05）：撤回绝对帧，只改 db 校准
- 实机确认：**只有 db（Dam_Iris_Level1）的世界原点变了**；bks 等其它图的发布帧与旧版一致，
  旧校准继续可用。绝对帧长期更稳，但要求每张图把校准点整体平移一次；现在只需改 db 一张，
  成本远低于给所有图重做校准，因此撤回。
- 客户端：`git revert 6be836e`（提交 `8d5d4f8`），恢复发布 CTW 相对帧。
  重新构建的 `build/x64/main` md5 `c4a6f5f07c4481f1582cb0904ff52ecf`
  与撤改前的 `/root/vm/bin/main.bak-20261105` 完全一致（构建可复现）。
- nika：`/root/vm/bin/main` 换回该二进制（绝对帧版备份 `/root/vm/bin/main.abs-20261105`），
  扫描进程重启，pidfile `/run/pmu-main.pid`。
- 10.1 `web/webpage.html` 恢复 CTW 校准：
  - db 用用户新测值 `(-31740,-21766) ↔ (836.77,495.92)`、`(30713,46610) ↔ (2429.74,2241.80)`；
  - az3（AZ-5_Level1）恢复旧值 `(29596,30011)/(-37994,-42155)`；cg/bks/ht/jy 未动。
  - 远端备份 `webpage.html.bak-20261105-pre-revert`。
- 结论：网页端继续按图校准；以后哪张图再出现整体偏移，才单独改那张图的点。

## 遗留
- `kDeadBodyIsAI = 0x2A12` 仍为外推值，未实测验证。
- 若发现同一张图不同局的 shift 会变，则绝对帧改造已经把这个问题也消掉了（CTW 帧会跳、绝对帧不会）。
- 绝对帧跨版本稳定性尚未经过下一次游戏更新验证（理论上稳定）。
