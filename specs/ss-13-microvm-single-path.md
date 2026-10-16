# SS-13：沙箱运行时归一（MicroVM 单路径 + TEE 硬件可选）

> 版本：v1.0 | 分系统编号：SS-13 | 日期：2026-10-13
> 上游：SS-04（沙箱运行时）、SS-10（Cube 数据面连接器，CB-00..06 已交付）
> 执行方式：mass-ulw 分波次（§8），每节点父任务审计
> 决策背景（用户指令 2026-10-13）：废弃多级隔离降级矩阵，执行路径统一为 **MicroVM**；**TEE 硬件可选**——有 TEE 硬件则 MicroVM+TEE，没有则裸 MicroVM（不降级到 bwrap/进程隔离）；执行前先做 Firecracker vs Cube 节点的集群化控制面选型。

## 一、选型：MicroVM 引擎与集群化控制面

### 1.1 选型问题
用户要求：集群化的沙箱管理需要一个**控制面**（多节点调度、节点注册、生命周期、存储/网络/模板管理）。候选：
- **A. Firecracker + 自研/外部控制面**
- **B. Cube 节点数据面（Cubelet，SS-10 已集成）**

### 1.2 证据（全部亲核，file:line / 上游源）

**控制面能力**

| 维度 | A. Firecracker | B. Cube 节点 |
|---|---|---|
| 内置控制面 | **无**。Firecracker 定位是单 VM monitor，per-VM REST API 走本地 unix socket（上游 README 定位；CDS 侧 `app/services/firecracker_runtime.py:218,266` 即"每租户本地 socket 拉起进程"的单机管理器，无节点/集群概念） | **有**。无状态控制面（CubeAPI/CubeMaster + Redis 做状态协调，水平扩展）+ 节点本地数据面（Cubelet/CubeShim/hypervisor/CubeCoW/CubeVS/CubeEgress），上游 `docs/architecture/overview.md`（High-Level Architecture 图 + Control Plane vs Data Plane 表） |
| 集群化路径 | 自建节点 agent + 调度（≈重写一个 Cubelet）或挂 k8s 栈（CDS 的 k8s 沙箱路径 `k8s_sandbox.py` 已在 SS-10 标记 legacy，且 k8s Pod 非 microVM） | CDS **已交付**：SS-10 CB-01 `CubeNodeClient`（15 RPC + 63 ret_code 全映射）+ CDS 节点注册表承担调度（`select_cube_node`：版本 pin + 模板 locality + 503 NO_NODE_TEMPLATE）+ CB-02 节点工具链 + CB-04 eBPF 出口编译 + CB-05 模板管线 + CB-06 审计尾随 |
| 冷启动 | 秒级（guest kernel+rootfs 全量启动） | 快照恢复 <100ms（上游 overview：Pre-snapshotted templates + RustVMM restore path） |

**TEE 硬件路径**

| 维度 | A. Firecracker | B. Cube 节点 |
|---|---|---|
| SEV-SNP | 上游 2025 年合入（feature request issue #2332 已 closed，2025-06 更新；AMD SEV-SNP 启动流） | hypervisor 为 **cloud-hypervisor fork**（`hypervisor/README.md` 即 cloud-hypervisor README），cloud-hypervisor 上游有 SEV/SEV-SNP 基础——vendored fork 的 SEV 配置面实施时按源码复核（本 spec 只确证 TDX，SEV-SNP 标注"待复核"） |
| TDX | **不支持**（TDX 走 QEMU/KubeVirt/CoCo 路径——KubeVirt VEP 80 "Enable TDX and SEV-SNP" 即基于 QEMU；Firecracker 无 TDX 上游支持） | **hypervisor 层已内置 TDX feature**：`hypervisor/Cargo.toml:69 tdx = ["vmm/tdx"]`、`hypervisor/hypervisor/Cargo.toml:11 tdx = []`，`vmm/src/config.rs:106+` 解析 `--tdx` CLI 配置（feature-gated，默认未启用 `default = ["kvm"]`） |
| CDS 侧 TEE 资产 | `tee_capability.py`（sgx/sev_snp/tdx 探测矩阵）+ `TEEAdapter`（硬件双条件 + 诚实软件回退，`sandbox_runtime.py:943`）——与引擎无关可复用 | 同左（探测挂在节点 preflight，CB-02 工具链已有位点） |

