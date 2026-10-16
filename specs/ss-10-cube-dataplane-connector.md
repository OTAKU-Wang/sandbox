# SS-10：Cube 数据面连接器（CubeSandbox 节点数据面 · 轻量接入）

> 本文件为**权威版本**。本地工作副本（仅本机、不入 git）：`~/Documents/workspace/CubeSandbox/LOCAL/ss-10-cube-dataplane-connector.md`（经 `.git/info/exclude` 本地排除，不影响上游仓库拉取；修改本文件后复制同步，副本头部多一行位置注释）。

> 版本：v1.1 | 分系统编号：SS-10
> v1.1 修订（2026-10-12，基于上游 v0.7.3-rc2 源码 + CDS 代码复核）：① CB-03 SM4-GCM 由 `tongsuo enc` CLI（Tongsuo `enc` 一律拒绝 AEAD 密码）改为链接 libtongsuo 的 helper 二进制，见 §2.9，M-01/MR-A1 计划联动回写；② 核实 Cubelet 管理 gRPC 为 plaintext-only（`grpc.NewServer()` 无 TLS，TLS 配置只覆盖 stream server），mTLS 改为节点侧 TLS 前置代理 sidecar 实现，见 §2.10；③ §2.4 `private_data` 流转改为实际机制（CDS 注入 `plugin-volume-sources` 注解）；④ §3.4 运行时级别表与代码枚举对齐（L0=进程隔离、L3=bwrap+seccomp，v1.0 "L0=bwrap" 系命名错误）；⑤ 次要：grpcio 依赖登记、新 W2 错误码登记、m4 幂等去重改 PG 表、BOM 补全、契约路径增列 `Cubelet/storage/pluginvolume.go`
> 上游：SS-01（密钥/身份）、SS-02（策略包）、SS-03（任务调度）、SS-04（沙箱运行时 · 本 spec 为其 L2' 扩展）、SS-05（输出网关）
> 下游：SS-06（审计）、SS-07（AI 训练管线 · GPU 路径远期 CB-09）
> 基准：CubeSandbox v0.7（`~/Documents/workspace/CubeSandbox`，proto + 源码 + deploy/ 实读，2026-10-12 复核）
> 性质：可执行落地方案，面向低级执行 agent
> 前序：`docs/cubesandbox-parity-plan.md`（W1–W19 已落地；本 spec 修订其 §1.3 裁决）、`specs/sandbox-productization-round3-spec.md`（A3/A7/N13 由本 spec 收口）、`docs/mock-remediation-plan.md`（M-01 国密 SM4-GCM 在 CB-03 获得真实消费者；**M-01 原计划的 `tongsuo enc` CLI 路径不可行——由本 spec §2.9 修订，该整改计划文档需随之回写**）
> 编号：CB-XX（Cube Bridge），与 W/N 系列独立，避免与既有任务编号冲突

---

## 一、目标与决策记录

### 1.1 一句话目标

把 CDS 的 L2 执行通道（firecracker 模拟 / K8s pod）替换为**零 diff 的 CubeSandbox 节点数据面（KVM MicroVM）**：CDS 作为唯一控制面，通过 `CubeboxMgr` gRPC 契约直驱各计算节点 Cubelet。全部"数据空间"业务逻辑只存在于三处——CDS 代码、CDS 自有插件二进制（cds-volume-plugin）、配置（egress rules / Cubelet 配置文件）。

### 1.2 决策记录：三路线对比与裁决

| 路线 | 代码债 | 升级债 | 控制面 | 裁决 |
|---|---|---|---|---|
| B1 fork CubeSandbox 改源码 | 高（每处修改永久 rebase） | 极高（安全补丁断档） | 单 | ❌ 否决 |
| A 部署全平台（CubeAPI/CubeMaster）+ E2B API 客户端 | 0 | 低（公共 API 稳定） | **双**（调度/配额/审计双轨记账） | ❌ 否决（双记账 = 本项目 mock 审计已定性的系统性问题，参照 DP 双预算教训） |
| **B 直驱节点数据面（本 spec）** | 低（4 个接入件，全在 CDS 侧） | 中（内部契约 → 版本钉住 + 冒烟纪律对冲） | 单（CDS） | ✅ **采用** |

裁决理由：
1. 技术债 = 自己维护的代码 + 押注的契约。stock 部署时未使用特性的维护成本为 0。
2. CDS 已具备控制面全部要素：node_selector（P1-6 加权随机）+ W14 节点运维（isolate/stale/offline）+ W9 自动暂停（含合约重验）+ W2 错误契约 + W16 任务队列。CubeMaster 的边际价值≈0，代价是双轨记账。
3. 节点数据面是稳定资产，控制面可插：CubeAPI/CubeMaster 只是同一节点栈的另一客户端，未来要 E2B 兼容或 WebUI 可叠加，节点不动。B 是扩展路径，非死胡同。
4. B 路线的升级债处置 = K8s 管 kubelet 版本的方式：按节点钉版本 + 每版本冒烟 + 滚动；最坏情况是钉版本（运维债，非代码债）。

### 1.3 对前序裁决的修订

| 前序裁决 | 出处 | 本 spec 处置 |
|---|---|---|
| E2B SDK / E2B API 形态不对齐 | parity-plan §1.3 | **保留**（CDS API 形态不变） |
| envd in-VM agent 不对齐（CDS 执行面由宿主侧驱动） | parity-plan §1.3 | **修订**：guest 执行通道使用 envd（它属于节点数据面 stock 组件，不是 CDS 自建 in-VM agent；宿主侧仍由 CDS 驱动） |
| OCI 模板构建 / Template Store 不对齐 | parity-plan §1.3 | **部分修订**：复用节点侧模板原语（ext4 artifact + AppSnapshot + artifact 注解），CDS 自建模板注册与分发；不部署 TemplateCenter 进程（build 场景例外见 CB-05 回退） |
| 跨节点 pause/resume 属远期 | parity-plan §1.3、round3 N13 | **提前为 P2（CB-07）**，前置=节点数据面版本支持 S3 backend |
| 组件多版本共存矩阵不对齐 | parity-plan §1.3 | **保留**（CDS 单体控制面；节点按 `runtime_version` 钉版本） |

### 1.4 铁律（红线，执行 agent 不可违反）

1. **节点数据面零 diff**：计算节点上的 CubeSandbox 组件（cubelet / shim / cube-runtime / CubeCoW / CubeVS / CubeEgress / guest 资产）永远是 stock release，不改源码、不 patch 二进制、不重编译。
2. **CDS 是唯一控制面**：不部署 CubeAPI / CubeMaster / CubeOps / WebUI / TemplateCenter（compute 节点）/ CubeProxy / cube-lifecycle-manager / Cube 侧 Redis / MySQL。调度、配额、审计、状态机全部走 CDS 单轨。
3. **禁伪造（沿用项目铁律）**：任何路径不可实现时（流式、跨节点 resume 等）必须诚实标注降级（`is_simulation` / `honest degradation` 纪律），禁止静默。
4. **数据不出域**：原始数据只经 CDS 加密卷（RO）进入沙箱；结果只经结果卷（宿主侧读 → SS-05 输出网关）离开；沙箱无任何文件 API 面（CubeAPI 未部署 → E2B 文件 API 面本身不存在）。
5. **审计不可丢弃**：CB-06 的审计尾随链路任何降级都必须告警，禁止 W13 式"队满丢弃"语义出现在审计路径。
6. **上游是安全基线**：CubeSandbox 安全补丁必须跟进（CB-02 升级纪律）；CDS 侧不对节点组件做“自补”。
7. **上游仓库零本地**：本地 CubeSandbox 仓库（工作副本）**不承载任何本地 commit、不修改任何 tracked 文件**；唯一合法写操作 = `fetch` / `reset --hard <upstream tag|sha>`。全部独立开发只存在于 CDS 仓库——工作流详见 §V。

### 1.5 范围外（不做）

- L1 TEE 路径（CDS 自建 SGX/SEV-SNP，FG-001 环境验收项）——不动
- L0/L3 低隔离路径（进程隔离 / bwrap+seccomp）——保留，降为 dev / 低敏感用途（SDK/前端标注；级别命名已按 §3.4 修正）
- E2B SDK 兼容——不做（§1.3）
- K8s pod 运行时（`k8s_sandbox.py`）——弃用（§3.5 处置清单）
- GPU / ARM / PVM——本轮不做（GPU 对齐 FG-002，登记 CB-09）
- 重写 CDS 既有 L0 功能代码（L0 流式、L0 共享卷等保留原样）

---

## 二、契约基线（2026-10-12 源码复核；实施时若与现状冲突，以代码为准并回写本文件）

### 2.1 节点数据面 BOM（`DEPLOY_ROLE=compute`）

证据：`deploy/one-click/install-compute.sh`（wrapper → `install.sh` 带 compute role）；`deploy/one-click/systemd/cube-sandbox-compute.target` 与 control target 独立；`install.sh:311-330` 证明 compute role 不部署 MinIO/MySQL/Redis。

| 组件 | 形态 | 职责 |
|---|---|---|
| cubelet（Go） | systemd | 节点生命周期 agent；暴露 `CubeboxMgr`/`Images` gRPC + `:9998` metrics |
| containerd-shim-cube-rs（Rust） | 经 containerd | Shim v2，VM boot/restore，vsock |
| cube-runtime（RustVMM+KVM） | 经 shim | MicroVM（独立内核），<60ms |
| CubeCoW（Rust lib） | cubelet 内 | FICLONE O(1) 快照/克隆（**节点数据盘必须 XFS reflink**） |
| CubeVS（eBPF） | 宿主内核 | L3/L4 线速策略、conntrack、TPROXY 转向、内置私网 CIDR 防护 |
| CubeEgress（OpenResty） | systemd | L7 默认拒绝 + 凭据注入 + 强制点 JSONL 审计（TLS MITM，沙箱信任其根 CA） |
| guest kernel（vmlinux）+ guest image + cube-agent.ext4 | 文件 | VM 内容；envd guest agent |
| containerd | systemd | 镜像拉取 |
| **cds-volume-plugin（CDS 二进制）** | 独立进程/socket | CDS 组件①（CB-03；独立 gRPC server，不修改任何 stock 文件/配置模板，仅在节点 Cubelet 配置注册；镜像内置 libtongsuo + SM4-GCM helper，§2.9） |
| **TLS 前置代理 sidecar（CDS 组件）** | systemd | CDS 组件②（CB-02；终结 CDS↔节点 mTLS，明文转发本地 cubelet gRPC 端口——stock cubelet gRPC 为 plaintext-only，§2.10） |
| **审计 tailer（CDS 脚本/小进程）** | systemd | CDS 组件③（CB-06；tail egress 审计 JSONL 推 CDS，见 CB-06） |

