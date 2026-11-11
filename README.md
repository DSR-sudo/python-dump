# python-dump

DMA 明文 UDP 数据通道 + WebRadar 网页雷达。

- **主力实现：C++17**（`cpp/`），运行时不依赖 Python。
- **旧 Python 实现**保留在 `python/`，仅作对照与回溯，不再是运行路径。
- `web/`、`image/` 是两种实现共享的网页资源与地图底图。

## 目录结构

```
python-dump/
├── cpp/                 # C++17 实现（active）
│   ├── src/             # dma_core、web_radar、协议、JSON、HTTP、目录数据
│   ├── tests/           # C++ 与 Python 的逐字段 parity 测试
│   ├── tools/           # 物品目录一次性生成脚本（仅开发期）
│   ├── build.sh         # 一键构建 -> cpp/build/dma_webradar
│   └── README.md        # 构建、运行、接口、环境变量
├── python/              # 旧 Python 实现（legacy / reference）
│   ├── main.py          # 旧入口
│   ├── dma_*.py         # 核心、API、控制台命令
│   ├── web_*.py         # 网页雷达 / Actor 分类服务
│   ├── ue_*.py          # UE 反射/SDK 扫描工具
│   └── README.md
├── web/                 # 共享网页资源：webpage.html、actor_kinds.html、config.txt
├── image/               # 共享地图底图
├── deploy/              # OpenWrt procd 启动脚本
├── tests/               # 前端网页 JS 测试
└── docs/                # 文档、工作日志、历史参考
    ├── legacy-python.md # 旧 Python 实现说明（UDP/控制台）
    ├── worklogs/        # 历史 WORKLOG
    └── reference/       # 早期 C++ 参考资料
```

## 快速开始（C++）

```sh
./cpp/build.sh
./cpp/build/dma_webradar --webradar          # 启动网页雷达（默认 34900）
# 访问 http://<host>:34900/，口令见 web/pwd.txt
```

仅接收 UDP（OpenWrt / procd）：

```sh
./cpp/build/dma_webradar --receiver-only --root=/root/python-dump
```

详细参数、控制台命令、HTTP 接口与环境变量见 **[cpp/README.md](cpp/README.md)**。

## 旧 Python 实现

```sh
python3 python/main.py                       # 交互控制台
python3 -u python/main.py --receiver-only    # 仅接收
```

资源根目录会自动解析到仓库根（`web/`、`image/` 所在目录），也可平铺部署为
`/root/python-dump/main.py`。协议与命令说明见 **[docs/legacy-python.md](docs/legacy-python.md)**。

## 验证

```sh
python3 cpp/tests/parity_test.py "$PWD" cpp/build/dma_webradar
```

同一批合成 UDP 帧同时驱动 C++ 与 Python 实现，逐字段比对 `/api/data`、
`/api/rwvg`、订阅过滤、鉴权、图片缓存、畸形报文及原始 DMA 请求链路。

更多文档见 **[docs/README.md](docs/README.md)**。
