# 03 — S3 沙箱安全测试（SS-14 要求 3）

- 时间窗口: 2026-10-13（远程 100.112.5.x:22022，脚本 `deploy/e2e-remote/e2e_sandbox_security.py`）
- 判据: 沙箱隔离面 + fail-closed（无节点 l2c / 退役级别）+ TEE/引擎披露一致性。
- 证据: `e2e_sandbox_security_evidence.json`（远程 /root/，关键摘录回填本文档）。

## 1. 测试面（脚本步骤契约）

| 步骤 | 契约 |
|---|---|
| admin.login | 人类 admin 登录 |
| probe.resource_upload / probe.product / probe.submit / probe.approve / probe.publish | 探针产品走完整发布生命周期（严格：每腿显式检查——Round C 教训：fire-and-forget 吞 400 造成假阳性 PASS） |
| failclosed.l2c_no_node | l2c 会话创建（无节点远程）→ **400 RUNTIME_UNAVAILABLE**（无 bwrap/进程回退，SS-13 单一路径） |
| failclosed.legacy_l0/l1/l2 | 退役级别创建 → 400（explicit retired 消息） |
| disclosure.meta_engine | capability-matrix 顶层 `sandbox_engine=cube_microvm` |
| disclosure.meta_trust_root | attestation `trust_root` 诚实披露（`local-simulated`/`external`，无伪造硬件）；会话级 TEE trust_root 无硬件默认 `none`（代码验证 sandbox_sessions.py:365，节点轨活验） |
| nodes.registry | 节点注册表面在（远程期望 0 节点/0 可调度） |

## 2. 执行记录

### Round B（API6 首跑）
- 8/9：`failclosed.l2c_no_node` FAIL——得 400 但为 "Data product is not published"（探针产品未发布，门序：产品发布检查先于运行时节点门，顺序合理；探针前置生命周期缺失为脚本缺陷）。其余 8 步全 PASS（退役级别三连 400 + 披露两表面 + 注册表面）。

### Round C（D-e2e-11 修复后）
- 8/9：同败因（探针产品仍无资源——资源上传本身被 D-e2e-12 HSM 缺陷 500 阻断；探针 PASS 为假阳性，Round C 暴露后已严格化）。

### Round D（API7：D-e2e-12 修复）
- 1/2：`probe.resource_upload` FAIL——wrap DEK 编码缺陷（D-e2e-13）阻断上传；探针严格化首次如实上报（Round C 的假阳性形态不再出现）。

### Round E（API8：D-e2e-13 修复）——全绿
- **10/10 PASS，0 failed**（证据 /root/e2e_sandbox_security_evidence.json）：探针产品完整发布链（资源上传 200 → 产品 201 → submit/approve/publish）→ `failclosed.l2c_no_node` **400 RUNTIME_UNAVAILABLE（无回退，SS-13 单一路径 fail-closed 远程实证）** → 退役级别三连 400 → 披露两表面（sandbox_engine=cube_microvm + attestation trust_root 诚实）→ 节点注册表面（0 节点/0 可调度）。

## 3. 隔离面说明（远程边界）
- 命令级隔离（容器逃逸/文件越权/egress 拒绝域实测）需要 **活节点 + 活会话**——远程无 KVM 节点，此面归本机节点轨 RT-07（bring-up 后在同一脚本扩展或节点轨测试中执行，证据回填本文档 §4）。`deploy/cube-node/node_bringup.sh` 一键 bring-up 脚本已备（sudo 一条命令：30G XFS loop → /data/cubelet → preflight → install → verify --json）。
- 远程可验证的安全契约: fail-closed 无回退（本文件 §2，Round E 实证）、退役级别拒绝、披露诚实性、scope 门（02 文档负例三连）。

## 4. 节点轨隔离证据（RT-07，2026-10-14 本机 KVM 活会话，已回填）

活会话 e2e（N1-N10，**12/12**，evidence-node/live_session_v38.json）+ 特权/设备面探针（evidence-node/isolation_live.json）：

| 面 | 结果 | 证据 |
|---|---|---|
| N5 egress 拒绝域 | PASS | VM 内 urllib 出站 93.184.216.34 → `EGRESS_BLOCKED URLError`（l2c 默认无外联路由；allow 需策略面显式放开） |
| N6 宿主内容不可见 | PASS | VM /etc/passwd 无宿主用户 kratos、/home/kratos 不存在（镜像自带 /etc/passwd 为容器合法内容，断言用宿主独有内容——D-e2e-21） |
| N7 跨租户 | PASS | 另一用户对本会话 files=403 exec=403 |
| P1 宿主进程不可见 | PASS | VM /proc 仅 3 PID，无 kubelet/containerd/dockerd |
| P2 提权效果 | PASS（附注） | VM 内 UID 0 = guest root（microVM 基线，guest root ≠ host root）；mount 系统调用 DENIED |
| P3 宿主挂载不可见 | PASS | /home/kratos、/data/cubelet 均 ABSENT |
| **microVM 边界实证** | **真实** | guest 内核 6.6.1199-0009-03（CubeSandbox guest）≠ 宿主 7.0.0-30-generic；guest MemTotal ≈1GB ≠ 宿主 15GB；/dev/mem 可读但仅暴露 **guest 自身**低地址内存（iomem 0x00000000 起，guest iomem 实证）——与任何 KVM guest 一致，非宿主逃逸 |

- 上游 guest 配置特性（如实记录，非 CDS 缺陷）：guest root 可读 guest /dev/mem（仅自己 VM 的 RAM，不跨租户）；/dev/kmem 已屏蔽。收紧 guest 内部设备策略归上游 guest image 配置面（零上游 diff 约束下 CDS 不改）。
- 会话级 TEE trust_root 无硬件 `none`：能力面披露已远程实证（§1 disclosure.meta_trust_root），节点轨活会话未带 TEE 硬件（本机无 TEE），与 SS-13"TEE 可选"一致。

## 5. 结论
- **远程形态: PASS（10/10）**——fail-closed/退役拒绝/披露/注册表面全绿。
- **节点轨形态: PASS**——命令级隔离全绿（宿主进程/挂载/出站/提权效果全部隔离成立，microVM 边界真实，guest 内部设备特性已如实披露）。
- 两形态互补定稿：远程证明"无节点时不伪造、不回退"，节点轨证明"有节点时隔离真实成立"。
