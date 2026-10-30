# 公网演示部署：Mac mini + Cloudflare Tunnel

```
访客 → Dify 官方云（两个应用）→ https://urm.wyphub.com → Cloudflare Tunnel → Mac mini
                                                              ├─ memory-service（容器）
                                                              └─ milvus（容器，只在内部网络）
```

| 文件 | 作用 |
|---|---|
| `docker-compose.yaml` | 三个容器：Milvus（内嵌 etcd）、记忆服务、cloudflared |
| `.env`（不提交） | 记忆服务配置：在 `memory-service/.env.example` 基础上，`API_KEY` 用公网专用的一串随机字符 |
| `cloudflared/config.yml`（不提交） | `tunnel: <ID>`、`credentials-file: /etc/cloudflared/<ID>.json`、`urm.wyphub.com → http://memory-service:8000` |
| `cloudflared/<ID>.json`（不提交） | Tunnel 凭证：在已登录 Cloudflare 的机器上 `cloudflared tunnel create urm` 生成，再 `cloudflared tunnel route dns urm urm.wyphub.com` |

为什么用 Mac mini 而不是 VPS：VPS 只剩 1.8 GB 可用内存，Milvus 单机要 2 GB 以上；Mac mini 16 GB，代码不用改。
为什么用 Tunnel：家里没有公网 IP，Tunnel 由本机主动连 Cloudflare，不用开路由器端口，只暴露记忆服务一个服务。

开机自启：Docker Desktop 设为登录时启动，容器都是 `restart: unless-stopped`。