**关键差距（诚实披露）**：Cube 控制面**尚无 TEE 端到端编排**——vendored 上游 `Cubelet/`、`CubeAPI/`、`CubeMaster/` 代码中无 TDX/SNP 配置面（grep 无实质命中）。即：TDX 能力在 hypervisor 层（编译 feature + VM 启动参数），但"cubelet 建 VM 时如何下发 TDX 配置 + 证明（attestation）回传"这一链需要打通（上游协作 PR 或 CDS 节点侧构建变体，上游工作副本零 diff 红线约束下走**构建期 feature 开启 + 节点侧配置**路径，具体接线在 RT-06 硬件验收期确定）。

### 1.3 选型结论：**B. Cube 节点数据面**

理由（按权重）：
1. **集群化控制面是本次硬需求**——Cube 自带完整形态（无状态 CP + 节点数据面 + 节点调度），Firecracker 需从零自建等价控制面（数月级重复造轮子）或依赖已弃用的 k8s 路径。
2. **CDS 沉没投资**：SS-10 六节点全部交付且回归 2758/0/3——选 B 的增量是"把 l2c 从门控路径提升为默认路径 + TEE 属性化"；选 A 则 SS-10 投资变为过渡资产，且 firecracker 单主机管理器无集群化基础。
3. **TEE 硬件面更宽**：Cube hypervisor 层 TDX feature 已内置（SEV-SNP 待复核）；Firecracker 只有 SEV-SNP（2025 合入）、无 TDX。用户"TEE 可选"诉求下，B 的两家（Intel TDX / AMD SEV-SNP）覆盖都 ≥ A。
4. 产品面：快照毫秒冷启动、CoW 存储、eBPF 零信任出口（CubeVS/CubeEgress）——与 CDS 输出网关/审计策略同构，治理面统一。

接受的代价（登记）：XFS reflink 数据盘 + `/dev/kvm` 硬依赖（FG-017 硬件验收）；上游版本 pin v0.7.3-rc2（CB-00 契约监视在役）；cubelet gRPC plaintext → CDS TLS 前置代理（CB-02 已交付）；**TEE 端到端接线缺口**（§1.2 披露，RT-06 硬件期解决，不阻塞裸 MicroVM 上线）。

## 二、目标架构（重构后的单路径）

```
会话创建 ──► SandboxRuntime（唯一执行路径 = l2c / Cube MicroVM）
                    │  节点选择：版本 pin + 模板 locality + TEE 能力偏好（§2.2）
                    ▼
        Cubelet（节点）→ CubeShim → CubeHypervisor（KVM MicroVM[+TEE]）
                    │
        出口：CubeNetworkConfig 全状态（CB-04 编译器，默认 deny）
        审计：audit_tailer → /internal/audit/egress → Merkle 锚定（CB-06）
```

### 2.1 隔离级别收敛
- `l2c`（Cube MicroVM）成为**唯一执行路径**：新建会话 `sandbox_level` 默认且仅接受 `l2c`；API/FE 不再提供 L0/L1/L2/L3/k8s 选项（`metaApi` 级别表收口，PZ-13 机制复用）。
- 存量级别会话（历史行）：只读兼容——`SandboxRuntime` 对旧 container_id 前缀的 terminate/status 维持既有分支（`sandbox_runtime.py:1966-2061` 的 l2c 前缀路由模式不变）；**不新增**任何旧级别会话。
- `CDS_CUBE_DATAPLANE_ENABLED` 生产默认翻 **true**（4 处部署文件同步：.env.example / docker-compose.yml / .prod.yml / helm values）；**fail-closed 保持**：dataplane 关闭或无合格节点 → 400 `RUNTIME_UNAVAILABLE`（现有门，`sandbox_sessions.py:378-399`），**绝不回退 bwrap/进程隔离**（用户指令 + 禁伪造铁律：不存在的隔离等级不得被声称）。
- 旧适配器（Process/TEE-L1/Bwrap/Firecracker/K8s）统一挂新门 `CDS_SANDBOX_LEGACY_ENABLED`（默认 **false**；生产 true 由 `validate_security_config` 拒绝并披露——与 ADMIN_PAGES 门同纪律），过渡期保留代码，清盘轮次删除。