上表 = CubeSandbox stock release（v0.7，install-compute.sh）全量 + 三个 CDS 组件（①②③ 均为独立进程/systemd 单元，由 CB-02/CB-06 节点 bundle 交付）；"零 diff" 声明 = stock 部分零源码改动、零二进制补丁、零配置模板改动。节点前置：`/dev/kvm` 可读写、XFS（reflink）数据盘、eBPF 内核支持、containerd 可用。CB-02 preflight 逐项硬门禁。

### 2.2 `CubeboxMgr` gRPC（`CubeSandbox/pkgs/proto/services/cubebox/v1/cubebox.proto`，15 RPC）

| RPC | 用途（本 spec） | 已核实关键字段 |
|---|---|---|
| `Create(RunCubeSandboxRequest)` | 会话沙箱创建 | `volumes[]`（Volume{name, volume_source}）、`containers[]`（ContainerConfig：`image: ImageSpec{image, storage_media, annotations}`、command/args、`envs[]`、`volume_mounts[]`、`resources`、`probe`）、`annotations`、`labels`、`exposed_ports`、`instance_type`、`NetworkType` |
| `Update(UpdateCubeSandboxRequest)` | **暂停/恢复 + 出向策略热换** | `annotations` + `optional CubeNetworkConfig`（缺省=不动网络；present=完整期望态，空字段=清除） |
| `Destroy` | 会话销毁 | 幂等；artifact in-use 时返回 Conflict（保护语义，非错误） |
| `List` | 节点沙箱清单 / 健康探针 | — |
| `Exec` | 命令执行 | `sandbox_id`、`container_id`、`terminal`、`args[]`、`env[]`、`cwd` |
| `AppSnapshot` | **模板生成**（create→snapshot→destroy，节点原语） | `create_request`（必需注解 `cube.master.appsnapshot.create="true"`、`cube.master.appsnapshot.template.id`）+ `snapshot_dir` + `backend`（xfs\|s3，空=xfs） |
| `CommitSandbox` / `RollbackSandbox` | 会话快照/回滚（替代 tar，A7/N13 收口） | — |
| `ListSandboxSnapshots` / `ListLocalSnapshots` / `GetLocalSnapshot` | 快照目录/校验 | 供 master 发现物理引用 |
| `CleanupTemplate` / `CleanupOrphanStorageFiles` / `InspectStorageVolumes` / `GetStorageMetrics` | 节点存储治理/健康 | — |

**注意**：无独立 Pause/Resume RPC——暂停/恢复走 `Update` + annotations（见 2.6）。

### 2.3 卷类型（`VolumeSource`，五选一）

| 成员 | 本 spec 用途 |
|---|---|
| `plugin_volume{driver}` | CDS 加密数据卷（CB-03 插件，driver=`"cds-encrypted"`） |
| `host_dir_volumes[{name, host_path}]`（virtiofs 通道） | 结果卷（`/var/lib/cds/sandbox-results/<session_id>`，RW）；共享工具链/缓存（RO，可选） |
| `empty_dir{medium, size_limit}` | 沙箱 scratch |
| `sandbox_path` / `image` | 不用 |

`VolumeMounts{name, container_path, readonly}`：插件卷的 rw/ro 由 sandbox 侧 `volumeMounts.readonly` 决定（`volumeplugin.proto` 注释实证：Attach 不再携带 read_only）。

### 2.4 Volume 插件协议（`pkgs/proto/services/volumeplugin/v1/volumeplugin.proto`）

- **Node 侧** `VolumePluginService`：
  - `Attach(sandbox_id, namespace, volume_id, volume_base_dir, ref_count, private_data≤1024B) → {host_path, metadata}`
    - **硬约束**：`host_path` 必须位于 `volume_base_dir` 内（上游校验，典型 `<base>/<plugin>-<volume_id>`），否则 attach 被拒
    - `ref_count` 为 attach 前计数：0=首次（做宿主级初始化）
  - `Detach(sandbox_id, namespace, volume_id, metadata, ref_count)`：`ref_count` 为 detach 后计数；0=最后（拆宿主级资源）
- **Controller 侧** `VolumeControllerService`：`Create(volume_id, name) → {token, private_data}`；`Destroy(volume_id)`
- RPC 型插件：单个 gRPC server 二进制可同时实现两服务；节点 Cubelet 配置注册（driver 名 + socket/binary 路径，参照 CubeSandbox `docs/guide/volume-plugin.md`）
- **`private_data` 流转（v1.1 修订，2026-10-12 源码复核）**：Cubelet 不持久化 private_data——它从 Create 请求的 **`plugin-volume-sources` 注解**读取（JSON 数组 `[{name, driver, private_data}]`，证据：`Cubelet/storage/pluginvolume.go:lookupPluginVolumeSource`；stock 部署中由 CubeMaster 读 `t_cube_volume` 表后注入该注解）。直驱模式下 **CDS 自己注入该注解**（CB-01 组装请求时）：`private_data` 持久化在 CDS DB，插件无业务态。回退路径 `VolumeSource.plugin_volume{driver}` 仅提供 driver（无 private_data，不满足本 spec 的 DEK 引用场景，必须走注解路径）
- `volumeID == volume.Name`（cubelet 实现语义，同文件）：`volumes[]` 中的卷名即 volume_id（本 spec 取 `cds-data-<session_id>`）

### 2.5 模板 artifact

- 创建引用：`ImageSpec{image: <artifactID>, storage_media="ext4", annotations{cube.master.instance.type: "cubebox"}}`
- 节点拉取注解（`CubeMaster/pkg/base/constants/constants.go:113-118`）：`cube.master.rootfs.artifact.{id, job_id, url, token, sha256, size_bytes}`——节点自 URL 下载 ext4（token 鉴权 + sha256 校验）
- `Images` 服务：`CreateImage` / `ListImages` / `DestroyImage`（幂等；`DestroyImage` 对 in-use artifact 返回 Conflict → 延迟 GC，上游语义）
- 模板生成：节点侧 `AppSnapshot` 原语（2.2）

### 2.6 暂停/恢复协议

- 证据：`CubeMaster/pkg/cubelet/actions.go`（`UpdateWithTimeout` 注释 "Pause uses a longer deadline"；`UpdateCubeSandboxResponse` 注释 "Pause returns volume ref-count events"、"S3 Pause: cubecow_export_snapshot uuids"）
- 注解（`constants.go`）：`cube.master.pause.snapshot.id`（master 分配，snap-* 格式）、`cube.pause.keep_tombstone`、`cube.pause.delete_tombstone`、`cube.pause.error`
- **实施前置（CB-01 启动前必须完成）**：对照 `CubeMaster/pkg/cubelet/` + `pausesnap` 包核实暂停与恢复的确切注解组合与请求形状，回写本节；以上游代码为准

### 2.7 Egress 审计（强制点）

- 文件：`/data/log/cube-egress/access.jsonl`（`CubeEgress/lua/audit.lua:35` 默认路径，`file_path` 选项可覆盖）
- 事件：`access` / `security_event`（含 allow 路径上游失败）/ `tls_handshake`；秘密脱敏（`redactor.lua`）
- 与 CDS W13（应用层判定日志 `egress_audit.py`）互补：网络层判定以本节强制点记录为准

### 2.8 出向策略（`CubeNetworkConfig`，`cubebox.proto:596-633`）

```
CubeNetworkConfig {
  allow_internet_access: bool,    // false → 0.0.0.0/0 deny-all，仅显式 allow 放行
  allow_out: [ip | cidr | dns],   // dns 走 CubeVS DNS A 记录学习
  deny_out: [ip | cidr],
  rules: [ EgressRule { name,
    match { sni | host | method[] | path | scheme | port },   // AND 匹配，列表 first-match-wins
    action { allow, audit, inject[ {header, secret, format} ] } } ]  // 未命中任何规则 = deny
}
```

- L3/L4 由 CubeVS（eBPF）执行；L7 由 CubeEgress（TPROXY+MITM）执行
- 内置防护：`allow_internet_access≠false` 时宿主内部 CIDR 自动入 deny（防沙箱探测基础设施）
- **热换**：`Update(CubeNetworkConfig)` 对运行中沙箱生效（`cubebox.proto:756-759`）
- L7 依赖：沙箱信任 CubeEgress 根 CA——**CDS 场景镜像构建时必须 bake 该 CA**（CB-04 步骤 3）

### 2.9 SM4-GCM 实现载体（v1.1 修订：取代 M-01/MR-A1 原 CLI 计划）

- **`tongsuo enc -sm4-gcm` 不存在**：Tongsuo（OpenSSL fork）的 `enc` 命令一律拒绝 AEAD 密码——证据：`Tongsuo/apps/lib/opt.c:opt_cipher`（`EVP_CIPH_FLAG_AEAD_CIPHER` → "AEAD ciphers not supported"）及 `apps/enc.c` 密码列表过滤。M-01/MR-A1 原计划（`tongsuo enc -sm4-gcm -K <hex> -iv <iv> -in - -out -`）及其 `openssl3` 备选（OpenSSL 3 `enc` 同限制）**不可行**；本节为权威修订，M-01 整改计划文档需随之回写。
- **可行路径**：库内 `EVP_sm4_gcm()` 已注册（`Tongsuo/crypto/evp/c_allc.c:72`）。CB-03 采用**链接 libtongsuo 的小 C helper**（约百行：`EVP_EncryptInit_ex(EVP_sm4_gcm())` + `EVP_CTRL_GCM_SET_IVLEN`/`EVP_CTRL_GCM_SET_TAG`/`EVP_CTRL_GCM_GET_TAG`，密钥/明文走 stdin，AAD 可选，subprocess timeout + 失败冒泡）执行 at-rest SM4-GCM 加解密；helper 与 libtongsuo 由 vendor 源码（`make -C Tongsuo`）构建，bake 进 cds-volume-plugin 镜像（CB-03 构建链；插件启动时自检：加解密往返 + tag 校验，失败即启动失败）。
- `CDS_SM4_AEAD_BACKEND=tongsuo`（生产默认）判定 = "helper 在场且自检通过"；缺 helper 或自检失败 → 插件启动失败、fail-closed、禁止静默回退 AES（项目铁律）。

