# python-dump 明文 UDP

DMA 通道仅使用明文 UDP。已删除 TCP 监听、流长度前缀、压缩编码和外层流重组。

运行：

```sh
python3 main.py
```

控制台无需等待远端上线即可输入命令。`status` 显示远端地址、最近包/状态时间及
快照计数；`watch` 每两秒刷新，Ctrl+C 返回命令行。等待输入时后台消息暂存，回车
后显示，避免破坏正在输入的命令；EOF/Ctrl+C（非 watch 内）正常退出。

新版 main 使用同一 UDP 源端口每秒发送 LOG `[PMU][STATUS] ...`，启动等待、GPUUUID
和扫描错误也可见；重复状态只更新在线时间，不重复打印/写日志。心跳仅代表 main
进程存活，不代表扫描成功，请同时查看最近快照时间。旧版 main 不发送状态时，
控制台明确显示未收到状态。main 是扫描发送端，不实现控制台的远程内存读写命令。

在 OpenWrt/iStoreOS 上仅接收 UDP、无需交互控制台时，使用
`python3 -u main.py --receiver-only`。该模式收到 SIGTERM 后关闭 UDP socket；
`deploy/udp-receiver.init` 可安装为 procd 服务，以便开机自启并在退出后重启。

默认监听 `0.0.0.0:53786`，可通过 `DMA_UDP_LISTEN_HOST`、`DMA_BIND_PORT` 配置。
未设置 `DMA_BIND_PORT` 时兼容 `DMA_TARGET_PORT`，旧的 `DMA_TCP_LISTEN_HOST` 不再使用。
IPv6 可设置 `DMA_UDP_LISTEN_HOST=::1`。先启动 Python，再启动 C 对端：

```sh
cd ../qemu-proxy-client
make -j4
./build/x64/main log 127.0.0.1 53786 'DRIVER_ONLINE' 3000
```

C 示例创建一个线程发送 LOG，然后最多等待 3 秒接收一个命令或心跳并退出。
它展示传输和解析，不执行读写内存等业务命令。Python 收到有效包后记录源 IP/端口，
通过同一个绑定的 UDP socket 回发命令和心跳，因此对端必须保持发送 socket 打开。
每秒发送一次心跳，连续 3 秒无有效入站包会离线并释放对端；正在等待命令结果时保留
该对端到请求完成或超时，避免其他发送方的数据混入。一个实例同时服务一个对端。

| 方向 | 每个 UDP 数据报的格式 |
| --- | --- |
| C → Python | `1 字节 type + 原始 payload`，type：LOG=1、DATA=2、ONLINE=3、SNAPSHOT=6 |
| Python → C 命令 | 小端 `<IBQQI1024s>`，固定 1049 字节，依次为 Magic、Command、Value、Address、Size、Data |
| Python → C 心跳 | `HELO` 后补 28 个零，固定 32 字节 |

Magic 为 `0xDEADBEEF`。命令号保留：读=1、写=2、CR3=3、区域=4、模块=5、
启动数据线程=13、停止=14、PingPong=15、特征搜索=16。

“明文”表示不加密、不压缩，二进制命令和数据仍使用既有字段布局。LOG 载荷为 UTF-8。
`pack_packet` / `parse_packet_header` 处理 C→Python 包，`pack_*_req` / `send_to_driver`
处理反向命令。单包最多 65507 字节，含 type 的载荷最多 65506 字节，不自动拆包。
现有 RWVG/Actor 快照业务内部的分片字段保留；旧 TCP/codec 封装没有兼容接收路径。

UDP 不保证送达、顺序或去重。原始内存多包响应没有传输序号，丢包通常会超时，乱序或
重复可能导致数据错误；本次没有引入可靠传输协议。跨主机网络及真实内存业务未验证。

验证：

```sh
python3 _run_tests.py
# 包含实际 C ↔ Python 互通验证：
make -C ../qemu-proxy-client test-dma
```

两端修改记录统一保存在 [现有工作文档](../qemu-proxy-client/WORKLOG.md)。
