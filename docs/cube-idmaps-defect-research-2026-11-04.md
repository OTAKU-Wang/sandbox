# Cube v0.7.3-rc2 cubelet「idmaps / bucket does not exist」缺陷调研报告

> 日期: 2026-11-04 · 基线: cubelet v0.7.3-rc2 (b0c4cc33) · 性质: 缺陷调研(取证+源码+上游材料) · 范围: l2c 数据面建箱失败一条链 · 编号: 关联记忆 notes/facts/2026-11-04-cube-idmaps-blocker.md

**对象**: TencentCloud/CubeSandbox Cubelet v0.7.3-rc2 (b0c4cc33, build 2026-10-12, go1.25.7, containerd v2.2.2 官方无 replace)
**结论速览**: 报错本体是 Cube 自研 patchoverlay snapshotter 与 state-tmpfs 设计组合出的**确定性死局**;但本机 13~22 轮"复发"实为**双层假象**(vite 代理回放 + 旧会话),真实缺陷在 14 轮清库后已愈。

## 1. 缺陷链(源码级,file:line 为 master 行号)

| # | 位置 | 缺陷 |
|---|------|------|
| D1 | `Cubelet/plugins/snapshots/overlay/patchoverlay/overlay.go:162-192` | NewSnapshotter 初始化用**只读**事务调 `storage.IDMap` 校验快照数==idmap 数;containerd `core/snapshots/storage/bolt.go` 的 `v1` 桶只在**可写**事务里创建 → 空 db 上 init 必报 `failed to get idmaps: bucket does not exist`(仅 log) |
| D2 | containerd `core/metadata/snapshot.go:408` | Prepare 返回 AlreadyExists 时走**只读**恢复 Walk;空 db 无 `v1` 桶 → `failed walking backend snapshots: bucket does not exist: not found` → unpack 失败 → 沙箱创建失败 |
| D3(放大器) | `Cubelet/cmd/cubelet/state_tmpfs.go` + changelog #1008(v0.6.0) | state 目录按设计挂 tmpfs(重启即清 snapshotter db),而 `data_path=/data/cubelet/root`(XFS)与 refs/snapshots 持久 → 一次 unpack 中断/重启即永久 fs↔db 撕裂,之后每次重试必触发 D2,**重启 cubelet 不能自愈** |
| D4(误导源) | `Cubelet/services/images/local.go:218` + CDS `app/services/cube_node_client.py:98/113` | ensure-image 失败串沿 ret_msg 逐层透传并持久化到会话;本例真实报错止于 09:39:55,之后全部是回放 |

## 2. 关键取证
- `go version -m` 二进制: module `github.com/tencentcloud/CubeSandbox/Cubelet/cmd/cubelet`,containerd/v2 v2.2.2 官方。
- `Cubelet-req.log` 全目录 `ensure image [` 时间戳:**最后一条 2026-11-04T09:39:55**,13~22 轮(10:35~11:25)零新增。
- PG `sandbox_sessions`: 含该错的会话**只有 1 条**,created 恰为 09:39:55;10:00 后库里 0 新会话。
- 直连 :8000 POST(启用被禁用的探测账号后)→ `RUNTIME_UNAVAILABLE no_eligible_node`,idmaps 错误**已不存在**——规避已生效。
- 远程 PVM 对照: 无 idmap 报错记录、refs 为空,从未进入撕裂态(解释"同版本能跑")。

## 3. 有效规避(官方留的门)
`state_tmpfs.go` 明示 "Non-tmpfs mounts are left alone" → 非 tmpfs 顶替即合规:
1. `mount --bind /data/cubelet/state-real /data/cubelet/state` + fstab(db 持久化,与 refs 一致);
2. 一次性清撕裂态: `root/io.containerd.snapshotter.v1.overlayfs/{refs,snapshots}`、`mstate/.../metadata.db`、`state/io.containerd.metadata.v1.bolt`、images 存储(均有 .bak-$TS 备份在位);
3. 重启 cubelet 后 `cubecli image pull` 预热(default/moby/k8s.io 三 ns 全部 `Image is up to date`)。

## 4. 本轮教训(工具链双层假象,比缺陷本身更贵)
- 轮次脚本走 `:3000`(vite 代理),旧后端死后代理层持续回放 09:39 的 201 响应体 → 六轮清理全部打在空气上;`ss -tlnp` 端口持有者 + 后端 access log 是唯一诚实证据。
- `pgrep/pkill -f 'uvicorn app.main'` 曾误杀心跳循环、`kill` 未清 setsid 子进程 → 进程管理要用 PID 文件(本次 :8000 端口实况两次骗过我们)。
- 探测账号 envdev_l2c 此前按合规要求置 is_active=false,本轮已重新启用(审计保留,不删)。

## 5. 上游 issue 草稿(English,可直接贴 TencentCloud/CubeSandbox)
> **Title**: patchoverlay init + state tmpfs make a failed unpack permanently fatal (v0.7.3-rc2)
> **Body**: On XFS data volumes with the default state tmpfs, an interrupted image unpack leaves `data_path` refs/snapshots persisted while the snapshotter metastore db is wiped at reboot. On the next pull, backend `Prepare` returns AlreadyExists and containerd v2.2.2's read-only recovery Walk (`core/metadata/snapshot.go:408`) hits `storage.IDMap` on an empty db; the `v1` bucket is only created in writable transactions, so the node fails **every** subsequent sandbox creation with `failed walking backend snapshots: bucket does not exist: not found` — restarting cubelet never heals. Additionally `NewSnapshotter` itself calls `storage.IDMap` in a read-only transaction (`overlay.go:162-192`), logging `failed to get idmaps` on any fresh db.
> **Repro**: fresh node → start `image pull` → kill cubelet mid-unpack → reboot → create sandbox: deterministic failure.
> **Suggested fix**: tolerate "bucket does not exist" in the recovery Walk and in the init idmap check (treat empty db as zero idmaps), or create the bucket in a writable txn during NewSnapshotter.
> **Workaround users can apply**: bind a persistent dir over the state tmpfs mount (state_tmpfs.go leaves non-tmpfs mounts alone).
> AI-assisted report: Assisted-by: coding-agent (per repo AGENTS.md).

## 6. 未结案(环境侧,非本缺陷)
l2c 现卡在 `no_eligible_node`: 心跳循环曾被子串误杀多次;选择器要求 heartbeat<300s + runtime_version 匹配 + cubelet endpoint(见记忆 skills/cds-e2e-flow-seed: ready_nodes 只看 status,健康探针不复原 offline→online)。下一步:核对心跳 payload 的 runtime_version 与 `pinned_cube_version()`/cubelet endpoint 字段。