### 2.10 节点 gRPC TLS 现实（v1.1 修订：2026-10-12 源码复核）

- **Cubelet 管理 gRPC 为 plaintext-only**：服务端 `grpc.NewServer()` 无 transport credentials（证据：`Cubelet/services/server/server.go`）；配置中的 TLS 项（`enable_tls_streaming` + `x509_key_pair_streaming`）只覆盖 stream server，不覆盖 `CubeboxMgr` 管理服务。因此 **CDS → cubelet 直接 mTLS 拨号不可行**（对端不会说 TLS）——v1.0 "不支持则 mTLS 必须" 的表述废止。
- **本 spec 的 mTLS 纪律由节点侧 TLS 前置代理 sidecar 实现**（CDS 组件②，独立 systemd 单元，stunnel/caddy/envoy 类）：CDS 持 `certificate_service` 签发的客户端证书对 sidecar 端口（`CDS_CUBE_NODE_TLS_PROXY_PORT`）做 mTLS → sidecar 明文转发 `127.0.0.1:<cubelet gRPC 端口>`。sidecar 由 CB-02 节点 bundle 交付，非 CubeSandbox 组件（不违反红线 1），其证书同样由 `certificate_service` 签发（节点纳管=发证书+注册，与插件 mTLS 证书同一 CA 体系）。
- 回退（仅限显式低风险的本地环境）：plaintext gRPC + 专用管理网段 + host 防火墙白名单（只放 CDS 控制面源地址）；需显式 `CDS_CUBE_NODE_GRPC_TLS=false` + `validate_security_config` 生产告警（M-08 纪律：生产默认不裸跑）。

---

## 三、目标架构

### 3.1 拓扑

```
┌────────────────────────────────────────────────────────────────────┐
│  CDS 控制平面（现有 FastAPI 部署不变：compose / helm / k3s）        │
│                                                                    │
│  会话/任务状态机 · 合约 · KMS/国密 · 输出网关 · 审计链(Merkle)       │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────┐  │
│  │ CB-01         │  │ CB-02 编译器  │  │ CB-06 审计接收端点       │  │
│  │ CubeNodeClient│  │ policy→Cube  │  │ /internal/audit/egress  │  │
│  │ (grpcio 直驱  │  │ NetworkConfig│  │ (mTLS, 幂等键去重)       │  │
│  │  各节点       │  │ (含热换)      │  │                         │  │
│  │  Cubelet)     │  └──────────────┘  └─────────────────────────┘  │
│  └──────┬───────┘      ↑ 策略包(SS-02)                             │
└─────────┼──────────────────────────────────────────────────────────┘
          │ gRPC (mTLS → 节点 TLS sidecar → 明文 cubelet；§2.10)
   ┌──────┴──────────────────────────────────────────┐
   │  计算节点 × N（裸金属/VM，DEPLOY_ROLE=compute）   │
   │  cubelet ─ shim ─ cube-runtime(KVM MicroVM)     │
   │    ├─ CubeCoW (XFS reflink)                     │
   │    ├─ CubeVS (eBPF: L3/L4 + TPROXY 转向)        │
   │    ├─ CubeEgress (L7 默认拒绝 + 注入 + JSONL)   │
   │    ├─ containerd (镜像/artifact 拉取)           │
   │    └─ CDS 件: vol-plugin / TLS sidecar / tailer │
   │  资产: guest kernel / guest image / agent.ext4   │
   │  版本钉住: sandbox_nodes.runtime_version         │
   └─────────────────────────────────────────────────┘
```

**不部署**：CubeAPI / CubeMaster / CubeOps / WebUI / TemplateCenter（compute）/ CubeProxy / cube-lifecycle-manager / Cube Redis / MySQL（install.sh:311-330 证明 compute role 天然不含这些）。

### 3.2 数据进 / 出（密态闭环）

```
数据进（原始数据，高敏感）:
  CDS 数据目录快照(密文) ──(CDS→节点插件扩展 API, mTLS)──> cds-volume-plugin
  插件: SM4-GCM at-rest 加密(Tongsuo 子进程路径 —— M-01 真实接线)
  沙箱创建: volumes=[plugin_volume{driver:"cds-encrypted"}]
           + volume_mounts{readonly: true, container_path:"/data"}
  沙箱内: 持 CDS_DEK_HEX 解密（沿用现有密态模式）
  宿主磁盘: 永远密文（插件目录）；DEK 只存于 KMS wrapped 形态 + 插件内存（用完 zeroize）

数据出（结果，已过审查，低敏感）:
  任务写 /results/（= host_dir_volumes 卷, RW, 宿主路径
    /var/lib/cds/sandbox-results/<session_id>）
  → 沙箱 Destroy 后 CDS 宿主侧读结果文件
  → SS-05 输出网关（PII/DP/水印/行限制，现有 T5 强制路径）
  → 现有会话文件下载端点交付调用方
```

安全切分依据：原始数据（高敏感）at-rest 加密——宿主被拖盘也只见密文；结果（已经输出网关审查）at-rest 明文存于 CDS 管控存储，与"ClickHouse 存结果"信任级一致。该切分避免插件提供宿主侧读 API（简化下载路径）。**若合规评审要求结果亦 at-rest 加密**：升级 CB-03 插件 RW 加密模式（登记，非本轮范围）。

### 3.3 session ↔ sandbox 映射

| CDS 概念 | Cube 数据面对象 | 备注 |
|---|---|---|
| SandboxSession（RUNNING） | cubelet sandbox | session_id ↔ sandbox_id 1:1，`sandbox_sessions.runtime_ref` 记录 |
| session SUSPENDED | paused sandbox（Update） | W9 唤醒链（合约 ACTIVE 重验）**顺序不变**，resume 动作换为 Update |
| 会话数据卷 | plugin volume（`volume_id=cds-data-<session_id>`；`plugin-volume-sources` 注解携带 `private_data`，§2.4） | 会话终止：Detach→Destroy（shred） |
| 会话结果卷 | host_dir volume | 交付/归档后由 GC 清理（retention 对齐 W12 纪律） |
| 场景模板（JSON 型，Round 40） | cube template artifact（ext4 + artifactID） | CB-05 注册表；CDS template_id → artifactID 映射列 |
| 策略包（SS-02） | CubeNetworkConfig | CB-02 编译器输出；热换走 Update |
| 节点（sandbox_nodes） | cubelet endpoint | +`cubelet_endpoint` +`runtime_version` |

### 3.4 运行时级别

| 级别 | 技术（以代码 `SandboxLevel` 枚举为准） | 定位 | 变化 |
|---|---|---|---|
| L0 | 进程隔离（ProcessAdapter：cgroup + 输出限制） | **dev / 低敏感** | 保留（降定位标注） |
| L1 | TEE（CDS 自建） | 最高密级，FG-001 硬件验收 | 不动 |
| **L2'** | **CubeSandbox MicroVM** | **生产默认** | **新增**（level 值 `l2c`；旧 `l2`=firecracker 路径标记 deprecated，SDK/前端标注，两个版本观察期后移除） |
| L2 | Firecracker + gVisor（firecracker_runtime.py） | 原生产路径 | 降级（`CDS_LEGACY_L2_ENABLED` 门禁，默认 false） |
| L3 | bwrap + seccomp（BwrapAdapter；netns/iptables 零信任，§3.5） | **dev / 低敏感** | 保留（降定位标注） |
| k8s | K8s pod（共享内核） | 原分布式路径 | 降级（同上门禁） |

> v1.1 更正：v1.0 "L0=bwrap" 系命名错误——代码枚举 L0=进程隔离、L3=bwrap+seccomp（另有第 6 值 `k8s`），本表按代码对齐（以代码为准）。

### 3.5 既有模块处置清单（替换 / 弃用 / 修改）

| CDS 模块 | 现状 | 处置 |
|---|---|---|
| `app/services/firecracker_runtime.py`（1003L） | L2 旧路径（sim/ssh/serial，M-25/FG-003） | **弃用**：`CDS_LEGACY_L2_ENABLED` 门禁（默认 false）；两版本观察期后删除（删除为独立决策项） |
| `app/services/k8s_sandbox.py` + `k8s_client.py`（801L+） | K8s pod 运行时（N7 产物） | **弃用**（同上门禁）；理由：pod=共享内核，L2' 完全覆盖且隔离更优；避免第三套运行时维护 |
| `app/services/sandbox_runtime.py` | 运行时 facade（2560L） | **修改**：注册 `CubeRuntimeAdapter`；L0/L1 分发逻辑不动 |
| `app/services/network_policy.py`（661L） | netns/iptables/DNS proxy | **修改**：L2' 级走 CB-02 编译器（不再建 netns/iptables）；L0/L3 低隔离路径保留 |
| `app/services/session_snapshots.py` | tar.gz 快照（A7） | **修改**：L2' 走 `CommitSandbox`/`RollbackSandbox`（新增 level 分支）；L0 tar 保留 |
| `app/services/egress_audit.py`（W13） | 应用层判定 JSONL | **保留**（CDS 侧判定留痕）；网络层判定权威来源改为 CB-06 强制点尾随 |
| `app/services/node_selector.py` | 加权随机选点 | **修改**：+`runtime_version` 过滤（CB-01）+ 模板本地性约束（CB-05） |
| `app/services/session_lifecycle.py` | 清理循环 | **修改**：+ cube 节点健康探针；W9 auto_resume 的 resume 动作换为 Update（唤醒链不变） |
| `app/services/session_files.py` | 文件 API（T5 出域审查） | **修改**：上传写数据卷（宿主侧经插件扩展 API）；下载路径不变（T5 强制） |
| `app/api/sandbox_sessions.py` | 会话端点 | **修改**：level=`l2c` 支持；错误映射对齐 W2 契约 |
| `app/models/sandbox_node.py` | 节点模型 | **修改**：+3 列（迁移 m1） |
| `app/core/config.py` | 配置 | **修改**：6.1 全部新键 |

---

## 四、任务清单

