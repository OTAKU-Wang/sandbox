# SS-16：Cube 节点集群的 K8s 交付形态（隔离面不变 + 编排/交付面 K8s 化）

> 版本：v1.0 | 分系统编号：SS-16 | 日期：2026-10-16
> 上游：SS-13（MicroVM 单路径归一，§三非目标「不引入 k8s 依赖」）、SS-10（CB-01..06 Cube 数据面）、`specs/productization-deployment-runbook.md` §9（Cube 节点部署与升级）
> 决策背景（2026-10-16 选型复核）：用户问「是否改用 docker+k8s 更符合中国可信数据空间对沙箱的要求」。复核结论是**分层**——隔离面（沙箱本身用什么关数据）**不换**，仍是 MicroVM；编排/交付面（节点集群怎么拉起、升级、观测）**要 K8s 化**。本 spec 落地后半句，**不重开前半句**。
> 编号告示：SS-14 的载体是 `.omo/plans/ss14-e2e-real-env.md`（实施计划，非 spec 契约）；`specs/` 下 SS-16 号此前无任何占用。

## 一、问题与不变量

### 1.1 问题（三条，均为实测事实）

1. **交付断层**：CDS 平台侧有两条 K8s 路径（`k8s/*.yaml` 的 k3s 路径、`helm/cds` 的生产路径），但**沙箱节点在 K8s 面上是零**——`k8s/` 与 `helm/` 全量 grep `runtimeClass|kata|gvisor|kvm|privileged` **0 命中**。今天 Cube 计算节点只能由 systemd 脚本交付（`deploy/cube-node/install.sh:85`）。结果：「平台能上 K8s，沙箱不能上 K8s」。
2. **硬件现实**：目标环境以虚拟化云为主（实测远程交付机为 OpenStack Nova 虚机，`modprobe kvm_amd` 不支持、无 `/dev/kvm`、无带外）。拿不到 KVM 的机器**不能**变成跑容器的理由（见 I-3），只能变成"这台机器不承载密态数据面"的判定；而 K8s 化能显著降低"合格节点"的交付与滚动成本，把裸金属/嵌套机器当**节点池**管理。
3. **运维形态不匹配**：`k8s/deploy.sh` 是 9 步 k3s 交付（`[1/9]`..`[9/9]`），客户 SRE 的日常工作面是 `kubectl`/Helm；而沙箱节点侧今天是 `preflight.sh` → `install.sh` → `verify.sh` 三件套人肉跑，节点登记靠 `INSTALL_STATE.json` 人工回读（runbook §9.1）。

### 1.2 三条不变量（先钉死；后文任何一条被违反即判 spec 失效）

| 编号 | 不变量 | 依据 |
|---|---|---|
| **I-1** | **K8s 不是安全边界**。Pod / Namespace / NetworkPolicy / RuntimeClass 一律不得被描述为实现数据隔离或使用控制的机制。隔离边界仍是 hypervisor（KVM MicroVM），使用控制仍走 CB-04（CubeVS/CubeEgress 出口）与 CB-06（审计尾随）。 | `specs/product-security-release-review.md` §6 禁止对外口径；上游 chart 自己写明 hostNetwork 模式下 **NetworkPolicy 不能治理 sandbox 流量**（`values.yaml:790-795`） |
| **I-2** | **SS-13 §三「不引入 k8s 依赖」的语义 = 执行编排不依赖 k8s**：会话→节点的放置仍由 CDS `select_cube_node` 决定（`app/services/cube_runtime_adapter.py:148`），不由 kube-scheduler 决定。本 spec 引入的是**节点集群的交付/升级/观测形态**。这是本 spec 与 SS-13 的关系条款。 | `specs/ss-13-microvm-single-path.md:76-79` |
| **I-3** | **KVM 只有三档，没有第四档**：① 裸金属 / 开嵌套的虚机 → ② PVM（显式 label + 上游明写仅限 dev/eval，非生产）→ ③ 无 KVM = 不打 compute label，会话 400 `RUNTIME_UNAVAILABLE`（现有门）。任何情况下**不得**用打开 `CDS_SANDBOX_LEGACY_ENABLED` 来"救活"一个没有 KVM 的集群。 | 用户指令 2026-10-13；禁伪造/诚实降级铁律；`app/core/config.py:526-538`（生产开 legacy 门直接启动失败） |