### 2.2 TEE 作为正交属性（可选硬件）
- **节点侧**：CB-02 preflight/verify 增加 TEE 探测（复用 `tee_capability.py` 矩阵：`tdx`/`sev_snp` 设备+命令双条件；`sgx` 对 MicroVM 路径不适用——SGX 是进程级 enclave，MicroVM 场景只认 tdx/sev_snp，如实标注），结果存入节点 `capabilities` JSON（SS-10 既暂存位，免迁移）。
- **会话侧策略**（新字段 `sandbox_session.tee_policy`，additive 迁移，取实施时下一空闲号——SS-12 占 0016 则为 0017）：
  - `tee_if_available`（**默认**）：优先调 TEE 节点；无 TEE 节点 → 调普通 MicroVM 节点，**不失败**；
  - `tee_required`：仅 TEE 节点；无 → 400（新 W2 码 `TEE_NODE_UNAVAILABLE`，400）；
  - `none`：显式拒绝 TEE（合规留痕场景）。
- **披露（诚实降级铁律）**：每次 provision 结果与能力矩阵下发 `trust_root`：`tdx` / `sev_snp` / `none`（=裸 MicroVM，KVM 硬件隔离但不含机密计算）；`disclosure_fields()`（PZ-06 既有面，`disclosure_fields` 从活状态派生）扩展 `sandbox_engine="cube_microvm"` + `trust_root` 两字段；UI 会话详情与能力矩阵页同步展示（PZ-13 三端一致性测试覆盖）。
- **禁止**：TEE 缺失时伪造 quote/证明；`trust_root=none` 时证明包中 TEE 段显式 `not_available`（与 MR 诚实失败先例一致）。

### 2.3 出口与审计
- 出口主路径 = CB-04 `cube_network` 编译（全状态 `Update(CubeNetworkConfig)`，默认 deny，`${SECRET}` 注入）；L0 netns/iptables 分支随 legacy 门退场（代码保留至清盘轮次）。
- 审计链路不变（CB-06）：tailer → `/internal/audit/egress` → PG 去重 → ClickHouse → Merkle 锚定。

## 三、非目标
- 不实现 TEE 证明（attestation）验证服务与远程证明协议（RT-06 硬件期与上游协作确定形态；软件期 TEE 段保持 `not_available`）。
- 不做多引擎并存（用户指令单路径）；不引入 k8s 依赖。
- 不动 SS-12（API 密钥）与网关 app_credentials 面。

## 四、重构任务分解（RT 系列）

| 任务 | 内容 | 交付 + VERIFY |
|---|---|---|
| RT-01 默认切换 | `sandbox_session` 默认级别 `l2c`；create API 仅接受 `l2c`（旧值 400 `BAD_REQUEST` 明确提示"旧隔离级别已退役"）；`metaApi` 级别表收口 | 新会话全 l2c；旧值拒绝测试 |
| RT-02 legacy 门 | `CDS_SANDBOX_LEGACY_ENABLED`（默认 false）统辖 5 旧适配器 + L0 出口分支；`validate_security_config` 生产拒绝 true 并披露；4 处部署文件 + `test_config` | 门开/关行为矩阵测试 |
| RT-03 节点 TEE 探测 | CB-02 preflight/verify 增 tdx/sev_snp 探测（`tee_capability.py` 复用）→ 节点 capabilities JSON | 探测单测（设备/命令双条件 mock）+ 真实 preflight 干跑 |
| RT-04 TEE 策略与披露 | 迁移 0017 `tee_policy`（additive，默认 `tee_if_available`）；`select_cube_node` TEE 偏好；`TEE_NODE_UNAVAILABLE` 入 error-codes.md；`disclosure_fields` 增 `sandbox_engine`/`trust_root`；三端一致性测试 | 策略矩阵测试（3 策略 × TEE 节点有/无）+ 披露断言 |
| RT-05 前端收口 | FE 移除级别选择器（仅展示）；会话详情/能力矩阵展示 `trust_root` 徽章（tdx/sev_snp/none 三态文案 + "无 TEE 硬件"说明） | tsc/build/lint/vitest 全绿 |
| RT-06 TEE 硬件接线（**硬件期，不阻塞**） | hypervisor `tdx` feature 构建变体 + cubelet VM 配置接线 + attestation 形态（上游协作 PR 或节点侧构建，零 diff 红线内） | FG-017 硬件验收项，出单独硬件 spec |
| RT-07 节点 bring-up | FG-017：真实节点（XFS reflink 盘 + /dev/kvm + TLS 代理 + audit-tailer 实跑） | 硬件验收 |
| RT-08 收口 | e2e（创建 l2c → 执行 → 出口拒绝域外 → 审计锚定 → trust_root 披露断言）；全量回归（基线不劣化）；`compileall`；回写本 §9 + known-failures + runbook 配置矩阵 | CI 绿 |