> 每个任务的 DoD 统一执行 6.3 测试与验证协议（含诚实性、配置四同步、禁项自检）。

### P0 · 接通（3 项，约 2–3 周）

#### CB-00 · 上游同步与契约监视（全任务前置）

**目标**：把“跟紧上游变更”变成有测试门禁、可重复的机制；把“上游仓库任意操作（fetch/reset/clean）影响独立开发”在结构上归零（§V）。

**实施步骤**：

1. **两类上游 checkout**（均在 CDS 运维范围，不进入 CDS git 仓库）：
   - 裸镜像：`git clone --mirror <upstream-url> /opt/cds/cube-mirror.git`（CI/生产环境；无工作树，零修改风险，唯一合法上游代码源）
   - 干净工作副本：`~/Documents/workspace/CubeSandbox`（开发阅读/本地冒烟；**红线 7**：零本地 commit、零 tracked 文件修改；唯一写入 = fetch / `reset --hard <upstream ref>`）
2. `scripts/gen-cube-stubs.sh`：读 `app/_stubs/cube/_VERSION.json` 钉住 sha → `git --git-dir=/opt/cds/cube-mirror.git archive <sha> pkgs/proto | tar -x -C <tmp>` → protoc → `app/_stubs/cube/`（**不依赖工作副本状态、零 checkout 切换**）；回写 stub_hash 至 `_VERSION.json`
3. `scripts/cube-upstream-check.sh`：fetch 镜像 → 枚举 pinned sha 之后的新 tag/commit → **契约 diff**（路径集由版本化配置 `scripts/cube-contract-paths.txt` 定义，见 5.3）→ 输出报告；exit code：0=契约面未变 / 1=已变 / 2=错误
4. CI job `.github/workflows/cube-upstream-watch.yml`（weekly + workflow_dispatch）：跑 check → 契约面未变 → 自动跟进流（临时目录重生成 stub + 回归；CB-01 合入前仅 stub 重生成 + compileall，合入后加全量 fake gRPC 测试集）→ 绿则自动开“pin 更新” PR（`_VERSION.json` + stubs）；契约面已变 → CI 评论 breaking 候选清单（文件 + hunk），不自动合入
5. **实验守则**：想在上游做本地 hack/试改 → `git clone <mirror> /tmp/cube-exp-$$` 一次性临时克隆；**永不**触碰干净工作副本
6. 落地 6.3 步骤 8 的硬门禁为可运行检查（联调脚本内 `git -C <工作副本> status --porcelain` 必须为空）

**测试**（`tests/test_cube_upstream_check.py`）：mock 镜像仓库——契约 diff 路径集命中/未命中、pinned sha 有效性校验、stub 重生成幂等（同 sha 两次 → 同 stub_hash）
**验收命令**：`pytest tests/test_cube_upstream_check.py -v`；`scripts/cube-upstream-check.sh` 手动跑一次，报告存档
**工时**：2 人日

#### CB-01 · Cubelet 节点客户端 + L2' 运行时适配器（修 A3；弃用 firecracker/k8s 路径）

**目标**：CDS 能经 gRPC 在真实节点上完成 L2' 会话沙箱的 创建→执行→销毁→暂停/恢复，节点健康纳入既有 W14 运维链。

**代码现状**：`firecracker_runtime.py`（simulation 回退 + ssh/serial 执行，M-25）；`k8s_sandbox.py`（N7 python client，pod 共享内核）；`sandbox_runtime.py:37` 的 `RuntimeAdapter` 抽象接口；W14 节点健康状态机已就绪。

**实施步骤**：

1. **proto stub 版本钉住**：
   - `scripts/gen-cube-stubs.sh`：从钉住的 CubeSandbox 版本（`CUBE_DATAPLANE_VERSION` + git sha，记录于 `app/_stubs/cube/_VERSION.json`）提取 `pkgs/proto/{services/cubebox,volumeplugin,images,errorcode,types,v1/descriptor}` → protoc → `app/_stubs/cube/`（生成物提交入库）
   - CI 门禁：stub 文件哈希 vs `_VERSION.json` 一致；不一致即红
   - 依赖登记：`requirements.txt` 增加 `grpcio`（运行时依赖，现不在 requirements 中）；`grpcio-tools` 仅用于 stub 再生成（构建期工具，非运行时依赖）
2. **`app/services/cube_node_client.py`**：
   - grpcio 安全通道池（按节点）；**TLS 处置（v1.1，§2.10）**：Cubelet gRPC 为 plaintext-only → CDS 一律对**节点 TLS 前置代理 sidecar** 做 mTLS 拨号（端口 `CDS_CUBE_NODE_TLS_PROXY_PORT`，客户端证书由 `certificate_service` CA 签发）；sidecar 不可达 → 节点判 down（W14 链），**不静默回退明文**
   - 15 个 RPC 类型化封装；`requestID=uuid4`（幂等/对账）；deadline 分级（默认 5s，`Update` 30s——对齐上游 "Pause uses a longer deadline" 语义）
   - 错误映射：`Ret.ret_code` → W2 `CDSError` 族（NotFound→404 `SESSION_NOT_FOUND`/`SANDBOX_NOT_FOUND`；Conflict→409 `SESSION_STATE_CONFLICT`；终态→410 `SESSION_TERMINATED`；暂态锁→503+`Retry-After`；超时→408）
   - 节点健康：`List`(空) + `GetStorageMetrics` 每 30s（并入既有 cleanup loop 频率）；stale→offline 走 W14 既有链（告警+审计 `node.*`）
3. **`app/services/cube_runtime_adapter.py`**（实现 `RuntimeAdapter`，`sandbox_runtime.py:37`）：
   - `provision()`：选点（node_selector + `runtime_version` 匹配 + CB-05 模板本地性）→ 组装 `RunCubeSandboxRequest`（`image`=artifact（CB-05 就绪前用验证镜像）、`envs` 含 `CDS_DEK_HEX`、`volumes`=data(plugin，name=`cds-data-<session_id>` 即 volumeID)+results(host_dir)+scratch(empty_dir)、`CubeNetworkConfig`（CB-02 输出）、annotations 预留 pause snapshot id 位 + **使用 plugin 卷时必须带 `plugin-volume-sources` 注解** `[{name, driver:"cds-encrypted", private_data}]`——`private_data` 从 CDS DB 读，§2.4）→ `Create`
   - `execute()`：`Exec` RPC（`container_id=sandbox_id`）
   - `terminate()`：`Destroy`（幂等；Conflict=in-use → critical 告警，不静默）
   - `get_status()`：`List` + 本地状态机
   - `execute_streaming()`：**day-1 spike**——验证宿主机到沙箱 envd（49983）可达性（TAP/SNAT 拓扑）：可达 → CDS 直连 envd 流式（复用 W10 帧协议 + 逐行 T5 审查，fail-closed 不变）；不可达 → 返回 `STREAM_UNSUPPORTED` 诚实标注（对齐 W10 对非 L0 级的既有纪律），exec 走同步 + `/logs` 补查
4. **状态机集成**：Create 成功/Destroy 完成/异常 → 既有 transition 函数（审计 `session.*` 不变）；410/409/408 语义映射测试化；**新 W2 码登记进 `docs/error-codes.md`**（`SANDBOX_NOT_FOUND`/`RUNTIME_UNAVAILABLE`/`NO_NODE_TEMPLATE` 并入现有 12 个具名码，保持 {code,message,detail,request_id} 四字段契约）
5. **门禁**：`CDS_CUBE_DATAPLANE_ENABLED`（默认 false；prod=true 要求≥1 节点 `runtime_version` 非空，`validate_security_config` 校验）；关闭时 level=`l2c` 请求 → 400 `RUNTIME_UNAVAILABLE`（诚实拒绝）
6. **弃用**：`firecracker_runtime.py` / `k8s_sandbox.py` / `k8s_client.py` 加 deprecated 标注 + `CDS_LEGACY_L2_ENABLED` 门禁（默认 false）

**测试**（`tests/test_cube_node_client.py`、`tests/test_cube_runtime_adapter.py`）：
- fake gRPC server（grpcio testing）：Create/Destroy/Exec/List happy path + ret_code→HTTP 映射全枚举
- stub 钉住：`_VERSION.json` 哈希校验用例
- adapter 组装断言：proto 字段级（卷类型/env/network config/annotations）
- 门禁：disabled → `RUNTIME_UNAVAILABLE`；选点排除 `runtime_version` 不匹配节点

**验收命令**：`pytest tests/test_cube_node_client.py tests/test_cube_runtime_adapter.py -v`；`python -m compileall -q app`
**工时**：5 人日（spike 1 人日）

---

#### CB-02 · 节点部署与升级工具链（版本钉住 + 滚动升级 SOP）

**目标**：节点 bring-up 可复现；升级=钉版本+冒烟+滚动；最坏情况可钉版本等待。

**代码现状**：无（CDS 侧无节点管理工具；上游 `install-compute.sh`/`smoke.sh` 现成）。

**实施步骤**：

1. `deploy/cube-node/`：
   - `preflight.sh`：`/dev/kvm` 可读写、XFS refllink（`xfs_info` + `FICLONE` 实测）、eBPF（`bpftool prog list` + 内核版本）、containerd 版本、内存/磁盘水位——逐项硬门禁，失败即非零退出（不降级）
   - `install.sh`：wrapper 调官方 `install-compute.sh`（`DEPLOY_ROLE=compute`）；注入 CDS 配置：egress 审计路径（`CDS_CUBE_AUDIT_DIR`）、`volume_plugins` 注册 `cds-encrypted`（CB-03 二进制+socket）、host_dir 允许前缀（`CDS_CUBE_RESULTS_DIR`）、CA bake 说明、TLS sidecar 证书安装（certificate_service 签发，§2.10）
   - `tls-proxy.service`（CDS 组件②，§2.10）：mTLS 前置代理（`CDS_CUBE_NODE_TLS_PROXY_PORT` 终结 → 明文 `127.0.0.1:<cubelet gRPC 端口>`）；独立 systemd 单元，不触碰 stock 单元（diff 清单归档）
   - `verify.sh`：`smoke.sh` + sidecar mTLS 握手探活（CDS 控制面源）+ CDS 节点注册探测（CB-01 客户端 List 探活）