### 1.3 规范侧结论（为什么不因为"合规"而改隔离面）

国内技术文件对**数据沙箱**的定义是「构建一个**应用层隔离环境**」，配套要求是使用控制（策略解析、行为校验、环境构建、操作执行、行为记录与存证回传）——验收对象是**控制点与证据链**，未规定必须虚拟化。项目自身的定级也早已给出：`specs/ss-10-cube-dataplane-connector.md:254` 把 `k8s` 档写作「K8s pod（**共享内核**）… 降级（同上门禁）」，`specs/detailed-tech-spec.md:714-722` 给「容器 + seccomp」标注内存加密=无、远程证明=无、适用=低敏/公开。故 SS-16 的范围**排除**任何把 K8s 当密态执行环境的方案（§八）。
（诚实标注：规范定义句取自国家数据局与地方数据局两份同源公开技术文件的一致表述，属技术文件/征求意见稿性质，正式条文号未逐条核对；本 spec 不依赖该句做强制论证，只用于排除"因为合规所以换容器"这一论据。）

## 二、选型：K8s 化的三个候选

| 候选 | 内容 | 判定 |
|---|---|---|
| A | CDS 自己写 4 个 DaemonSet（cubelet/shim/kernel/guest + bootstrap） | **否**：等于 fork 上游交付逻辑，节点组件升级责任从上游转回 CDS，直接违反 runbook §9.0 红线 1（CDS 不 patch、不自补安全补丁） |
| **B** | **采用上游官方 chart，compute-only 模式** + CDS 侧新增自有 chart 与 `cds-cube-agent` DaemonSet | **采用** |
| C | 全量部署上游控制面（CubeMaster/CubeAPI/CubeOps/MySQL/CubeProxy/MinIO/templatecenter…） | **退路**：仅当 KN-00 判否（compute-only 起不来）时启用，且必须登记"新增 3 个有状态件 + 第二套调度语义"的代价 |

### 2.1 证据（全部亲核）

**上游 K8s 形态是一等公民，不是脚本副产品**

| 事实 | 出处 |
|---|---|
| 官方 chart 存在：`CubeSandbox/deploy/kubernetes/chart/`，含 4 个 compute DaemonSet 模板（`node-daemonset.yaml` Big Pod、`node-installer-daemonset.yaml`、`node-bootstrap-daemonset.yaml`、`node-pvm-daemonset.yaml`）与 3 个 preflight Hook（`cube-node-hostnet-preflight.yaml`、`cubevs-cidr-preflight.yaml`、`pvm-startup-gate-preflight.yaml`） | chart `templates/` 清单 |
| 镜像全部钉 `v0.7.3`，且 `global` 提供**私有 registry 改写位点**（国内镜像可行） | `values.yaml:14-16`、`:21-118` |
| **compute-only 是官方模式**：`externalControlPlane`（"Compute-only mode equivalent to one-click `ONE_CLICK_DEPLOY_ROLE=compute`"） | `values.yaml:202-210` |
| 计算节点放置由 nodeSelector `cube.tencent.com/cube-node: "true"` 决定（`placement.compute`，含 `cube.tencent.com/compute:NoSchedule` 容忍），PVM 额外要求 `cube.tencent.com/allow-pvm-bootstrap: "true"`（混布集群安全：无 label 节点不拉 PVM 镜像） | `values.yaml:165-201`（compute `:179-187`） |
| Big Pod：`hostNetwork: true`（默认）、`hostPID: true`、`privileged: true`；上游注释明写 hostNetwork 下 **sandbox tap 跨 Pod 重建存活**，Pod 网络下重建会打断该节点全部 sandbox，且 **cubelet 无认证——"firewall it like kubelet"** | `values.yaml:790-795`、`:1320-1324` |
| cubelet 监听 **9999(gRPC)** / 9998(http) / 9966(debug)；startUp/readiness 探针即 TCP 9999 | `templates/node-daemonset.yaml:76-81`、`values.yaml:867-889`（`tcpSocketPort: 9999` 见 `:870,876`） |
| **Big Pod 无 sidecar 注入位点**：容器清单固定为 `wait-node-prep`(init) / `cubelet` / `cube-egress`，只有 `cubeNode.env` 可注环境变量（无 `extraContainers` 位点） | `templates/node-daemonset.yaml:42,55-57,160`、`values.yaml:858`（`env: []`） |
| 上游把 stock 组件装进**宿主** toolbox 树（hostPath `/usr/local/services/cubetoolbox`、`/data/cubelet`…）——K8s 形态下宿主侧前置检查依然成立 | `values.yaml:1326-1360`（`hostPaths`） |

