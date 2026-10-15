# 01 — 远程环境部署记录（SS-14 e2e，2026-10-13）

> 目标：CDS 全栈部署到远程服务器 100.112.5.x:22022（Tailscale 入口），中国境内依赖走国内源，镜像走渡渡鸟 ddn 加速。

## 1. 远程主机事实（ssh 探测定稿）

| 项 | 值 |
|---|---|
| 入口 | 100.112.5.x:22022（Tailscale；root 免密） |
| 内网 IP | 192.168.17.68/24（eth0） |
| OS | openEuler 22.03 LTS（x86_64，kernel 5.10.0-60） |
| 资源 | 32C / 62GB RAM（部署时 59GB 空闲）/ 197GB 磁盘（185GB 空闲） |
| 包管理 | dnf/yum |
| 虚拟化 | KVM guest，**无嵌套虚拟化**（CPU 无 vmx/svm 标志）→ 无 /dev/kvm（见 §5 硬件边界） |
| Docker | 部署前未安装 → 经 aliyun 镜像源安装（get-docker.sh MIRROR=aliyun） |

## 2. 国内源 / 镜像加速策略

| 用途 | 源 | 说明 |
|---|---|---|
| 远程 docker 安装 | get.docker.sh + MIRROR=aliyun（mirrors.aliyun.com/docker-ce） | 中国境内直连 |
| docker 镜像 | 本地（国际网络）直拉 docker.io → docker save → scp → docker load 上远程；docker.io 失败时转 ddn：swr.cn-north-4.myhuaweicloud.com/ddn-k8s/<原路径>（检索 https://docker.aityp.com/i/search） | 远程不经手任何 registry 拉取 |
| Python 依赖 | 构建在本机（.venv 3.14 / Dockerfile.api python:3.12-slim）；本机国际网络直连 PyPI | 远程不装 Python 依赖 |
| npm 依赖 | 构建在本机 cds-frontend（package-lock.json，npm ci） | 远程不装 npm 依赖 |

镜像清单（compose 主文件 docker-compose.yml + 两个构建镜像）：
postgres:16-alpine / redis:7-alpine / clickhouse/clickhouse-server:24.3-alpine / minio/minio:latest / openpolicyagent/opa:0.68.0 / hashicorp/vault:1.15 + cds-api（build: Dockerfile.api，python:3.12-slim 两阶段）+ cds-frontend（build: cds-frontend/Dockerfile，node:20-alpine → nginx:alpine）。

## 3. 部署步骤（执行记录随过程回填）
1. 远程装 docker（aliyun 源）—— 进行中，日志 /tmp/ss14_remote_docker_install.log
2. 本机构建 cds-api / cds-frontend 镜像（前置：SS-12 平台 API key 实现完成，代码树定稿）
3. 本机 pull 全部 8 镜像 → docker save 打 tar → scp → 远程 docker load
4. 远程生成 .env（口令/端点按 KVM 决策定 CDS_CUBE_DATAPLANE_ENABLED 等）→ docker compose up -d
5. 验证：/health 200、/metrics、前端页（真浏览器截图）、admin 登录冒烟
6. Cube 节点 bring-up —— 依 KVM 决策（§5）

## 4. 镜像拉取/构建日志（回填）
- 本机中间件 6 镜像：/tmp/ss14_pulls.log
- 本机构建基础 3 镜像：/tmp/ss14_pulls2.log

## 5. 硬件边界（KVM，决定性事实）
远程为 KVM guest 且 hypervisor 未开嵌套虚拟化（/proc/cpuinfo 无 vmx/svm），无法产生 /dev/kvm；deploy/cube-node/preflight.sh check_kvm 为硬门禁 → **Cube 计算节点（MicroVM 沙箱）不能在该主机运行**。控制面全栈不受影响。处置选项（管理员决策中）：A 换/开 KVM 主机全量部署；B 远程控制面 + 本地 KVM 工作站节点（Tailscale gRPC mTLS）；C 双环境拆分（远程控制面全功能 + 本地完整环境跑沙箱执行测试）。