2. **版本 bundle manifest**：`scripts/cube-bundle-manifest.json`（version、git sha、组件清单、各组件 sha256）；节点 bring-up 后写入 `sandbox_nodes.runtime_version`（如 `0.7.1+sha12`）
3. **升级 SOP**（`docs/runbook` 新章）：isolate（W14）→ 排空（存量会话 terminate 或 pause；CB-07 就绪前不跨节点迁移，排空=终止）→ 换 bundle → `verify.sh` → unisolate；批次大小=1 起步；回滚=旧 bundle 重钉
4. **安全补丁强制流**：runbook 标注"上游安全版本 → 48h 内滚动"，豁免 isolate 等待窗口
5. K8s 侧：节点为裸金属/VM（**不在** CDS k3s 内）；`helm/cds` 增加可选 `externalCubeNodes` 配置（端点列表，供前端/SDK 展示）——CDS k8s 不管理节点
6. 配置四同步：6.1 全部新键

**测试**（`tests/test_cube_node_preflight.py`、`tests/test_cube_node_upgrade_gate.py`）：
- preflight：mock subprocess，逐项断言（kvm 缺 → 非零退出且输出含该项）
- 升级门禁：isolate 期间 node_selector 不再选该节点（复用 W14 测试模式）

**验收命令**：`pytest tests/test_cube_node_*.py -v`；真实节点 preflight+install+verify 全绿（**环境验收项 FG-017**，登记 `SS-gap-analysis.md` FG 系列，对齐 FG-001..016 格式）
**工时**：3 人日

### P1 · 数据面闭环（4 项，约 4–6 周）

#### CB-03 · CDS 加密 Volume 插件（数据进域侧；M-01 真实接线）

**目标**：原始数据以 at-rest SM4-GCM 密文形态进入沙箱（宿主拖盘只见密文），DEK 生命周期受控。

**代码现状**：`kms_service.distribute_key`（wrapped key + TTL，现有）；`Tongsuo/` 已 vendor 未接线（M-01）；`shared_volumes`（W15，L0-only）；`app/utils/crypto.py` `SM4Cipher`（现为 AES-GCM 等价）。

**实施步骤**：

1. 新目录 `cds-volume-plugin/`（独立 Python 包 + Dockerfile；grpcio server，实现 `VolumePluginService` + `VolumeControllerService` 双服务，单二进制双注册）
2. **Controller 侧**（`Create`/`Destroy`，CDS 控制面直调节点插件 socket，mTLS 服务证书；**不加第二层代理**）：
   - `Create(volume_id, name)`：分配 `/var/lib/cds/volumes/<volume_id>/`（密文目录）；返回 `private_data={session_dek_ref: <KMS wrapped key id>}`（≤1024B；volume_id→session 映射在 CDS DB，插件不落业务状态）。**CDS 控制面把 Create 返回的 `private_data` 持久化进自有 DB（session→volume 映射），并在每次 Create 请求（含 pause-resume/重建）组装 `plugin-volume-sources` 注解时携带——§2.4；这是直驱模式下 private_data 的唯一来源（无 CubeMaster 中转）**
   - `Destroy`：crypto-erase（DEK 覆盖写 → 删文件）→ 审计
3. **数据写入路径**（CDS 内部扩展 API，非上游协议）：插件暴露 `PutVolumeData`（append-only，mTLS）；CDS 数据目录快照密文 → 节点插件 → SM4-GCM at-rest 加密落盘（**libtongsuo 链接 helper 路径，§2.9**：helper 读 stdin 的密钥/明文做 `EVP_sm4_gcm` 加解密，subprocess timeout + 失败冒泡——M-01 真实消费者，由 M-01 原 `tongsuo enc` CLI 计划修订而来（CLI 不支持 AEAD）；生产配置 `CDS_SM4_AEAD_BACKEND=tongsuo`，缺 helper 或自检失败 → 插件启动失败 fail-closed，不静默回退 AES）
4. **Node 侧**（`Attach`/`Detach`）：
   - `Attach`：入参 `private_data` 来自 CDS 注入的 `plugin-volume-sources` 注解（§2.4）；校验 `host_path` 位于 `volume_base_dir`（上游硬约束自证）；取 DEK：插件 → CDS KMS（mTLS，session-scoped，TTL，复用 `distribute_key` 语义）→ 解密生成明文库 → `host_path` 指向明文库（virtiofs RO 入 VM）；密文库与明文库分目录
   - `Detach`：DEK 内存 zeroize；`ref_count=0` → 会话终止路径触发 shred（`Destroy`）
   - KMS 调用失败 → **fail-closed**（Attach 拒绝，沙箱创建失败并审计）
5. 会话集成：CB-01 `provision` 触发卷生命周期（Create → PutVolumeData → Create 请求携带 plugin volume；teardown：Detach → Destroy）；结果卷**不经**插件（host_dir 直挂，见 3.2 切分依据）
6. 安全：插件 socket `root:cds 0600`；`private_data` 无明文密钥；合规披露：`compliance_report` 增加 `data_volume_at_rest: "SM4-GCM(tongsuo)"` 字段（M-01 计划第 4 步落点）

**测试**（`tests/test_cds_volume_plugin.py`）：
- gRPC fake 客户端：Create/Attach/Detach/Destroy 全路径 + refcount 语义 + `host_path` 越界拒绝
- KMS 失败 → Attach 拒绝（fail-closed）
- at-rest 断言：写入含特征明文 → 宿主密文目录 `strings` 无特征明文；helper 自检用例（加解密往返 + tag 校验，§2.9）标 `@pytest.mark.slow` + 环境门禁（无 helper 时如实 skip 并 CI 登记）

**验收命令**：`pytest tests/test_cds_volume_plugin.py -v`；真实节点 e2e（100MB 数据卷 → 沙箱内 exec 读明文成功 → 宿主密文断言 → Destroy → 磁盘擦除验证）
**工时**：9 人日（含 SM4-GCM helper + Tongsuo 构建链 1 人日，§2.9）

---

#### CB-04 · Egress 策略编译器（退役 661 行 iptables；L7 闭环）

**目标**：SS-02 策略包 → `CubeNetworkConfig`；默认拒绝；凭据注入下沉网络层；运行中热换。

**代码现状**：`network_policy.py`（netns+iptables+DNS proxy，661L，L0 用）；`policy_compiler.py`/`policy_evaluator.py`（OPA 目标已有）；`app_credentials` + `/gateway/credentials`（应用层凭据托管，现有）。

**实施步骤**：

1. `policy_compiler` 新增输出目标 `cube_network`（与既有 OPA 目标并存）：
   - 恒设 `allow_internet_access=false`（默认拒绝基线）
   - `allow_out`：策略包"外部服务白名单"（ip/cidr/dns 直通）
   - `rules[]`：契约级域名/路径/方法规则（AND 匹配 + first-match-wins = 策略包优先级，编译期保序）
   - 凭据注入：`app_credentials` → `action.inject[{header:"Authorization", secret:<凭据值>, format:"Bearer {{secret}}"}]`（**实施时核实 `format` 字段语义**，上游 `EgressRuleInject.format`）
   - 输出：`CubeNetworkConfig` dict → CB-01 序列化为 proto
2. **热换**：契约变更/会话中权限调整 → CB-01 `Update(CubeNetworkConfig)`（完整期望态语义：省略字段=清除——编译器必须始终输出完整态，禁止增量）
3. `network_policy.py` 分支：level=`l2c` 不走 netns/iptables 路径；L0 路径原样保留
4. **CA bake**：CDS 场景镜像构建链（现有 Dockerfile 体系）增加 CubeEgress 根 CA 注入（CB-02 节点 bring-up 产出 CA 工件 → 镜像构建参数）；模板 probe 阶段校验 CA 在场（缺失 → 模板构建失败，诚实错误）
5. 映射表（`docs/` 新文档）：策略包字段 → `CubeNetworkConfig` 字段全量对照，验收与联调引用该表

**测试**（`tests/test_policy_compile_cube.py`）：
- 策略包样例 → 期望 proto 字段级断言：默认拒绝、白名单、注入、优先级保序
- 完整态不变式：任意两次编译输出字段集合一致（无增量漏配）
- L0/L2' 分支：l2c 会话不产生 iptables 调用（断言无 netns 副作用）

**验收命令**：`pytest tests/test_policy_compile_cube.py -v`；真实节点 e2e（白名单外域名 → 403 + JSONL `security_event`；白名单内+注入凭据 → 上游 200 且沙箱内 `env`/`/proc` 无凭据明文）
**工时**：4 人日

---

#### CB-05 · 模板管线（镜像 → artifact → 节点分发）

**目标**：CDS 场景镜像可构建为 cube 模板并分发到任意节点；会话创建获得"模板本地性"调度约束（天然优于现有加权随机）。

**代码现状**：CDS 场景镜像（Round 40 会话模板 = JSON 配置型 + 既有 Dockerfile 体系）；CDS MinIO（compose 现有）；上游模板链路 = `CubeTemplateCenter`（依赖 CubeMaster，不可直接部署）+ 节点 `AppSnapshot`/`Images` 原语。

**实施步骤**：

1. **模板注册表**（迁移 m2）：`cube_templates`（id、name、version、artifact_id、sha256、size_bytes、image_ref、status：BUILDING/DISTRIBUTING/READY、created_by、created_at）+ `cube_template_nodes`（template_id、node_id、status：PENDING/DISTRIBUTING/READY/FAILED、artifact_sha256、last_error、updated_at）
2. **构建**（异步任务，复用 W11 操作模型：`?async=true` → 202+operation_id + 进度）：
   - 镜像 → rootfs ext4：**实施时 spike（M1 内完成）**——核实上游 TC 构建链（`CubeTemplateCenter/pkg/build`、`pkg/image`）转换脚本可复用性；不可复用 → 自建转换脚本（docker export → ext4 → fsck → probe 配置）
   - **回退路径（不违反红线 2）**：在独立**构建机**（非计算节点）部署 TC 构建链作为 build tool——构建工具不进入运行时路径
   - 就绪语义对齐 C4：HTTP probe 2xx = READY（probe 配置随模板注册表保存）
   - 快照生成：选一台 READY 节点 → `AppSnapshot(create_request, template.id)` → artifact（节点侧）→ 上传 CDS MinIO → 注册 sha256/size