**CDS 侧对接点无需改动**

| 事实 | 出处 |
|---|---|
| CDS 直连节点 cubelet gRPC，经 mTLS 前置代理 sidecar（`CUBE_NODE_TLS_PROXY_PORT` 默认 **9443**），**不经 CubeMaster/CubeAPI** | `app/services/cube_node_client.py:8`、`app/core/config.py:321` |
| CDS 今天是 compute-only（不装、也不配 CubeMaster；`CUBE_MASTER_ADDR` 默认空） | `deploy/cube-node/install.sh:85`、`app/core/config.py:342` |
| 节点元数据全在 `capabilities` JSON（`cubelet_endpoint` / `runtime_version` / `tee`），专用列 CB-01 时刻意未建 | `app/services/cube_node_client.py:295-309` |
| 放置逻辑 = 版本 pin + 模板 locality + TEE 偏好 + 反热点加权随机 | `app/services/cube_runtime_adapter.py:80-97,148-200` |

### 2.2 选型结论

**B**。理由（按权重）：① 节点组件升级责任留在上游（红线 1 的唯一保持方式）；② CDS 已跑 compute-only，`externalControlPlane` 是其 K8s 等价物，语义不漂移；③ 不新增 MySQL/CubeOps/CubeProxy 等状态件，"不引入 k8s 作为执行编排依赖"（I-2）可守；④ 上游 chart 自带 hostnet/CIDR/PVM 三类 preflight Hook，与 CDS `preflight.sh` 是**互补双闸**而非重复劳动。

**接受的代价（登记）**：Big Pod 是 privileged + hostPID + hostNetwork 的特权负载（§3.4 的缓解措施必做）；`hostNetwork: true` 成为部署前提（否则 mTLS 代理目标与 sandbox 存活都受影响）；上游 chart 依赖外网镜像与 bitnami 子 chart（KN-01 处理镜像改写与离线源）。

## 三、目标架构

```
                ┌──────────────── K8s 集群（客户交付面）─────────────────┐
                │                                                        │
  上游 chart     │  cube-node-installer DS ─┐                             │
  （compute-only）│  cube-node-bootstrap DS ─┼─▶ 宿主 toolbox 树 + XFS/kvm │
                │  cube-node-pvm DS(可选) ─┘         │                   │
                │  cube-node Big Pod (hostNetwork, privileged)           │
                │        └─ cubelet:9999(明文,无认证) / cube-egress       │
                │                                   ▲                    │
  CDS 自有 chart │  cds-cube-agent DS（hostNetwork，CDS 镜像）             │
                │    ├ stunnel mTLS 0.0.0.0:9443 → 127.0.0.1:9999        │
                │    ├ cds-audit-tailer  ├ cds-volume-plugin socket       │
                │    └ preflight/verify/tee_probe → 真探测上报             │
                └───────────────────────────────────┬────────────────────┘
                                                    │ ①节点同步 ②9443 mTLS ③/internal/audit
                              CDS 控制面（helm/cds 或 k8s/*.yaml）
                    节点注册表 sandbox_nodes ── select_cube_node（版本 pin / 模板 / TEE）
```

