# 部署

## OpenWrt / iStoreOS：UDP 仅接收服务

`udp-receiver.init` 通过 procd 启动 C++ 接收端：

```sh
/root/python-dump/cpp/build/dma_webradar --receiver-only --root=/root/python-dump
```

安装：

```sh
scp cpp/build/dma_webradar root@<router>:/root/python-dump/cpp/build/
scp deploy/udp-receiver.init root@<router>:/etc/init.d/udp-receiver
ssh root@<router> 'chmod +x /etc/init.d/udp-receiver && \
    /etc/init.d/udp-receiver enable && /etc/init.d/udp-receiver start'
```

部署目录需包含共享资源：

```
/root/python-dump/
├── cpp/build/dma_webradar
├── web/            # webpage.html、config.txt
├── image/          # 地图底图
└── logs/           # 运行时生成
```

`--root` 也可省略：程序会依次探测可执行文件目录、其两级父目录和当前目录中是否
存在 `web/`。端口与监听地址通过 `DMA_BIND_PORT`、`DMA_UDP_LISTEN_HOST` 配置。

如需继续使用旧 Python 实现，把 init 中的 command 改为：

```sh
/usr/bin/python3 -u /root/python-dump/python/main.py --receiver-only
```