3. **分发**：CDS 签发 MinIO presigned URL（TTL，`CDS_CUBE_TEMPLATE_SIGNING_KEY`）→ 逐节点 `CreateImage`（注解 `cube.master.rootfs.artifact.{id,url,token,sha256,size_bytes,job_id}`）→ `ListImages` 校验 sha256 → 节点状态 READY；sha 不符 → FAILED + critical 告警（不降级）
4. **调度约束**：CB-01 选点 = 模板在该节点 READY 的节点集合（无匹配 → 503 `NO_NODE_TEMPLATE`，W2 契约）
5. **GC**：模板删除 → 逐节点 `DestroyImage`；Conflict（in-use）→ 延迟至下轮 GC（上游幂等语义，`isIgnorableArtifactDeleteError` 同款处理）
6. CDS 既有"会话模板"（JSON 型，Round 40）映射：JSON 模板声明场景镜像 + 配置 → CB-05 artifact；`template` 字段接受 CDS template_id，内部解析 artifactID

**测试**（`tests/test_cube_template_registry.py`、`tests/test_cube_template_dist.py`）：
- 注册表状态机全迁移（含 FAILED 重试）
- fake gRPC：分发顺序、sha 不符 → FAILED、Conflict → 延迟、presigned URL TTL 过期 → 重试
- 调度约束：无 READY 节点 → 503 `NO_NODE_TEMPLATE`

**验收命令**：`pytest tests/test_cube_template_*.py -v`；真实环境 e2e（场景镜像 → 构建 → 双节点分发 → 各节点建会话 → READY；模板删除 → in-use 保护验证）
**工时**：8 人日（转换脚本为最大不确定项，M1 内 spike 收敛）

---

#### CB-06 · 审计尾随（强制点 JSONL → ClickHouse → Merkle）

**目标**：CubeEgress 强制点审计进入 CDS 审计链（ClickHouse + SM3 Merkle + 链上锚定），不可丢弃。

**代码现状**：W13 `egress_audit.py`（应用层，保留）；`clickhouse-init.sql` 规范 schema（`cds_audit.audit_events` ~40 列，7 年 TTL）；`merkle_service`（同步路径，现有）；`merkle_pipeline` 为死代码（C3，**不得**启用）；W12 janitor（audit retention=0 永留）。

**实施步骤**：

1. **tailer 形态**：节点侧小进程（Python，`deploy/cube-node/` 交付，systemd）——watch `/data/log/cube-egress/access.jsonl`（`CDS_CUBE_AUDIT_DIR`），offset 水位线持久化（本地文件），HTTP push → CDS `POST /internal/audit/egress`（mTLS 服务证书）
   - 幂等键：`node_id + file_name + offset + line_no`（CDS 端去重表/唯一约束）
   - at-least-once：push 失败 → 本地磁盘队列（有界，如 10000 行）→ **溢出 = critical 告警 + 停写保护（不静默丢弃，红线 5）**
   - tailer 崩溃重启 → offset 水位续读（无丢失；重读由幂等键吸收）
2. **schema 映射**：入 `cds_audit.audit_events`（规范 40 列，**不新建表**）：`source='cube_egress'`、`event_type`∈{access, security_event, tls_handshake}、host/path/verdict/rule_id/credential_id 对现有列；cube 特有字段（sni、tls 结果、upstream 失败）入 `detail` JSON
3. **防篡改**：入 ClickHouse 后走 `merkle_service` 同步路径 → 周期链上锚定（现有链流程，FG-005 环境项）
4. CDS 端点：`/internal/audit/egress`（仅 mTLS 服务证书可达；批量接收；去重后落库 + 打点 `cds_cube_egress_lines_total`）
5. W13 查询端点不变（应用层审计）；网络层审计查询走既有 audit 路由（`source` 过滤）

**测试**（`tests/test_cube_audit_tailer.py`、`tests/test_cube_audit_schema.py`）：
- offset 续读（中途 kill → 重启 → 无丢失无重复）
- 幂等键去重（重放同批 → 0 新增）
- 溢出告警（灌入超过有界队列 → critical 告警存在 + 无静默丢弃）
- 40 列映射字段级断言

**验收命令**：`pytest tests/test_cube_audit_*.py -v`；真实节点 e2e（触发 10 条 egress 事件 → ClickHouse 10 条 → Merkle 根含对应 leaf）
**工时**：4 人日

### P2 · 路线图（立项另估）

| # | 方向 | 内容 | 前置 |
|---|---|---|---|
| CB-07 | 跨节点 pause/resume（S3 backend） | `AppSnapshot backend=s3` + CDS MinIO；auto_resume 跨节点（W9 唤醒链 + CB-01 调度）；收口 round3 N13 | 节点数据面版本支持 S3（v0.7 preview → 稳定） |
| CB-08 | 节点排空 + 会话迁移 | isolate → 逐会话 pause → 目标节点 resume → 旧节点 Destroy；W18 差异化项（单控制面=天然优势） | CB-07 |
| CB-09 | GPU | 对齐 FG-002；上游 GPU sandbox 能力落地后接入（训练/推理场景） | 上游 + 硬件 |
| CB-10 | 上游回馈（PR 非 fork） | cds-volume-plugin 参考实现、egress 热换用例、"无控制面直驱节点"部署文档 | M3 稳定后 |
| CB-11 | L0/L2' 功能平价审计 | streaming（视 CB-01 spike 结论）、共享卷 L2' 版（= 插件卷 refcount>1，上游 refcount 语义已就绪，CDS `shared_volumes` 表映射） | CB-03 |

---

## 五、上游同步与独立开发隔离（git 工作流）

> 本章回答两个问题：① 如何实时跟紧 CubeSandbox 上游变更而不被阻塞；② 为什么对上游仓库的任何操作（fetch / reset / clean）都不影响 CDS 独立开发。

### 5.1 三层边界模型（两个仓库 + 一个产物）

| 层 | 是什么 | 承载什么 | 红线 |
|---|---|---|---|
| **上游裸镜像**（`/opt/cds/cube-mirror.git`，bare） | 上游代码的唯一合法来源 | CubeSandbox 全部源码 / proto / deploy | 无工作树；除 upstream 引用外不建本地 ref |
| **上游干净工作副本**（`~/Documents/workspace/CubeSandbox`） | 开发阅读 / 本地冒烟 | **什么都不承载**（它不是开发仓库） | **零本地 commit、零 tracked 文件修改**（红线 7）；唯一写入 = fetch / `reset --hard <upstream tag\|sha>` |
| **CDS 仓库**（`sandbox/`） | 独立开发唯一仓库 | CB-00..CB-06 全部产出：客户端、插件、编译器、tailer、部署脚本、stubs、`_VERSION.json` | 全部业务代码在此，**永不**进上游仓库 |

节点部署 bundle（install wrapper + 插件二进制 + tailer + CA 工件）= CDS 仓库的**构建产物**（`deploy/cube-node/`），以文件/OCI 镜像分发到节点——不构成第三个仓库，也永不进上游仓库。

### 5.2 版本钉住

- 钉住单位 = **上游 git sha**（不是分支、不是 "latest"）；`app/_stubs/cube/_VERSION.json` 记录 `{cube_version（tag 人读标注）, git_sha, proto_hash, stub_hash, generated_at}`
- 一切 proto / 资产提取走镜像的 `git archive <sha>`（5.1）——不依赖工作副本状态，工作副本停在哪个 ref 都无所谓
- 节点侧 `sandbox_nodes.runtime_version` 记录节点实际运行版本；CDS 客户端拒绝调度到与 pin 不匹配的节点（CB-01 选点过滤）

### 5.3 实时跟紧机制（upstream watch）

“实时跟紧” = **契约面实时感知 + 测试门禁的 pin 推进**，不是盲目拉最新：

```
weekly / 手动触发 ──> cube-upstream-check.sh
  ├─ git fetch（裸镜像）
  ├─ 枚举 pinned sha 之后的新 tag / commit
  ├─ 契约 diff（路径集 = scripts/cube-contract-paths.txt，版本化配置，变更需评审）：
  │    pkgs/proto/services/{cubebox,volumeplugin,images,errorcode}
  │    pkgs/proto/types/**
  │    CubeMaster/pkg/base/constants.go        # 注解集合（pause/appsnapshot/artifact）
  │    Cubelet/storage/pluginvolume.go         # plugin-volume-sources 注解契约（v1.1 增列，§2.4）
  │    CubeEgress/lua/audit.lua                # 审计 schema / 默认路径
  │    deploy/one-click/{install*.sh,systemd/} # 节点形态（compute role）
  │
  ├─ 契约面未变 ──> 自动跟进流：
  │    临时目录重生成 stubs → 全量回归（fake gRPC 测试集）→ 绿 →
  │    自动开 "pin 更新" PR（_VERSION.json + stubs，人合入）
  │
  └─ 契约面已变 ──> CI 评论：breaking 候选清单（文件 + hunk）
       → 人工评审（对照 §二 契约基线逐条核，回写本文件）
       → 回归通过才推 pin；否则继续钉旧 sha
          （上游继续演进，我们只是这次不冒险，无损失）
```

- 安全版本（上游 SECURITY release）**豁免等待**：48h 内强制走跟进流（对齐 CB-02 安全补丁强制流）
- 钉住期间上游的新功能不阻塞 CDS 开发：CDS 侧任务只依赖已钉契约面

### 5.4 独立开发隔离保证（结构性回答）

**上游 pull 为什么永远不影响我们：业务代码在上游仓库里不存在。** 对上游仓库的任何操作（fetch / `reset --hard` / `clean`）只能触及上游自己的文件。与上游仅有的两个耦合面都是“可再生 / 可重放”的：

| 耦合面 | 形态 | 处置 |
|---|---|---|
| gRPC stubs（`app/_stubs/cube/`） | 由 pinned sha 生成 | `git archive` + protoc 可再生（CB-00 步骤 2）；CI 哈希门禁（stub_hash vs `_VERSION.json`） |
| 节点侧 stock 配置增量注册（`volume_plugins` 段 / 审计路径 / host_dir 前缀） | CB-02 install 脚本对节点 stock 配置文件的增量 | 脚本**幂等**（可重复执行；节点重装 / 上游升级后重跑 `deploy/cube-node/install.sh` 即恢复）；“改了哪些 stock 配置文件、各几行”的完整 diff 清单归档 runbook（红线 1 之“diff 清单留档”落地处） |