**分层职责（这张表是本 spec 的核心，也是 runbook §9.0 所有权表在 K8s 形态下的等价）**

| 层 | 交付物 | 升级责任方 | K8s 化了吗 |
|---|---|---|---|
| 编排/交付面 | 上游 chart（compute-only）+ `helm/cds-cube/`（新）+ `k8s/cube-node.yaml`（k3s 等价物） | 上游 chart 部分=上游；包装与 values=**CDS** | **本 spec 的全部** |
| 隔离面（不变） | cubelet → CubeShim → hypervisor（KVM MicroVM[+TEE]） | 上游 | 只换交付形态，**不改一行执行代码** |
| 使用控制面（不变） | `sandbox_nodes` 注册表、`select_cube_node`、CB-04 出口编译、CB-06 审计尾随、DEK 注入 | CDS | 仅新增"节点从 K8s 同步进来"的入口 |

### 3.1 systemd → K8s 逐项映射

| 今天（systemd / 人工） | K8s 形态 | 语义保持 |
|---|---|---|
| 上游 `install-compute.sh`（`cube-sandbox-compute.target`） | 上游 chart 的 installer / bootstrap / Big Pod（+ 可选 pvm）4 个 DS | 组件仍由上游交付，CDS 不 patch |
| ② `cds-tls-proxy.service`（stunnel，`accept 0.0.0.0:9443 → connect 127.0.0.1:<cubelet>`，`verify=2`） | `cds-cube-agent` DS 内 stunnel 容器，**同一份 conf 模板**（`deploy/cube-node/tls-proxy.stunnel.conf`） | mTLS 终结、**无明文回退**（sidecar 不可达=节点 down）不变 |
| ③ `cds-audit-tailer.service`（CB-06） | 同 DS 内 tailer 容器（hostPath 读 egress 日志/socket） | 红线 5：审计不可丢弃；投递失败必须重试而非丢弃 |
| ① `cds-volume-plugin`（socket `/run/cds-volume-plugin/cds.sock`） | 同 DS 内插件容器（hostPath socket） | 镜像仍含钉 sha 的 Tongsuo 构建（runbook §9.0.1 不变） |
| `preflight.sh` 硬门禁主序列（kvm / tee_probe / data_fs / ebpf / cubelet_bin / volume_sock / tls_sidecar / containerd / sandbox_cidr / resources，`preflight.sh:473-482`；另在内层循环跑 `check_route_attribution`，`:395`） | DS 的 initContainer 跑**同一脚本**；任一 FAIL → Pod 不进 Ready → 节点不被注册 | 门禁只有一份实现，K8s 只是新的执行处 |
| `verify.sh --json` 人工验收 | DS 周期性跑并上报 | 机读结论不变 |
| `INSTALL_STATE.json` 人工登记 | `cds-cube-agent` → CDS 节点同步入口（KN-03）自动注册/心跳/下线 | 见 §3.3 |

### 3.2 为什么是"两个 DaemonSet"而不是"塞进 Big Pod"

上游 Big Pod 容器清单固定（§2.1 证据），注入 CDS 容器必须 patch 上游 chart —— 违反零 diff 红线。故选**并列自有 DS**：`cds-cube-agent` 与 Big Pod 同在 `hostNetwork`，因此 `127.0.0.1:9999` 可达 cubelet，且能经 hostPath 访问 toolbox / 日志 / socket。**推论（部署前提 C-1）**：`cubeNode.hostNetwork` 必须为 `true`（上游默认）；若客户强制 Pod 网络，则代理目标变为 Big Pod IP 且 sandbox 无法跨重建存活——该组合**不在本 spec 支持范围**，`cds-cube-agent` 启动时探测 `hostNetwork` 不满足即 fail-closed 并显式报错。

### 3.3 节点身份同步（KN-03 的全部难点都在这节）

