# dma_webradar（C++ 改写版）

`dma_core.py` 与 `web_radar.py` 的 C++17 实现。运行时**不再依赖 Python**，
原 Python 实现位于仓库 [`python/`](../python/)，仅作对照。协议、报文布局、
JSON 字段、鉴权与缓存语义均按原实现逐项移植；`cpp/tests/parity_test.py` 会用
同一批合成 UDP 帧同时驱动两边并逐字段比对（当前 41/41 通过）。

共享资源（`web/`、`image/`）位于仓库根目录，C++ 会自动探测资源根；也可用
`--root` 或 `DMA_WEBRADAR_ROOT` 指定。

## 目录

| 文件 | 对应 Python（在 `python/` 下） |
| --- | --- |
| `src/dma_core.*` | `dma_core.py` |
| `src/web_radar.*` | `web_radar.py` |
| `src/protocol.*` | `dma_protocol.py`、`rwvg_protocol.py`、`rwvg_actor_snapshot.py` |
| `src/radar_snapshot.*` | `actor_snapshot_radar.py` |
| `src/item_descriptor.*` | `item_descriptor.py` |
| `src/catalog.*` + `src/catalog_data.inc` | `item_catalog.py`、`id_catalog.py`（数据表） |
| `src/http_server.*` | `http.server.ThreadingHTTPServer` / `BaseHTTPRequestHandler` |
| `src/json.*` | `json.dumps/json.loads`（保留插入顺序、UTF-8 原样输出） |
| `src/session_log.*` | `session_log.py` |
| `src/main.cpp` | `main.py` 的控制台入口（核心子集） |

## 构建

```sh
cd cpp
./build.sh            # 产物 cpp/build/dma_webradar
# 或
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```

无第三方依赖，只需 C++17 编译器与 pthread。

## 运行

```sh
# 交互控制台（默认不启动网页雷达）
./build/dma_webradar

# 启动时自动开网页雷达，默认端口 34900
./build/dma_webradar --webradar
./build/dma_webradar --webradar=34900

# 仅 UDP 接收（OpenWrt / procd 场景）
./build/dma_webradar --receiver-only

# 指定资源根目录（包含 web/、image/、logs/）
./build/dma_webradar --webradar --root=/root/python-dump
```

资源根目录解析顺序：`--root` > `DMA_WEBRADAR_ROOT` > 可执行文件目录及其两级父
目录（存在 `web/` 时）> 当前目录（存在 `web/` 时）> 可执行文件目录。

控制台命令：

```
status                 对端/主程序/快照状态
watch                  每 2 秒刷新，Ctrl-C 返回
webradar start [PORT]  启动网页雷达
webradar stop|status   停止/查看
aim <cmd>              向 client 发 AIM 热命令
stream_stats           输出 RWVG 计数器
cr3 <pid>              请求 CR3（UserDTB/KernelDTB/Base）
cpueaxh_ping           发送 PingPong 命令并等待 ACK
start_data_threads / stop_data_threads
help | exit
```

## HTTP 接口（与 Python 版一致）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | `web/webpage.html` |
| GET | `/api/data` | 雷达负载；支持 `X-Auth-Token`、`X-Client-Id`、`X-Data-Token`（304→204）、`nocache` |
| GET | `/api/aim-state` | `{"dev": bool}` |
| GET | `/api/rwvg` | 协议描述、统计、快照、日志 |
| GET | `/api/logs` | 仅日志部分 |
| GET | `/image/...` | 地图图片，带 ETag / 304 |
| POST | `/api/subscription` | 每客户端过滤订阅（clean/resources/deadbox/qualities/ego） |
| POST | `/api/map` | 底图联动 AIM `map <model>` |
| POST | `/api/aim` | 开发口令专属热命令白名单 |

鉴权口令：`web/pwd.txt` 中的随机 6 位口令，或开发口令 `963007`。启动时生成并写入
`web/pwd.txt`；端口读取 `web/config.txt` 第一行。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DMA_UDP_LISTEN_HOST` | `0.0.0.0` | 含 `:` 时使用 IPv6 |
| `DMA_BIND_PORT` / `DMA_TARGET_PORT` | `53786` | UDP 监听端口 |
| `DMA_VERBOSE_EXPECTING` | `0` | 打印请求字节数/超时预算 |
| `DMA_WEBRADAR_PLAYER_TTL` | `1.5` | 旧版玩家/物资帧 TTL（秒） |
| `DMA_ACTOR_ITEMS_TTL_MS` | `6000` | players-only 帧回放世界物体的时长 |
| `DMA_WEBRADAR_IMAGE_DIR` | 自动探测 | 地图图片目录 |
| `DMA_WEBRADAR_ROOT` | 自动探测 | 资源根目录 |
| `RWBASE_DECRYPT_LOG` | `1` | 解密诊断日志开关 |
| `RWBASE_DECRYPT_FALLBACK_PATH` | `/var/log/rwbase/decrypt_fallback.log` | 落盘路径 |

## 数据表再生成

`src/catalog_data.inc` 由 Python 数据表一次性生成（仅开发期使用，运行时不调用
Python）：

```sh
python3 cpp/tools/gen_catalog.py "$PWD"   # 在 python-dump 根目录执行
```

## 验证

```sh
# 与 Python 实现逐字段对比（需要 Python 3，仅测试用）
python3 cpp/tests/parity_test.py "$PWD" cpp/build/dma_webradar

# ASan/UBSan
cmake -S cpp -B /tmp/build-asan -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_CXX_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer -g"
cmake --build /tmp/build-asan -j
ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 \
  python3 cpp/tests/parity_test.py "$PWD" /tmp/build-asan/dma_webradar
```

覆盖：空负载、旧版 utils/player/item/batch 回退路径、Type=6 v10 双分片完整快照、
players-only 世界物体回放、四类订阅过滤、`/api/rwvg` 与 `/api/logs`、鉴权/开发
会话/404、图片 ETag/304、`X-Data-Token` 204、HTTP keep-alive、畸形报文、控制台
`cr3`/PingPong 原始请求链路。

## 未移植

- `web_actor_kinds.py`（34901 分类监控页）不在本次范围。
- `dma_command.py` 的 attach/dump/regions 等控制台业务命令未移植；C++ 入口只保留
  WebRadar 与驱动通信所需的核心命令。