即：最坏情况 = **pin 不推进**（能力冻结、等待修复），而不是 = 独立开发损坏。

### 5.5 日常开发工作流

| 动作 | 命令 / 路径 |
|---|---|
| 查上游有无更新 | `scripts/cube-upstream-check.sh`（fetch + 契约 diff + 报告） |
| 跟进（契约面未变） | CI 自动 PR；或手动：更新 `_VERSION.json` → `gen-cube-stubs.sh` → 全量回归 → 节点滚动（CB-02 SOP） |
| 跟进（契约面已变） | 人工评审 breaking 清单 → 回写 §二 → 客户端适配（如需）→ 同上 |
| 钉住等待 | 无操作（weekly check 持续跑；契约变更告警不消失，直到人工处置） |
| 上游本地 hack / 试改 | `git clone <mirror> /tmp/cube-exp-$$` 一次性临时克隆；**永不**触碰干净工作副本 |
| 节点升级 | CB-02 runbook SOP（isolate → 排空 → 换 bundle → verify → unisolate） |

### 5.6 工具清单（全部位于 CDS 仓库）

| 文件 | 职责 | 任务 |
|---|---|---|
| `scripts/gen-cube-stubs.sh` | 镜像 → `git archive` → protoc → `app/_stubs/cube/` + 哈希回写 | CB-00 |
| `scripts/cube-upstream-check.sh` | fetch + 契约 diff + 报告（exit 0/1/2 语义） | CB-00 |
| `scripts/cube-contract-paths.txt` | 契约 diff 路径集（版本化配置） | CB-00 |
| `.github/workflows/cube-upstream-watch.yml` | weekly + 手动触发；自动跟进 PR / breaking 评论 | CB-00 |
| `app/_stubs/cube/_VERSION.json` | 钉住记录（sha + 哈希） | CB-00 |
| `deploy/cube-node/` | 节点 bundle（preflight/install/verify/tailer/插件打包） | CB-02 |

---

## 六、横切面

### 6.1 配置键矩阵（新增，四处同步：`.env.example` / `docker-compose.yml` / `docker-compose.prod.yml` / `helm/cds/values.yaml`）

| 字段 | 默认 | dev/test | 生产 | 任务 |
|---|---|---|---|---|
| `CDS_CUBE_DATAPLANE_ENABLED` | **False** | false | true（需 ≥1 节点就绪，否则 validate RAISE） | CB-01 |
| `CDS_LEGACY_L2_ENABLED` | **False** | true（dev 观察期） | **false** | CB-01 |
| `CDS_CUBE_NODE_GRPC_TLS` | True | false（本地裸机联调显式关+告警） | **True（mTLS；拨号对象=节点 TLS sidecar，§2.10）** | CB-01 |
| `CDS_CUBE_NODE_TLS_CA` | None | 自建 | 必配（certificate_service 签发） | CB-01 |
| `CDS_CUBE_NODE_TLS_PROXY_PORT` | 9443 | 同 | 同（节点侧 sidecar 监听端口，§2.10） | CB-02 |
| `CDS_CUBE_NODE_GRPC_DEADLINE_MS` | 5000 | 同 | 同（Update 内部 30000） | CB-01 |
| `CDS_CUBE_NODE_HEARTBEAT_SECONDS` | 30 | 同 | 同 | CB-01 |
| `CDS_CUBE_RESULTS_DIR` | `/var/lib/cds/sandbox-results` | 同 | 必配（节点） | CB-01 |
| `CDS_CUBE_AUDIT_DIR` | `/data/log/cube-egress` | 同 | 同（节点） | CB-06 |
| `CDS_CUBE_VOLUME_PLUGIN_SOCKET` | `/run/cds-volume-plugin/cds.sock` | 同 | 同（节点） | CB-03 |
| `CDS_SM4_AEAD_BACKEND` | `aes_fallback` | `aes_fallback` | **`tongsuo`（缺 helper 或自检失败 → 插件启动失败，§2.9）** | CB-03 |
| `CDS_CUBE_TEMPLATE_SIGNING_KEY` | None | dev key | 必配 | CB-05 |

原则：延续既有纪律——**新开关安全默认，宽松只在显式配置处**；生产未接线 → `validate_security_config` RAISE（对齐 `HSM_SOFTWARE_FALLBACK_ALLOWED` 纪律）。

### 6.2 Alembic 迁移清单（依赖顺序，全 additive 可空/带默认）

| 迁移 | 内容 |
|---|---|
| m1 | `sandbox_nodes` +`cubelet_endpoint`(Text, null) +`runtime_version`(Text, null) +`data_plane_health`(String(16), default "unknown")——与 W14 `health_state` 关系实施时核实（复用或新列，以代码为准回写） |
| m2 | `cube_templates` + `cube_template_nodes`（CB-05 字段清单） |
| m3 | `sandbox_sessions` +`runtime_ref`(Text, null)（sandbox_id）；`sandbox_level` 枚举扩展 `l2c`（如模型层枚举足够则免列，核实后回写） |
| m4 | 审计幂等键去重：**采用 PG 去重表路线**（新表 `cube_egress_dedup(node_id, file_name, offset, line_no)` + 唯一约束，additive，alembic）；现有 `audit_events` 为 MergeTree（非 ReplacingMergeTree，`scripts/clickhouse-init.sql`），引擎迁移触碰存量数据，**不在本 spec 范围**（将来如需 CH 侧去重另立决策项） |

命令纪律不变：`alembic revision --autogenerate` → 人工核对（**禁止直接信任 autogenerate**）→ `alembic upgrade head` → `python scripts/verify_schema_parity.py`。

### 6.3 测试与验证协议（每任务 DoD 模板）

1. **新测试全绿**：`pytest tests/test_cube_*.py tests/test_cds_volume_plugin.py -v`（含任务列出的每条用例）
2. **受影响回归绿**：`test_session_lifecycle / test_sandbox_sessions / test_snapshots / test_egress_audit / test_node_operations / test_shared_volumes / test_policy_*` 通过数不低于基线
3. **编译绿**：`python -m compileall -q app tests alembic`
4. **配置四同步**：6.1 新键出现于四处
5. **诚实性**：任何降级/模拟路径 `logger.warning` + （安全姿态相关）`validate_security_config` 覆盖；L2' 不可用时的拒绝是 4xx + 精确 code，不静默落 L0
6. **stub 钉住**：`app/_stubs/cube/_VERSION.json` 与生成物哈希一致（CI 门禁）
7. **节点 e2e**：`@pytest.mark.slow` + 环境门禁（`CDS_CUBE_E2E_NODE=host:port` 未设则 skip 并 CI 登记）；CI 只跑 fake gRPC；真实节点归 **FG-017（环境验收项：节点数据面 bring-up + 全链路 e2e）**
8. **禁项自检**：无新增裸 `except: pass`；未删除既有测试；未放宽任何 W2/T5 安全门禁；**未修改任何 CubeSandbox 源文件**（红线 1，CI 加一项：`git -C <cube-repo> status --porcelain` 为空，纳入联调脚本）

### 6.4 发布顺序与依赖

```
CB-00 (上游同步 / 契约监视 —— 前置)
CB-01 (client + adapter)
  → CB-02 (节点 bring-up；CB-01 真实冒烟依赖它)
  → CB-03 (volume)  ∥  CB-04 (egress 编译)     [均仅依赖 CB-01 proto/客户端]
  → CB-05 (模板；依赖 CB-01 选点 + MinIO 现有)
  → CB-06 (审计尾随；依赖 CB-02 节点形态 + ClickHouse 现有)
  → [P1 收口：全量回归 + FG-017 环境验收 + 安全补丁流演练一次]
  → P2 立项（CB-07..11）
```

每项独立 PR，revert 边界干净；CB-01 是公共依赖必须先行合入；CB-05 的转换脚本 spike 在 M1 内完成（不等 CB-01 全量）。

### 6.5 里程碑与工时

| 里程碑 | 内容 | 时点 | 工时 |
|---|---|---|---|
| M1 | CB-00 + CB-01 + CB-02：上游契约监视上线（weekly CI + 首次基线报告）；L2' 在真实节点可 创建/执行/销毁；流式 spike 结论回写；模板转换脚本 spike 结论回写 | 第 1–3 周 | 10 人日 |
| M2 | CB-03 + CB-04：数据进出闭环（加密卷 + egress 默认拒绝 + 注入 + 热换） | 第 3–6 周 | 13 人日 |
| M3 | CB-05 + CB-06：模板闭环 + 强制点审计入链；P1 收口回归 + FG-017 | 第 6–8 周 | 12 人日 |
| P2 | CB-07..11 | 第 2 月起 | 另估 |

### 6.6 风险表

| 风险 | 影响 | 缓解 |
|---|---|---|
| `CubeboxMgr` 为内部契约，上游升级可能 breaking | CDS 客户端故障 | 契约面实时感知（CB-00 watch，weekly + 手动）；版本钉住（`runtime_version` + `_VERSION.json`）+ 每版本冒烟；最坏钉版本等待（运维债，非代码债）；订阅上游 release，安全补丁 48h 强制流 |
| envd 宿主可达性未验证 | L2' 流式不可用 | CB-01 day-1 spike；诚实回退（同步 exec + /logs，对齐 W10 纪律）；结论回写 2.x |
| 模板转换脚本（docker→ext4）不确定 | CB-05 延期 | M1 内 spike 收敛；回退=独立构建机跑 TC 构建链（构建工具，不违反红线 2） |
| M-01 原计划 `tongsuo enc` CLI 不支持 AEAD（上游源码核实） | CB-03 若按旧方案实现则生产配置直接失效 | §2.9 改 libtongsuo 链接 helper；helper 自检为插件启动硬门禁；M-01/MR-A1 同步回写（v1.1 已裁决，留作执行边界） |
| KVM/XFS/eBPF 环境未就绪 | 节点 bring-up 失败 | CB-02 preflight 硬门禁 + FG-017 环境验收 |
| mTLS（CDS↔cubelet/插件）证书运营 | 新攻击面/运营负担 | 复用 `certificate_service` CA 体系；节点纳管=签发+注册；吊销流程入 runbook；**cubelet 侧不会说 TLS（§2.10），mTLS 由节点 TLS sidecar（CDS 组件，可独立升级）实现** |
| 结果卷 at-rest 明文 | 宿主拖盘=结果泄露 | 3.2 安全切分依据 + 结果目录宿主权限 + CDS 主机基线；合规要求加密 → CB-03 插件 RW 模式（登记） |
| 审计 tailer 节点侧单点 | 审计丢失 | 本地有界队列 + 溢出 critical 告警 + 红线 5（禁丢弃）；幂等键吸收重放 |
| 上游安全补丁滞后 | 风险窗口 | 订阅上游；安全版本强制滚动（runbook 豁免等待窗口） |
| L0/L2' 功能碎片化（streaming/共享卷） | 体验不一致 | CB-11 平价审计；SDK/前端功能矩阵标注 |
| 弃用 firecracker/k8s 路径后存量依赖 | 未知调用方 | `CDS_LEGACY_L2_ENABLED` 观察期（两版本）+ 访问打点（使用计数入 /metrics） |