| 项 | 规则 |
|---|---|
| 身份来源 | `nodeIDSource: specNodeName`（上游默认）⇒ K8s Node 名 = 稳定身份 |
| **归一** | `sandbox_nodes.node_id` 有约束 `^[a-z0-9][a-z0-9-]*$`、3..64 字符（`app/api/sandbox_nodes.py:32`），而 K8s 节点名常含点号/下划线/大写 ⇒ 同步器必须做**确定性归一**（小写化、非 `[a-z0-9-]` → `-`、折叠重复 `-`、超 64 截断后追加 8 位 sm3 前缀防撞名），原始名保留在 `capabilities.k8s_node` |
| endpoint / 版本 | 仍写 `capabilities.cubelet_endpoint` = `http(s)://<nodeIP>:9443`、`capabilities.runtime_version`（读自节点真实 `--version`），**不新建列**（`cube_node_client.py:295-309` 的既有契约） |
| capabilities | `tee` / `xfs_reflink` / `ebpf` 一律来自 agent 在节点上的**真实探测**（`tee_probe.py` + `preflight.sh --json`）。**禁止**从 chart values 手写 `tee: tdx`——那是伪造信任根，违反 I-3 与 SS-13 §六 |
| 凭证 | 新内部入口沿用 CB-06 先例：service token（fail-closed，未配置即全部拒绝）+ mTLS 传输（`app/api/cube_egress.py:3-4`）。**不得**复用 `/api/v1/sandbox-nodes`：该路由是 `require_group("infra")` + `role == "admin"` 的人类管理面（`app/main.py:357`、`sandbox_nodes.py:42`），且注册语义是运维手工动作 |
| 下线 | 心跳缺时走既有 `detect_stale_nodes`；Pod `NotReady` → 自动 `isolate`（≈ cordon，**已有能力**）；**绝不自动 unisolate**（`sandbox_nodes.py:1-5` 既定原则：健康检测只读、不做自动恢复） |

### 3.4 安全与网络要求（不得省略，验收项）

1. cubelet 9999/9998/9966 在 hostNetwork 下**直接绑宿主网卡且无认证**（上游自述）⇒ 节点防火墙/安全组必须把这三个端口限制到 CDS 控制面与本机，仅暴露 9443(mTLS)。此项为 A-5 验收项，写进 runbook。
2. `privileged + hostPID + hostNetwork` 的 DS 是集群内最高权限负载 ⇒ 独立 namespace `cds-cube-system`、专用 SA 仅本 namespace 最小 RBAC、compute 节点用 taint + `placement.compute` 容忍（`values.yaml:179-187`）隔离，避免普通业务负载落到沙箱节点。
3. `NetworkPolicy` 不参与 sandbox 流量治理（I-1）；对外口径继续受 `release-gate-checklist` RG-P1-008（沙箱逃逸/渗透测试未完成则生产 No-Go）约束，**不得**因"上了 K8s"而声称网络隔离已覆盖沙箱。
4. CIDR：chart `cubeNode.network.cidr`（默认 `172.16.0.0/18`）与 CDS `check_sandbox_cidr`/`check_route_attribution`（`preflight.sh:421,395`）**双闸**，任一 FAIL 即节点不合格；冲突时以 CDS preflight 为终局判定。

## 四、任务分解（KN 系列）