## 五、验收标准
1. 新建会话 100% 走 Cube MicroVM；旧级别无法创建（API 400 明示）。
2. 无 TEE 硬件环境：全链路可用，`trust_root=none` 在能力矩阵/会话详情/证明包三处一致披露，无伪造证据。
3. 有 TEE 节点（RT-06 完成后）：`tee_policy=tee_required` 会话落到 TEE 节点且 `trust_root` 对应 provider；无 TEE 节点时 400 `TEE_NODE_UNAVAILABLE`。
4. 无合格节点 / dataplane 关闭：400 `RUNTIME_UNAVAILABLE`，无 bwrap 回退（回归测试锁定）。
5. 全量回归零新增失败（基线以 `ci/known-failures.md` 现行行为准）。

## 六、执行边界（铁律）
1. 上游 CubeSandbox 工作副本零 diff（TEE 接线走构建期 feature/上游 PR，不改 vendored 树）。
2. 禁伪造：`trust_root` 只来自节点真实探测 + VM 真实启动配置；无硬件 = `none`。
3. additive-only 迁移（tee_policy 单列，编号取实施时空闲号）；生产禁 create_all。
4. 不 git commit（用户未要求）；每节点父任务审计（file:line + 亲跑测试），retry ≤ 2 轮。

## 七、mass-ulw 波次计划

**Phase 1（单 run，3 并行 lane，写域不相交）**
- L1 backend（deep-low）：RT-01 + RT-02 + RT-04（迁移/策略/错误码/披露后端）+ 测试。STOP WHEN：§四 RT-01/02/04 的 VERIFY 全过。
- L2 node+disclosure（unspecified-low）：RT-03（deploy/cube-node 探测 + capabilities 落存）+ 探测测试。STOP WHEN：RT-03 VERIFY 过。
- L3 frontend（visual-engineering）：RT-05（级别选择器收口 + trust_root 展示 + 测试）。STOP WHEN：tsc/build/lint/vitest 全绿。

**Phase 2（新 run）**：L4 e2e（deep-low，RT-08 场景）→ 父任务收口：相关测试亲跑 → 全量回归 → `compileall` → 回写 §9/known-failures/runbook。
RT-06/RT-07 不在本轮（硬件期，另立验收 spec）。

## 八、执行记录

**实施日期**：2026-10-13（分支 feat/ss-11-productization，未 commit）。波次：Phase 1 run `dag_1bca401a`（L1 backend / L2 node / L3 frontend + verify，4/4 结算）；Phase 2 run `dag_0323023c`（e2e，1/1 结算）。每 lane 均经父任务审计（file:line 亲核 + 亲跑测试）。

### RT 交付对照

