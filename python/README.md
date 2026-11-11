# 旧 Python 实现（legacy / reference）

当前运行路径是 [`cpp/`](../cpp/README.md) 下的 C++17 实现。本目录保留原 Python
版本，便于对照行为、回溯逻辑；除 `cpp/tests/parity_test.py` 这类开发期对比测试外，
部署时不再需要 Python。

需要 Python 3.10+（`dma_core.py` 使用了 `dict | None` 等 3.10 语法）。

## 运行

```sh
# 在仓库根目录
python3 python/main.py
python3 -u python/main.py --receiver-only
```

## 模块

| 模块 | 作用 | C++ 对应 |
| --- | --- | --- |
| `main.py` | 交互控制台入口 | `cpp/src/main.cpp`（核心命令子集） |
| `dma_core.py` | UDP 接收、RWVG 流、Type=6 快照、雷达状态 | `cpp/src/dma_core.*` |
| `web_radar.py` | 网页雷达 HTTP 服务 | `cpp/src/web_radar.*`、`http_server.*` |
| `web_actor_kinds.py` | 34901 Actor 分类监控服务 | 未移植 |
| `dma_command.py` | 控制台业务命令 | 未移植 |
| `dma_api.py` | 内存读写/CR3/线程控制 API | 未移植（C++ 入口仅含 cr3/ping 等核心请求） |
| `dma_protocol.py`、`rwvg_protocol.py`、`rwvg_actor_snapshot.py` | 协议打包与解析 | `cpp/src/protocol.*` |
| `actor_snapshot_radar.py` | Type=6 记录转雷达负载 | `cpp/src/radar_snapshot.*` |
| `item_descriptor.py`、`item_catalog.py`、`id_catalog.py` | 物品品质/名称、探员与武器表 | `cpp/src/item_descriptor.*`、`catalog.*` |
| `session_log.py` | 控制台会话日志 | `cpp/src/session_log.*` |
| `sdk_helper.py`、`ue_*.py` | UE 反射/SDK 扫描工具链 | 未移植 |
| `paths.py` | 解释 `web/`、`image/` 资源根目录 | C++ 内建根目录探测 |
| `unknown_id_records.json` | 历史未知 ID 记录 | — |

资源目录（`web/`、`image/`）和日志目录（`logs/`）由 `paths.py` 解析到仓库根或
平铺部署目录，见 [docs/legacy-python.md](../docs/legacy-python.md)。