| 任务 | 内容 | 交付 + VERIFY |
|---|---|---|
| **KN-00 可行性 spike（前置，判否即换候选 C）** | 在本地 k3s/k3d 用 `externalControlPlane.enabled=true` 起 4 个 DS（不装 CubeMaster/CubeOps/MySQL） | **判据**：Big Pod `Ready`（探针 TCP 9999 通）、宿主 toolbox 落地、`cubebox ls` 可建/销 VM、`masterEndpoint`/`opsEndpoint` 为空时**无 CrashLoop、无静默 half-ready**。产物：`docs/e2e/…` spike 记录 + 判定表 |
| KN-01 chart 包装 | 新增 `helm/cds-cube/`（`Chart.yaml` 以 file/oci dependency 引上游、`values.yaml` 只写覆盖项）+ k3s 路径等价物 `k8s/cube-node.yaml`；镜像经 `global` registry 改写为国内源；钉住 tag 与 `CUBE_PINNED_RUNTIME_VERSION` 一致性 | `helm template` 渲染通过 + `helm lint`；CI 断言 values tag == `CUBE_PINNED_RUNTIME_VERSION`；不新增第 6 份 `CDS_*` env 拷贝（见 §七 铁律 5） |
| KN-02 `cds-cube-agent` 镜像与 DS | 容器：stunnel（复用 `tls-proxy.stunnel.conf` 模板）+ tailer + volume-plugin + `preflight.sh`/`verify.sh --json`/`tee_probe.py` initContainer；证书三件套从 Secret 挂入，CA 由 certificate_service 签发（不入库） | `hostNetwork` 探测 fail-closed（C-1）；`verify=2` 断言；sidecar 不可达 → 节点 down 语义测试 |
| KN-03 节点同步 | 新内部入口（token fail-closed）+ 同步器：注册/心跳/下线；`node_id` 确定性归一 + 原始名进 `capabilities.k8s_node`；NotReady→isolate，不自动 unisolate | 归一函数单测（含点号/大写/超 64 字符/撞名）；token 未配置=全拒（401/403 W2 契约）；重复同步幂等 |
| KN-04 门禁双跑 | 上游 3 个 Hook 与 `preflight.sh` 主序列（§3.1）的冲突矩阵；CIDR/hostPorts 检查去重或明确"都要过" | 干跑记录：任一闸 FAIL 时节点不得出现在 `sandbox_nodes` READY 集 |
| KN-05 升级与回滚 SOP | 版本 pin 检查 → 会话排空（isolate）→ `maxUnavailable=1` 滚动 → `verify.sh --json` 冒烟 → unisolate；回滚=回退 release + 重跑冒烟 | runbook §9.3/§9.4 增补 K8s 分支；一次真实滚动演练记录（远程权威环境） |
| KN-06 观测与告警 | Big Pod/agent 的 `NotReady`、stunnel down、tailer 积压、`runtime_version` 漂移 → 复用既有告警中心；节点详情展示 `trust_root`（SS-13 既有面） | 告警单测 + 面板字段来源可追到真探测 |
| KN-07 安全复核 | 特权负载威胁模型、RBAC 最小化、§3.4 端口暴露面、`product-security-release-review` §6 口径复核 | 评审记录 + RG-P1-008 状态如实（未完成≠可宣称） |
| KN-08 文档与回归 | runbook §1 模式表加一行、新增 §9.6（K8s 形态 bring-up/分诊/滚动/回滚）；`tests/test_helm_chart.py` 式不变量测试（新 chart 的 values 契约）；全量回归 | CI 绿 + 回归不劣化（`ci/known-failures.md` 不增长）+ `compileall` |

## 五、配置与门禁矩阵（全部 `CDS_` 前缀）

| 配置 | 默认 | 语义 | 生产门（`validate_security_config`） |
|---|---|---|---|
| `CDS_CUBE_NODE_SYNC_ENABLED` | `false` | 开启"节点由 K8s agent 同步"通道 | — |
| `CDS_CUBE_NODE_SYNC_TOKEN` | `None` | 同步入口 service token | `SYNC_ENABLED=true` 而 token 未设 → **启动拒绝**（fail-closed） |
| `CDS_CUBE_AGENT_REQUIRE_HOSTNETWORK` | `true` | 部署前提 C-1 的机器可检版本 | 置 `false` → 生产**启动拒绝**（等价于打开不受支持形态） |
| `CDS_CUBE_NODE_AUTO_ISOLATE` | `true` | NotReady → isolate（cordon） | — |
| `CDS_CUBE_NODE_AUTO_UNISOLATE` | `false` | 保留位，**默认且生产禁止** true | 生产 true → **启动拒绝**（守"不做自动恢复"的既定诚实原则） |
| `CDS_CUBE_K8S_NODE_LABEL` | `cube.tencent.com/cube-node=true` | 只同步带此 label 的节点 | — |