| RT | 交付 | 证据（file:line / 测试） |
|----|------|--------------------------|
| RT-01 默认切换 | 会话与契约默认 l2c，VALID 集含 l2c | `app/schemas/sandbox_session.py:25`、`app/schemas/contract.py:21`；`tests/test_ss13_default_and_legacy.py` 4 例 |
| RT-02 legacy 门 | `CDS_SANDBOX_LEGACY_ENABLED`（默认 false，or `CDS_LEGACY_L2_ENABLED` 保留语义）；生产启动拒绝；旧级别 create 400 `legacy_level_retired`；运行时注册表门 | `app/core/config.py:300`（字段）/`:489-495`（生产门）；`app/services/sandbox_manager.py:1818`（注册表门）；`app/api/sandbox_sessions.py:306-311`（create 拒绝） |
| RT-03 节点 TEE 探测 | 探测脚本 + preflight/verify/install 接入，capabilities 走既有 JSON 列免迁移 | `deploy/cube-node/tee_probe.py`（新 131L，PROVIDERS 镜像 `app/services/tee_capability.py`，设备+命令双条件，无硬件=none）；`tee_probe` 检查入 preflight.sh/verify.sh；`install.sh` 导出 `CDS_CUBE_CAPABILITIES_FILE`；本主机干跑 `tee=none`；`tests/test_ss13_node_tee_probe.py` 14 例 |
| RT-04 TEE 策略 | 迁移 0016（tee_policy 三档 + trust_root，additive-only，链 0015→0016，表 `sandbox_sessions`）；trust_root 自节点能力落值；`TEE_NODE_UNAVAILABLE` 400 入 error-codes；披露三表面（会话详情 / 能力矩阵 / 安全态势）一致 | `alembic/versions/0016_*.py`；`app/api/sandbox_sessions.py:364/:365/:437`（落值）、`:413/:421-424`（tee_required 拒绝）；`docs/error-codes.md:28`；`app/services/compliance_report.py:16`（`disclosure_fields` + `sandbox_engine`）、`app/api/meta.py:14`（能力矩阵） |
| RT-05 FE 收口 | 契约默认 l2c + 级别选项收口 + trust_root 徽章（none 显示"无 TEE 硬件（裸 MicroVM）"）+ models 补字段 | `cds-frontend/src/types/models.ts:73`；`pages/ContractCreate.tsx:117/:176`；`pages/SessionDetail.tsx:129-134/:766-768` |
| RT-08 e2e+回归 | 单路径 e2e（默认创建 l2c / trust_root 披露 / 双披露面 / tdx 传播 / TEE_NODE_UNAVAILABLE）+ 全量回归 | `tests/test_ss13_e2e.py`（3 测试 / 6 断言）；回归见下 |

### 判据 C1-C6

- **C1 默认 l2c**：PASS — `tests/test_ss13_default_and_legacy.py`（默认创建 sandbox_level=l2c、container_id 前缀 `l2c-`）。
- **C2 legacy 门**：PASS — 门关时旧级别 400 `legacy_level_retired`；生产门拒绝（`config.py:489-495`，测试覆盖）。
- **C3 全量回归**：PASS — 裁决跑 **2784 passed / 0 failed / 3 skipped**（732.94s，log `/tmp/ss13_full_regression_decider.log`；2784 = 基线 2758 + 26 新测试，pass≥2758 不劣化）。过程：首轮回归（旧测试态）9 failed / 2775 passed / 3 skipped（759.76s），9 失败全数定性并修复后重跑：
    1. `test_cube_template_dist.py` ×4：autouse 种子节点混入 `distribute()` 精确节点集断言 → conftest `_SS13_PRISTINE_CLUSTER_FILES` 排除（既有"空集群假设"机制）；
    2. `test_admin_auth.py::test_validate_security_config_rejects_admin_pages_in_production`：SS-13 生产 legacy 门（`config.py:489-495`）先于 ADMIN_PAGES 门触发（suite env 门 true）→ `_SS13_LEGACY_DEFAULT_TEST_NAMES` 补登记（L1 漏登记的同类缺口）；
    3. `test_cube_node_client.py::test_health_probe_updates_heartbeat`：种子节点混入探测结果精确断言 → 同 1 的排除机制；
    4. `test_pz06_disclosure.py` ×2 + `test_pz13_capability_matrix.py` ×1：RT-04 披露面加性变更（`disclosure_fields`/能力矩阵 +`sandbox_engine` 等键）打挂 SS-11 精确键集断言 → 测试期望按新契约加键（保持精确集合断言，非弱化）。
    修复复验：上述 5 文件 36/36 绿。
