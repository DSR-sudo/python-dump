# 部署

## OpenWrt / iStoreOS：后台接收 + WebRadar

适用于 aarch64 设备（如 Raspberry Pi 4B）。路由器上没有编译工具，二进制需在
开发机交叉编译成**全静态**再传过去；OpenWrt 使用 musl，动态 glibc 产物无法运行。

### 1. 在开发机构建 aarch64 静态版

需要 `aarch64-linux-gnu-g++`、CMake（Ubuntu：`sudo apt install g++-aarch64-linux-gnu cmake ninja-build`）。

```sh
./cpp/build-aarch64.sh
file cpp/build-aarch64/dma_webradar
# 期望：ELF 64-bit LSB executable, ARM aarch64, ... statically linked
```

### 2. 部署目录

程序按 `--root` 指定的目录查找 `web/`、`image/`，并在其中写入 `logs/`。
推荐固定使用 `/root/python-dump`（init 脚本按此路径编写）：

```
/root/python-dump/
├── cpp/build/dma_webradar
├── web/            # webpage.html、config.txt（pwd.txt 启动时自动生成）
├── image/          # 地图底图，按需拷贝
└── logs/           # 运行时自动创建
```

传输：

```sh
ssh root@<router> 'mkdir -p /root/python-dump/cpp/build /root/python-dump/web /root/python-dump/image'

scp cpp/build-aarch64/dma_webradar root@<router>:/root/python-dump/cpp/build/
scp web/webpage.html web/config.txt root@<router>:/root/python-dump/web/
scp image/ht.png image/jy.png image/az3.png root@<router>:/root/python-dump/image/
# 其余地图按需：bks.png cg.png db.png az3_in.png jy_1f.png ... jy_4f.png
```

注意：

- `image/` 全量约 86 MB，路由器 overlay 空间有限时只传当前用的地图。
- `web/pwd.txt` 每次启动重新生成，不需要传；`web/` 和 `logs/` 必须可写。
- 地图也可以放 U 盘，用 `DMA_WEBRADAR_IMAGE_DIR=/mnt/.../image` 指定。
- 换到非 `/root/python-dump` 目录时，必须加 `--root=`；同时注意程序会优先使用
  已存在的 `/root/python-dump/image`，迁移时把旧目录删掉或显式设置
  `DMA_WEBRADAR_IMAGE_DIR`。

### 3. procd 启动脚本

```sh
scp deploy/udp-receiver.init root@<router>:/etc/init.d/udp-receiver
ssh root@<router> 'chmod +x /etc/init.d/udp-receiver && \
    /etc/init.d/udp-receiver enable && /etc/init.d/udp-receiver start'
```

脚本默认执行：

```sh
/root/python-dump/cpp/build/dma_webradar \
    --headless --webradar --root=/root/python-dump
```

- `--headless`：不进入交互控制台，收到 SIGTERM 正常退出（procd 下必需）。
- `--webradar`：同时启动 HTTP 服务，默认 `0.0.0.0:34900`，端口可由
  `web/config.txt` 第一行覆盖。
- 只需要 UDP 接收、不提供网页时，把命令改成
  `--receiver-only --root=/root/python-dump`。

如需继续使用旧 Python 实现，把 command 改为：

```sh
/usr/bin/python3 -u /root/python-dump/python/main.py --receiver-only
```

### 4. 验证

```sh
ssh root@<router> 'logread -f'          # 启动日志
cat /root/python-dump/web/pwd.txt       # 6 位网页口令（或开发口令 963007）
curl -I http://<router>:34900/          # 200
curl -o /dev/null -w '%{http_code} %{size_download}\n' \
     'http://<router>:34900/image/ht.png?token=963007'   # 200 8762878
```

浏览器访问 `http://<router>:34900/`，输入口令即可。

UDP 监听端口与地址通过 `DMA_BIND_PORT`、`DMA_UDP_LISTEN_HOST` 配置，默认
`0.0.0.0:53786`。