**迁移：零。** `sandbox_nodes` 现有列 + `capabilities` JSON 足够（`app/models/sandbox_node.py:38-45`、`cube_node_client.py:295-309`），与 additive-only 约定天然一致；错误码复用既有 `RUNTIME_UNAVAILABLE` / `NO_NODE_TEMPLATE` / `TEE_NODE_UNAVAILABLE`，同步入口鉴权失败走 W2 既有码，不新造。

## 六、验收标准

1. **A-1** 上游 chart 未被修改：`git -C CubeSandbox status --porcelain` 为空（零 diff 红线，交付前后各测一次）。
2. **A-2** compute-only 起节点：带 compute label 的节点上 4 个 DS 全 Ready，toolbox/XFS 前置在宿主可见。
3. **A-3** 自动注册：不执行任何人工登记，`sandbox_nodes` 出现该节点且 `READY`；`capabilities` 内 `cubelet_endpoint`/`runtime_version`/`tee` 与节点实测输出**逐字段相等**（对比 `verify.sh --json` 与 `tee_probe.py --json` 原文）。
4. **A-4** 真会话：在该节点上创建 `l2c` 会话并跑通执行→出口拒绝域外→审计锚定（复用 SS-13 C1/C4 判据），`trust_root` 三表面披露一致。
5. **A-5** 暴露面：节点上 `ss -tlnp` 复核 9998/9999/9966 不对 CDS 控制面以外源可达；CDS 侧仅经 9443 mTLS 成功建连，明文直连 cubelet 从控制面以外被拒。
6. **A-6** 三档诚实：拔掉/关闭 KVM 的节点必须**不出现**在 READY 集（preflight FAIL → 不注册），会话得 400 `RUNTIME_UNAVAILABLE`；全程无 legacy 适配器被激活（回归锁定）。
7. **A-7** 滚动与回滚：真实滚动一个节点，sandbox 存活（hostNetwork 语义）或按 SOP 先排空；回滚后冒烟通过。（A-5/A-6/A-7 **必须在远程权威环境跑**，不在本地跑——用户既定验证规则。）
8. **A-8** 门禁矩阵：§五 三个"生产拒绝"分支（token 未设 / REQUIRE_HOSTNETWORK=false / AUTO_UNISOLATE=true）各有启动期测试；`test_helm_chart.py` 式不变量测试覆盖 `helm/cds-cube/values.yaml`。
9. **A-9** 回归：全量回归通过且 `ci/known-failures.md` 不增长；`python -m compileall -q app tests` 通过。

## 七、执行边界（铁律）

1. **上游零 diff**：只经 values / dependency 包装 / 自有 chart 交付；不 patch `CubeSandbox/` 树（A-1 机器校验）。
2. **禁伪造**：`capabilities` 的 `tee`/`xfs`/`ebpf`/`runtime_version` 全部来自节点真实探测；无 KVM = 不注册，无 TEE = `trust_root=none`。
3. **不引入 k8s 作为执行编排依赖**（I-2）：不新增 kube 侧会话调度、不把 `SandboxLevel.K8S` adapter 接回、不因 K8s 形态调整 `select_cube_node` 的语义。
4. **不静默降级**：三档 KVM 路径之外没有路径；`RUNTIME_UNAVAILABLE` 是正确答案而不是故障。
5. **不制造第 6 份配置拷贝**：`CDS_*` env 契约仍以 `helm/cds/values.yaml` + `docker-compose.prod.yml` 双份为准（`helm/AGENTS.md`），`helm/cds-cube/values.yaml` **只放节点交付参数**（镜像/tag/CIDR/label/端口），不得复制平台 env。
6. additive-only 迁移约定天然免用（零迁移）；生产禁 `create_all` 不变。
7. **不 git commit**（用户未要求）；每节点交付由父任务亲核（file:line + 亲跑）。