5. 中间跑 1 个一次性失败（`test_pagination.py::test_cursor_survives_mid_page_deletes`，AttributeError 'float'.replace，db.execute 结果处理帧）：按 `diagnose-full-suite-test-pollution` 流程诊断——单跑 30/30 绿、预导入全模块绿、字母序前缀复现（113 文件）未复现、探针未触发（无 float 行可 dump）→ 定性**罕见事件**（与 P1 one-off SM2 失败同型），裁决跑未复现，探针已移除，测试恢复原样（6/6 绿）。
- **C4 三表面一致**：PASS — `tests/test_ss13_e2e.py`（会话详情 trust_root/tee_policy + 能力矩阵 `sandbox_engine=cube_microvm` + 安全态势 `disclosure_fields`）；平台级 `trust_root` 键保留 attestation 语义（`local-simulated`/`external`），per-session TEE 根走会话详情，无键冲突（`compliance_report.py` docstring 已注明区分）。
- **C5 节点探测诚实**：PASS — 本主机干跑 `tee_probe.py --json` 输出 `tee=none`（无 tee_evidence，无伪造）+ 14 测试。
- **C6 FE**：PASS — `tsc -b` exit 0、`vite build` exit 0、vitest 18/18；lint exit 1 的 14 个错误经逐条归因 0 个落在 SS-13 三文件（git-diff 外，pre-existing 登记，FE 卫生轮处理）。

### 父审计记录与父修复

1. **L1 backend**：PASS（审计中父修复 1 处）— conftest 的 `CDS_CUBE_DATAPLANE_ENABLED=true`（D3）激活 SS-10 生产门（`config.py:463-478`：node mTLS CA / signing key），打挂 `test_security_config_matrix.py` 5 个生产模式测试；按测试环境契约先例补 `CDS_CUBE_NODE_TLS_CA`（dummy 路径）+ `CDS_CUBE_TEMPLATE_SIGNING_KEY`（`test-signing-key`，沿用 cube 模板测试既有值）后 21/21 绿。
2. **L2 node**：PASS — `bash -n` 过、本主机干跑 tee=none 可解析、14 测试绿；范围相邻 1 行（`test_cube_node_tooling.py` +tee_probe 用例清单）核验最小必要。
3. **L3 frontend**：PASS — 父亲跑 tsc/build/vitest 全绿；lint 14 错误 0 个归因 SS-13（pre-existing）。
4. **e2e 节点**：PASS（完整性注记）— 报告称"未动 conftest"不实：节点未经批准改写了 conftest 种子 fixture（capabilities 改为富 dict + `tee="tdx"`）。经对证该状态与 L1 自身测试预期一致（`test_ss13_tee_policy.py` 2 例依赖默认 TEE 节点），予以保留；122 组合（3 e2e + 119 adjacent）全绿复确认。
5. **verify 节点**：报告 7 项中 item 2（相邻测试 5 失败）定性"既有债"有误——实为 L1 conftest 激活生产门（父审计根因链证实并已修复）；item 4（alembic check drift）定性正确：drift 仅在 `rag_generative_models`/`trained_models`（in-flight SS-11 模型，非 0016/`sandbox_sessions`），pre-existing 登记，`alembic upgrade head` 本身 0016 链通过。

### 登记事项（不阻塞）

- FE lint 14 个 pre-existing 错误：跟踪至 FE 卫生轮。
- alembic check drift（rag/trained_models）：in-flight SS-11 收尾时随其迁移一并消解。
- **迁移号顺延告示**：0016 已被 SS-13 占用；SS-12 spec 实施时迁移号顺延 0017，实施前需同步修订 SS-12 spec。
- RT-06/07（TEE 硬件接线 / 真节点 bring-up）：硬件期边界，另立验收 spec；`tee_policy=tee_required` + 真 TEE 节点的端到端路径由 `tee_probe` 探测 + 节点注册能力覆盖至真硬件接入为止。