---

## 七、执行边界声明

1. 本 spec 只覆盖**软件层 + 节点 bring-up 环境验收登记（FG-017）**。真实 TEE 硬件、GPU、HSM、真链 e2e 按既有 FG-001..FG-016 验收轨道跟踪，不在本 spec 范围伪造完成。
2. **节点数据面零 diff 为第一红线**：执行 agent 不得修改 CubeSandbox 源码、不得编译定制二进制、不得 patch stock 文件。节点 CDS 组件（`cds-volume-plugin` / TLS sidecar / 审计 tailer）均为独立进程、独立 systemd 单元、独立包，其存在不构成 Cube diff；节点上 stock 配置文件的**增量注册**（`volume_plugins` 段、审计路径、host_dir 前缀、sidecar 证书）由 CB-02 脚本完成并留档 diff 清单。
3. 不部署 CubeAPI / CubeMaster / CubeOps / WebUI / TemplateCenter / CubeProxy / Cube Redis / MySQL（compute 节点）。例外仅 CB-05 回退路径（独立构建机运行 TC 构建链，不进入运行时路径）。
4. 所有"上游契约"描述基于 2026-10-12 源码复核（第二章）；实施时若与现状冲突，以代码为准并回写本文件（沿用前序方案纪律）。
5. 暂停/恢复注解组合（2.6）与 `EgressRuleInject.format` 语义（CB-04）在实施前必须对照上游代码核实并回写。
6. 每轮实施完成后，在下方“执行记录”追加：任务 | 状态 | 关键产出 | 新增测试与结果 | 回归结论 | 未实施项。
7. 本 spec 的 git 工作流遵循 §五：上游仓库零本地（红线 7）、sha 钉住、跟进走 CB-00 契约监视。执行 agent **不得**在 CubeSandbox 仓库创建任何本地 commit，不得把任何独立开发代码提交进该仓库。
8. **CB-03 的 SM4-GCM 必须走 §2.9 helper 路径**；禁止以任何 `enc` 类 CLI（Tongsuo/OpenSSL）实现 AEAD（上游 `enc` 一律拒绝 AEAD 密码，已源码核实）；M-01/MR-A1 计划按 §2.9 回写。

---

## 八、执行记录

### M1+M2 软件侧实施（2026-10-13，父任务审计制）

上游钉住：CubeSandbox @ 9b75d33907 (v0.7.3-rc2)，执行全程零 diff（每节点审计 `git status --porcelain` 亲验）。
环境：grpcio 1.84.0 装入 CDS venv（requirements.txt:68 钉住，grpcio-tools 仅构建期）；protoc 27.3（/home/kratos/bin）；本机无 XFS 工具/无 CAP_SYS_ADMIN（见各节点未实施项）。

| 任务 | 状态 | 关键产出 | 新增测试与结果 | 回归结论 | 未实施项 |
|------|------|----------|----------------|----------|----------|
| CB-00 stubs+watch | ✅ | app/_stubs/cube/（pb2+pb2_grpc，5.26 生成）；scripts/check_cube_upstream.sh 契约监视；上游版本钉住 9b75d33907 | test_cube_upstream_check 绿 | 见全量回归行 | — |
| CB-01 节点客户端+L2' 适配器 | ✅ | app/services/cube_node_client.py（map_ret_code:78 对 63 个 ret_code total 枚举、15 RPC 包装、CubeNodeClientPool:318、健康探测:392 挂入既有节点健康流）；cube_runtime_adapter.py（L2' 适配，select_cube_node:105）；SandboxLevel.L2C + 注册挂 CDS_CUBE_DATAPLANE_ENABLED（sandbox_runtime.py:1820-1832，默认 false）；create 门 400 RUNTIME_UNAVAILABLE（sandbox_sessions.py:375-393）；mTLS PEM 走 CDS_CUBE_NODE_TLS_*（无材料 fail-closed，禁回退明文） | test_cube_node_client 11 + test_cube_runtime_adapter 7；限定 6 文件 79 绿 | 见全量回归行 | 真 TLS 对活节点；envd streaming 可达分支 |
| CB-02 节点工具链 | ✅ | deploy/cube-node/（preflight/install/verify/tls-proxy + audit-tailer.service 占位）；增量注册留档 diff 清单 | 脚本门测试绿 | 见全量回归行 | 真节点 bring-up（FG-017） |
| CB-03 CDS 卷插件 | ✅ | cds-volume-plugin/（Go plugin + CDS 服务侧）；libtongsuo C helper（§2.9 路径，SM4-GCM AEAD 走 EVP_CTRL_* 3.x 惯例 AAD 喂入，禁 enc CLI）；KMS 控制面 3 路由（api/kms.py：register/distribute/revoke，服务级 token fail-closed） | test_kms_volume_keys 等绿 | 见全量回归行 | 节点上真 XFS 卷挂载（FG-017） |
| CB-04 egress 编译器 | ✅（经 1 轮父审计修复） | policy_compiler.py cube_network target（:130，full-state 不变式自检:280）；默认 deny 硬编码；app_credentials 凭据注入（SM3 哈希源，不可解析跳过并披露，禁伪造）；l2c 分支跳过 netns/iptables（network_policy.py，L0 路径字节级回归）；热换 = Update(CubeNetworkConfig) 完整态；docs/cube-egress-mapping.md | test_policy_compile_cube 17（含第二会话 wipe 回归 test_engine_does_not_wipe_uncommitted_caller_rows） | 见全量回归行 | docker build CA 烘焙（无 docker 运行） |
| CB-05 模板管线 | ✅ | 迁移 0014（cube_templates/cube_template_nodes，内联 UniqueConstraint）；app/api/cube_templates.py（W11 202 异步构建 + GC）；MinIO presigned URL + 6 注解分发 + sha256 校验 + 失配 critical 告警；select_cube_node 模板 locality + 503 NO_NODE_TEMPLATE；scripts/cube-rootfs-convert.sh（TC 构建链判定不可复用，独立脚本，硬失败 exit 3/4） | test_cube_template_registry 7 + test_cube_template_dist 7 | 见全量回归行 | rootfs 转换 populate/e2fsck 段（本机无 CAP_SYS_ADMIN，mkfs.ext4 前段已实跑）；provision 换镜像 1 行（登记） |
| CB-06 审计尾随 | ✅ | deploy/cube-node/audit_tailer.py（水位双指针 offset/pending_offset、inode 轮转、断行/坏行不丢、溢出停写+标记+重读去重）；POST /internal/audit/egress（api/cube_egress.py:33，CDS_CUBE_AUDIT_SERVICE_TOKEN fail-closed）；迁移 0015 去重表 (node_id,file_name,offset,line_no)；43 列映射 + /audit ?source=cube_egress 过滤；Merkle 走 /audit/anchor 同步链（逐批锚定+blockchain_tx_hash 戳行）；metric cds_cube_egress_lines_total | test_cube_audit_tailer 9 + test_cube_audit_schema 5；-k audit 波及 191 绿 | 见全量回归行 | 对活节点实跑 tailer（无节点环境）；install.sh 打包 audit_tailer.py 未验 |

**§2.6/§7.5 实施核实回写**：`EgressRuleInject.format` 为纯字符串模板，上游 render_inject（CubeEgress/lua/access_phase.lua:168-195）替换**字面量 `${SECRET}`**（spec 示例 `Bearer {{secret}}` 不匹配，编译器按 `Bearer ${SECRET}` 产出，详见 docs/cube-egress-mapping.md §3）。暂停/恢复注解组合按 §2.6 现状（UpdateCubeSandboxResponse.ext_info）核实。

**spec 沉默处裁定**（已实现，留档）：① 节点 cubelet_endpoint/runtime_version 暂存 SandboxNode.capabilities JSON（无 m1 列，免迁移）；② m3 runtime_ref 未建 → container_id = l2c-<node>-<sandbox> 编码节点 locality；③ CDS_DEK_HEX 由 CDS 生成 hex 注入 Create（真 KMS 包裹 DEK 经 CB-03 控制面）；④ 凭据注入源 = contracts.terms["credentials"] 联接有效 app_credentials（库中仅存 SM3 哈希，无明文）；⑤ Merkle 逐批同步锚定（既有无周期性 Merkle 循环，批次管线 C3 为死代码）。

**CB-04 缺陷与修复（父任务审计记录）**：初版 `_session_is_l2c` 在 create_policy 内自开第二 DB 会话——测试环境（conftest 单连接 in-memory SQLite 共享）关闭时回滚请求会话未提交事务（e2e 3×401 wipe）；生产（连接池）则永远查不到未提交 session 行导致 l2c 分支失效。修复：create_policy/remove_policy 增加 db 参数（11 个调用点全部透传请求会话），_session_is_l2c 不再开任何会话；回归 test_e2e_flows 42/42（修复前 39/3）。

**全量回归**（2026-10-13 定讫）：**2758 passed / 0 failed / 3 skipped（724.03s，排除 `tests/test_e2e_full_lifecycle.py` 与 CI 同口径）**——零失败，pass 增量 +104（基线 2654/0/3，2026-10-13 known-failures 行）全部为 CB-00..06 新增测试；3 项 skip 与基线同集。中间轮次（v3，2756/2/3）的 2 个失败为 `tests/test_sandbox_sessions.py` 两处测试 fake 未接 `create_policy/remove_policy` 新 `db` 参数（CB-04 签名变更波及，测试侧修复后全绿）。详见 ci/known-failures.md 2026-10-13 SS-10 行。