## 八、范围外（非目标）

- 不把容器/K8s Pod 作为任何密态会话的执行环境（`SANDBOX_K8S_RUNTIME_CLASS` 与 `SandboxLevel.K8S` 继续锁在 `CDS_SANDBOX_LEGACY_ENABLED` 门后）。
- 不做 GPU 节点池、多集群联邦、跨集群调度。
- 不部署 CubeMaster/CubeAPI/CubeOps/MinIO/templatecenter（除非 KN-00 判否并显式改选候选 C）。
- 不做 TEE 硬件接线与 attestation 验证（SS-13 RT-06 硬件期）。
- 不改 compose 路径（今天也不承载 Cube 节点面）。
- 不改 `SandboxLevel` 枚举与 SS-13 已交付的单路径行为。

## 九、风险登记

| 编号 | 风险 | 处置 |
|---|---|---|
| R-1 | **compute-only 无 CubeMaster/CubeOps 时 cubelet 行为未证**（上游注释显示 cubelet 向 CubeOps 注册/心跳）。KN-00 的第一判据 | KN-00 spike 判否 → 候选 C（全量控制面），并登记"新增状态件 + 第二套调度语义"代价与新的对外口径复核 |
| R-2 | `hostNetwork=true` 与客户 VPC/Service CIDR 冲突（上游 hook + CDS preflight 都可能 FAIL） | chart 默认 `172.16.0.0/18` 可覆盖；冲突时按 KN-04 分诊表改 CIDR，不得跳过检查（`cidrSkipConflictCheck` 生产禁用） |
| R-3 | privileged+hostPID+hostNetwork 特权负载的集群侧风险（拿它可看宿主全部进程与设备） | §3.4：专用 namespace/最小 RBAC/taint/端口防火墙；KN-07 复核；对客户明示"节点即信任域" |
| R-4 | Pod 重建打断该节点全部 sandbox（若 hostNetwork 被关） | C-1 fail-closed 前提 + KN-05 SOP 先 isolate 排空 |
| R-5 | 上游 chart 镜像与 bitnami 子 chart 依赖外网 | `global` registry 改写 + 离线源包（沿用 runbook §9.0.1 的 sha 校验纪律，源包 sha 不可被 env 覆盖） |
| R-6 | PVM 档被误当生产（上游明写 kvm-pvm "intended for development, evaluation and self-built experiments only ... NOT suitable for production workloads"，`CubeSandbox/deploy/one-click/install.sh:927-930`） | agent 上报 `kvm_source=pvm` 且**会话披露面标注非生产**；无 `allow-pvm-bootstrap` label 的节点不得拉 PVM 镜像（上游已保证，KN-04 复核） |
| R-7 | chart tag 与 `CUBE_PINNED_RUNTIME_VERSION` 漂移 → 节点装了 A 版、调度按 B 版筛 | KN-01 CI 断言二者相等；漂移即 CI 红（版本 pin 是既有机制，`cube_runtime_adapter.py:97`） |

## 十、波次计划（mass-ulw）

- **Phase 0（串行，1 节点）**：KN-00 spike（deep-low）。STOP WHEN：判据表出结论 + 判定候选 B/C。判否则本 spec 出 v1.1 改选候选 C，再继续。
- **Phase 1（并行 3 lane，写域不相交）**：L1 chart 与 agent（KN-01/02，unspecified-low）｜L2 后端同步与门禁（KN-03 + §五 门，deep-low）｜L3 观测与文档（KN-06 + runbook/模式表，writing）。
- **Phase 2（串行）**：KN-04 双闸矩阵 → KN-05 滚动/回滚演练（远程权威环境）→ KN-07 安全复核 → KN-08 回归收口 + 回写本 §十一。

## 十一、执行记录

**状态：未开始。** 实施后回填（KN 交付对照、判据、回归实际数字、KN-00 spike 结论）。禁止预填任何未实测数字。
