# SS-14 远程 e2e — 部署记录（2026-10-13）

> 目标：CDS 全栈部署到远程服务器 `100.112.5.x:22022`（openEuler 22.03，中国境内），
> 达到 S1 验收（全服务 healthy + /health 200 + /metrics + 前端可达）。
> 本目录所有文档不写任何密码/令牌（铁律）；密钥在 `/root/cds.env`（600 权限）。

## 1. 环境侦察结论

| 项 | 结果 |
|---|---|
| OS | openEuler 22.03（x86_64），ssh `root@100.112.5.x:22022` 免密 |
| CPU/内存/盘 | 8C / 24GB / 数据盘空闲（部署前已核） |
| 虚拟化 | **KVM guest，无嵌套虚拟化** → 远程不能跑 MicroVM 沙箱节点（决策：C+B 拆分，见 §4） |
| 网络 | GitHub 不可达（compose 插件装不上）；docker.io 直连不稳（偶发 EOF / pull access denied）；aliyun 镜像站可用 |
| Docker | 原无 docker → 安装 29.7.2（与本机同版） |

## 2. Docker 安装（远程）

get-docker.sh 不支持 openEuler → 改 **aliyun 静态 tarball**（docker-29.7.2.tgz）：
1. 解包到 `/usr/local/docker/`（官方 tgz 解出 `docker/` 子目录，二进制需 `cp` 到 `/usr/local/bin/`——首轮踩坑：systemd 单元指向 `/usr/local/bin/dockerd` 而文件在子目录，服务起不来）。
2. sshd 非交互 PATH 不含 `/usr/local/bin` → 建 `/usr/bin/docker{,d}` 等符号链接。
3. 结果：`docker version` server=29.7.2，storage=overlayfs，network create/rm 冒烟通过，`systemctl is-active dockerd` = active。
4. compose v2 插件：GitHub 不可达 → **放弃 compose，脚本化 `docker run` 编排**（`deploy/e2e-remote/remote_deploy.sh`，与 `docker-compose.yml` 根变体 1:1 镜像，含健康等待与幂等重入）。

## 3. 镜像策略（中国境内）

- 基础 3（python:3.12-slim / node:20-alpine / nginx:alpine）+ 中间件 6（postgres:16-alpine / redis:7-alpine / clickhouse-server:24.3-alpine / minio:latest / opa:0.68.0 / vault:1.15）**全部本机拉取成功**（docker.io 直连 + 重试；minio 曾 2 次 pull access denied，重试后到位）。
- ddn 渡渡鸟 fallback 路径确认（来自本机既有拉取记录）：`swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/<原路径>`（quay.io 系为 `ddn-k8s/quay.io/<路径>`）。
- 应用镜像 `cds-api:latest` / `cds-frontend:latest` **本机构建 → `docker save` → scp → 远程 `docker load`**（远程不拉应用镜像、不装 pip 依赖，绕开远程网络不稳）。
- **中间件 6 镜像同样走 save/scp**：实测远程拉 ddn 被拒（`swr.cn-north-4.myhuaweicloud.com/ddn-k8s` 要求 SWR 登录态，远程无凭据 → `no X-Auth-Token`），而远程 docker.io 直连不可靠 → 本机拉齐后 `docker save | gzip` 一次性传输（~1.5GB），远程 `docker load`。
- 构建网络教训（本机）：
  - build 容器默认 bridge 网络 DNS 解析失败（先后 EAI_AGAIN 击中 registry.npmjs.org 与 registry.npmmirror.com；npm 把 DNS 超时竞态表象为 "Exit handler never called!"，`npm ci` 半途而废却 exit 0 → node_modules 不完整）→ **`docker build --network=host`** + 构建内 `npm config set registry` 统一源 + 安装后 `test -x node_modules/.bin/tsc` 显式校验（防御保留，见 `cds-frontend/Dockerfile`）。
  - 阿里云 PyPI 镜像对本机 IP 返回 403（IP ACL blacklist）→ 先用官方 PyPI 直连（中途连接断在 423MB wheel）→ **清华 tuna 源**（`Dockerfile.api` 的 `PIP_INDEX_URL` ARG，默认官方源行为不变）落定 build4 成功。

## 4. 沙箱执行路径决策（KVM 硬限制）

远程无嵌套虚拟化 → 按用户 SS-13 指令（TEE 可选、无 TEE 硬件用裸 MicroVM）与诚实降级铁律：
- **远程**：控制面 + 完整功能/场景测试；`CDS_CUBE_DATAPLANE_ENABLED=false`，沙箱创建**诚实 400 `RUNTIME_UNAVAILABLE` fail-closed**（fail-closed 本身作为安全测试项在远程执行）。
- **本机**（/dev/kvm 可用，kratos 用户有 kvm ACL）：Cube 计算节点 bring-up（`deploy/cube-node/`：preflight→install→verify；XFS reflink 数据盘 `/data/cubelet`；上游 CubeSandbox 钉住 `9b75d33907ed`，工作副本零 diff），真实 MicroVM 隔离/出口拒绝/TEE 披露三表面一致性测试。状态：bring-up 需要 root（XFS loop 盘 + 内核参数），root 命令块已交付用户，**待用户执行**（2026-10-13 提问超时未答，不阻塞远程轨）。

## 5. 部署执行记录

- `/root/cds.env`：`deploy/e2e-remote/make_env.sh` 生成（openssl 随机密钥，600 权限；CUBE_DP=false）。
- **e2e 环境声明**（写入 `/root/cds.env`，诚实披露）：`CDS_DEBUG=true`（功能测试栈，放宽 prod 启动安全门；沙箱/auth/egress 行为不受影响）、`CDS_AUTH_MFA_ENABLED=false`（e2e 业务闭环不走 MFA 流程，MFA 面由套件单测/API 测覆盖）。脚本内 federation 关闭、column key/SM3 等改为 env 驱动（prod 部署时由环境给合规值，见 `remote_deploy.sh` 注释）。
- 编排：`deploy/e2e-remote/remote_deploy.sh`（8 容器：postgres/redis/clickhouse/minio/opa/vault + cds-api + cds-frontend；命名卷；健康等待；幂等）。
- 镜像传输：`cds-api.tar.gz`（3.3GB）/ `cds-frontend.tar.gz`（26MB）/ `cds-middleware.tar.gz`（6 镜像）→ scp → 远程 `docker load`。
- 执行结果：**deploy#12（2026-10-13）一次成功**。deploy#1-#11 的败点全部为部署工具/代码缺陷（见 04-defects-and-fixes.md D-e2e-06/07/08），非环境因素；#12 时全部前置验证已绿（镜像 3.12 导入冒烟、真实 PG 上 alembic 全链 + create_all 双路径 RC=0）。

## 6. 验收 S1 证据

**S1 PASS**（deploy#12 后，`/root/s1_check.sh` 输出，2026-10-13）：

1. `GET :8000/health` → **HTTP 200**：
   `{"status":"ok","service":"cds","version":"0.1.0","capability":{"levels":["L0","L1","L2","l2c","L3","k8s"],"l2c_planned":true,"baseline":"2026-10-12"}}`
2. `GET :8000/metrics` → Prometheus 文本（`# HELP cds_http_requests_total HTTP requests handled` / `# TYPE ... counter` / `cds_http_request_duration_seconds`）。
3. 前端 `:3000` → **HTTP 200**，`<!doctype html>` zh-CN 管理台文档。
4. `docker ps`：8/8 容器 Up，`cds-api: Up (healthy)`（postgres/redis/clickhouse/minio/opa/vault/frontend 同 Up）。
