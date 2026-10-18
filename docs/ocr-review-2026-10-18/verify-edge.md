# w3 边缘波次复核（plugin/server/layout/rootfs/cli/main/selftest/alembic-0012）

判定基准：`TRUE·LIVE`（从 main.go/cli.go/server.go 真实入口可达）/ `TRUE·LATENT`（代码事实成立但无存活调用方）/ `FALSE`。
环境说明：本机 go1.18（模块要求 go1.25.0，见 cds-volume-plugin/go.mod:3），未 vendor 依赖且需 Linux（unix socket / mount / loop），故未跑 `go test`；所有判定基于逐行阅读 + 一个 stdlib 探针程序（验证 `filepath.Join/Rel` 的包含性代数）。行号均来自本次 `nl -ba` 实读。

## cds-volume-plugin/plugin.go

### [plugin.go #1] 自定义 AAD 参与 Seal 但从不写回 Manifest.AAD，卷将永久无法 Attach — **TRUE·LIVE**
证据：`cds-volume-plugin/plugin.go:140-142` `if aad == "" { aad = m.AAD }`；`plugin.go:164` `ct, tag, err := r.cipher.Seal(dek, nonce, []byte(aad), data)`；`plugin.go:183-185` 只回写 `HasData`/`DataSizeBytes`，AAD 从未更新（AAD 只在 `plugin.go:81` `AAD: name, // spec: AAD = volume name/label` 写入一次）；Attach 解密固定用 manifest 的 AAD：`plugin.go:249` `plain, err := r.cipher.Open(dek, nonce, []byte(m.AAD), ct, tag)`。调用链：`server.go:99` `s.p.PutVolumeData(ctx, req.GetVolumeId(), req.GetAad(), req.GetData())`、`cli.go:101` `p.PutVolumeData(ctx, *vid, *aad, data)`（`cli.go:78` 暴露 `--aad`）。（原描述里的“第 246 行”实际是 `plugin.go:249`。）
判定理由：机制完全成立——用非默认 AAD 写入成功后，Attach 永远以 `m.AAD` 打开 → GCM 认证必然失败（`plugin.go:251-252` fail-closed），卷变成不可挂载；重试 Put 时幂等校验用请求 AAD（`plugin.go:145`），带另一 AAD 重试则报“无法解密已存在数据”（`plugin.go:147`）。降级因素：仓库内唯一真实调用方不发 aad 字段（`app/services/cube_runtime_adapter.py:686` 只传 `volume_id`/`data`），所以默认路径安全，触发需要调用方主动传自定义 AAD；不是密钥泄露，是可用性/数据可取回性缺陷。所谓“无法表达空 AAD”属实但属细枝末节。
修法：PutVolumeData 在 `!m.HasData` 分支里把本次 aad 固化进 manifest（`m.AAD = aad`）后再 saveManifest，或直接拒绝 `aad != m.AAD`。

### [plugin.go #2] refCount>0 复用 stage 文件只做存在性检查、不做完整性校验 — **TRUE·LIVE**（结论成立，空数据那段推论有误）
证据：`plugin.go:216-223` 复用分支 `if st, serr := os.Stat(stageFile); serr != nil || st.Size() == 0 { … } else { return hostPath, attachMetadata(m, baseDir), nil }`，该分支确实早于 `plugin.go:225` `if !m.HasData`；manifest 里存了 `DataSizeBytes`（`layout.go:32`，写入于 `plugin.go:184`）但全文件从未与 `st.Size()` 比较（`grep -n DataSizeBytes plugin.go` 只命中 184）。
判定理由："复用路径不做任何完整性校验、只要文件存在就交 hostPath" 是真实代码事实且从 `server.go:48` 可达，配合 #7（Destroy 不清 stage 目录）会出现"上一个已销毁卷的明文被当作新卷内容挂进 VM"这种真实后果，因此维持 LIVE。但两个推论要纠正：(a) 空数据（`len(data)==0`）时 stage 文件 size==0，`plugin.go:218` 判为 stage lost → 每次 Attach 重新 unwrap+解密，属性能问题；写出的空文件正是调用方放入的内容，"数据丢失"不成立。(b) `Detach(refCount>0)` 只 stat（`plugin.go:319-323`）与"空文件"无关，是引用计数完全信任调用方的问题（见 #9）。
修法：复用返回前要求 `m.HasData && st.Size() == int64(m.DataSizeBytes)`，否则按 stage lost 重新解密（并在卷锁内完成）。

### [plugin.go #3] privateDataJSON 在 manifest 落盘之后才失败，绕过 abortAfterRegister — **TRUE·LIVE**（触发面极窄）
证据：`plugin.go:84-87` `if err := r.saveManifest(m); err != nil { return "", r.abortAfterRegister(...) }` 之后紧接 `return privateDataJSON(ref)`；`plugin.go:100-108` 只在 `len(b) > 1024` 时报错（`json.Marshal(map[string]string)` 不会失败）。KMS 侧对 key_id 无长度校验：`kms.go:107-109` 只拒绝空串。
判定理由：状态不一致属实且可达（`server.go:76-78` → 返回 FailedPrecondition）：DEK 已注册、manifest 已落盘，但 Create 报错；重试走 `plugin.go:57-58` 同一函数，同样超限 → 永久失败。但触发需要控制面返回 ≥1KB 的 key_id，正常 KMS 不会；且本地卷仍可用（CLI 可查），属"控制面返回异常 → 状态机不自洽"的低危问题，不宜按 medium 计。
修法：先算 `privateDataJSON(ref)` 再 saveManifest；或让 RegisterDEK 对 key_id 做长度/字符白名单校验（fail-closed）。

### [plugin.go #4] Plugin 无每卷互斥；失败分支回滚不完整 — **TRUE·LIVE**（回滚一半描述不准）
证据：`plugin.go:3-15` 导入里没有 `sync`；`plugin.go:19-27` Plugin 只有 cfg/cipher/kms，无任何锁；`server.go:165-168` 单个 `grpc.NewServer()` 注册三个服务，gRPC 每请求一 goroutine；`layout.go:95-101` `tmp := path + ".tmp"` 固定名 + `os.WriteFile`（仅 O_TRUNC，无 O_EXCL）+ `os.Rename`。`plugin.go:144` 并发双 Put 都看到 `!m.HasData`，各自 `plugin.go:164` Seal 后依次 `plugin.go:171-182` 写 ct/nonce/tag。
判定理由：撕裂写成立且无保护：两个并发首 Put 可以让磁盘上的 ct/nonce/tag 来自不同 nonce 的两轮 Seal，Attach 必然失败（`plugin.go:249-252`）。回滚部分需纠正：tag 写失败时并不存在"tag 孤儿文件"（rename 未成功就没有 tag），残留的是 `data.tag.tmp`；manifest 无需回滚——`HasData` 只在内存里被置位（`plugin.go:183`），失败时磁盘 manifest 仍是 `has_data=false`，与删除 ct/nonce 后的状态一致。medium 合理，按撕裂写计。
修法：Plugin 内加 `map[string]*sync.Mutex` 按 volumeID 串行化，并把 `writeAtomic` 换成 `os.CreateTemp` 唯一名 + 失败删除。

### [plugin.go #5] Attach/Detach/Put/Destroy/Get 均不调用 validateVolumeID；stageDir 包含性自检可被绕过 — **TRUE·LIVE**（校验缺失成立，stageDir 绕过论证 FALSE）
证据：`grep -rn validateVolumeID cds-volume-plugin/*.go` 只有两处：定义 `plugin.go:29` 与唯一调用 `plugin.go:50`（CreateVolume 内）。Attach(`plugin.go:199`)、PutVolumeData(`plugin.go:133`)、Detach(`plugin.go:314`)、Destroy(`plugin.go:381`)、GetVolumeInfo(`plugin.go:404`) 直接用调用方 volumeID；`layout.go:116-119` 的自检为 `d := filepath.Join(base, "cds-"+volumeID)` + `rel[:3] == "../"`。
判定理由：入口校验缺失是真实且可达的（Cubelet/控制面可传任意 volumeID），真正的越界面在 `layout.go:57-66 paths()`（`filepath.Join(r.cfg.VolumeRoot, volumeID)` 无 "cds-" 前缀中和）。但本条对 stageDir 的论证不成立：`cds-`+volumeID 的第一个路径段永远以 `cds-` 开头，Clean 无法把它化简成 `..`；volumeID 含 `/` 时才可能外逸，而这种情况 rel 会变成 `../…` 被拒。我用 stdlib 探针复算了同一代数（`Join(base,"cds-"+id)` → `Rel`）：`..`→`cds-..`（base 内合法目录）、`/../../etc`→rel `../etc`（Linux 下被 `plugin`/`layout.go:118` 拒）、`a/../../x`→rel `../x`（拒）；而 `paths()` 对 `../../etc/x` 直接给出 `…/lib/etc/x`（越界）。故"cds-.. 被判为仍在 base 内 = 校验失效"是把合法目录误读成逃逸。
修法：在 Attach/Detach/PutVolumeData/Destroy/GetVolumeInfo 入口统一 `validateVolumeID`（并让 `paths()` 返回 error），不要改 stageDir 的判定语义。

### [plugin.go #6] shredFile 不做 Sync 就 unlink；覆盖失败仍删文件且错误文案失真 — **TRUE·LIVE**
证据：`plugin.go:345-373` 全文无 `Sync`：`plugin.go:363` `f.WriteAt(chunk[:n], off)` → `plugin.go:367` `f.Close()` → `plugin.go:370` `os.Remove(path)`（无条件执行，即使 firstErr 已非 nil）。调用方文案：`plugin.go:334-336` `return fmt.Errorf("stage shred failed, plaintext retained: %w", err)`。
判定理由：两点均为代码事实且从 `server.go:58`（Detach）与 `server.go:84`（Destroy）可达。零写未落盘即 unlink，掉电/快照回滚后原明文仍可能被恢复，与"session 结束即擦除"的承诺存在真实差距（注释自己也承认 best effort：`plugin.go:341-342`）；写失败路径下文件其实已被 unlink，而错误却声称 "plaintext retained"，运维判断会被误导。属正确性/诚实降级问题而非密钥泄露，medium 可下调为 low-medium。
修法：unlink 前 `f.Sync()`；`firstErr != nil` 时保留文件并返回"未擦除但已删除/未删除"的准确状态。

### [plugin.go #7] Destroy 不清 staging 明文，也不看引用计数 — **TRUE·LIVE**
证据：`plugin.go:388-398` Destroy 只 shred `p.ct/p.nonce/p.tag/p.mani` 并 `os.Remove(p.dir)`；`stageDir` 在 plugin.go 只出现于 Attach(`210`)/Detach(`314`)，Destroy 完全不涉及；Detach 定位 stage 目录纯靠调用方 base_dir（`plugin.go:314`），与 manifest 无关，正如所述。
判定理由：LIVE：`server.go:84` / `cli.go:118` 可直接销毁仍在挂载使用的卷（DestroyRequest 无引用计数，服务端也没有任何记账），0400 明文永久留在 `<base>/cds-<vid>`；随后 Detach 仍能"成功"（走 `plugin.go:325-338`），但 manifest 已不存在 → 无法再 Attach/审计。缓和因素：DEK 已先 crypto-erase（`plugin.go:385-387`），残留明文不再是"可再分发的密钥保护对象"，但它仍是可被任意 root 读取的会话数据。medium 成立。
修法：Attach 时按 volumeID 记录 stage 路径，Destroy 前拒绝（或一并 shred）已知 stage，并要求 refCount==0 证明。

### [plugin.go #8] writeAtomic 固定 `.tmp` 名：并发互踩 + 残留 0400 tmp 导致 Attach 永久失败 — **TRUE·LIVE**（第二半仅非 root 部署成立）
证据：`layout.go:96-100` `tmp := path + ".tmp"` / `os.WriteFile(tmp, data, perm)` / `os.Rename(tmp, path)`，无唯一名、无失败清理。Attach 以 0400 写 stage：`plugin.go:259` `writeAtomic(stageFile, plain, 0400)`。
判定理由：两个 sandbox 并发 Attach 同一 volumeID（refCount==0，`plugin.go:216` 判定都不成立）→ 同一 `data.tmp` 被两路 O_TRUNC 交叉写再各自 rename，交给 VM 的明文可能被混写/截断，且 GCM 校验发生在写出之前，无法在 stage 阶段发现——该半段无条件成立。第二半仅在插件非 root 时成立：perm 只在创建时生效，已存在的 0400 tmp 对非 root 会 EACCES 且无自愈路径；但本插件在生产是 root（`cds-volume-plugin/Dockerfile` 无 USER 指令，`rootfs.go:86` 的 mount 需要 CAP_SYS_ADMIN），root 不受 DAC 限制，仓库自身也预期非 root 只在 dev（`server.go:161-162` "need root; expected in dev"）。
修法：`os.CreateTemp(dir, ".stage-*.tmp")` + 写后 Sync + rename 失败时 shred/删除 tmp，并在 Attach 入口按 volumeID 加锁。

### [plugin.go #9] Detach 完全信任调用方 refCount 与 baseDir，无兜底 — **TRUE·LIVE**
证据：`server.go:58` `s.p.Detach(ctx, req.GetVolumeId(), req.GetMetadata()["base_dir"], req.GetRefCount())`；`plugin.go:319-324` refCount>0 且文件存在即 `return nil`；`plugin.go:326-329` 文件不存在则 best-effort rmdir 后 `return nil`（base_dir 传错 → 路径下没有 data → 走这条静默成功分支）。全模块无任何 TTL/驻留时间/stage 记账（`grep -rn "ttl|expiry" cds-volume-plugin/*.go` 无命中）。
判定理由：与描述一致，属 low：明文长期驻留与"传错 base_dir 静默成功"都是真实语义，但攻击者需已具备调用该 root-only socket 的权限（`server.go:149` 0600），因此是信任模型/可靠性问题，不是新增越权面。与 server.go#6 同源。
修法：Attach 时把 stage 路径落到本地按 volumeID 的记账（文件或内存表），Detach 交叉校验并对不一致告警；加最大明文驻留时间。

### [plugin.go #10] Destroy 丢弃 shredFile 错误，销毁失败仍报成功 — **TRUE·LIVE**
证据：`plugin.go:389-395` `for _, f := range []string{p.ct, p.nonce, p.tag, p.mani} { … _ = shredFile(f, fi.Size()) }`，随后 `plugin.go:396-399` 只在 `os.Remove(p.dir)` 非 ENOENT 时返回错误，否则 `return nil`。
判定理由：属实且可达（`server.go:84`），与本文件对 shred 失败的 fail-closed 立场自相矛盾（对照 `plugin.go:334-336` Detach 的处理，以及 AGENTS.md "shred failure = error"）。但降级：此时 DEK 已在 `plugin.go:385` 撤销，残留的密文已不可解密，泄露风险接近零，真实危害是"控制面误判已彻底销毁"与排障信息缺失；`os.Remove(p.dir)` 在残留 `*.tmp`（见 #8）时确实会失败并返回，只是原因被吞掉的 shred 错误无法体现。low 合适。
修法：把每次 shred 的错误 `errors.Join` 起来并入返回值，非空即返回失败。

### [plugin.go #11] 幂等校验解出的明文 existing 从不 zeroize — **TRUE·LIVE**
证据：`plugin.go:145-152` `existing, err := r.readStagedPlain(...)` 后直接 `bytes.Equal(existing, data)` 并在 150/152 返回，无 zeroize；`plugin.go:284-307` readStagedPlain 内部只 `defer zeroize(dek)`（302），把 `plain` 原样返回（307）。对照 Attach 里明文三处都清零：`plugin.go:256 / 260 / 263`。
判定理由：本文件对 dek/plain 的一致性要求确实被这条路径漏掉，LIVE（幂等重试是常规路径，`server.go:99`）。属内存卫生：明文在 Go 堆上驻留到 GC，进程常驻（serve）时窗口不小；但不是"返回未认证明文"那类红线，low 合适。`data` 由调用方持有（gRPC 解码缓冲），不在插件的清零责任范围。
修法：`existing` 取到后立即 `defer zeroize(existing)`（在读回错误检查之后）。

## cds-volume-plugin/server.go

### [server.go #1] socket 强制 0600 与 “root:cds 组成员可 dial” 的访问模型自相矛盾 — **TRUE·LIVE**（TOCTOU 子项为条件成立）
证据：`server.go:149` `os.Chmod(cfg.SocketPath, 0600)`；`server.go:152-157` 注释“socket root:cds 0600 … Cubelet's runtime user must be in that group to dial the socket” 紧接 `os.Chown(cfg.SocketPath, 0, gid)`；`server.go:141` `os.MkdirAll(socketDir, 0711)` → `145` `net.Listen` → `149` Chmod。镜像侧把同一矛盾写进了构建：`cds-volume-plugin/Dockerfile:43-47`（“prod identity per spec §2.3: socket root:cds 0600, Cubelet runtime user is a member of the cds group” + `groupadd --system cds` + `useradd --gid cds cubelet`）。
判定理由：0600 的组位为空，非 root 的 cds 组成员 dial 必然 EACCES ——要么 cubelet 以 root 跑（那这段 chown 与注释纯属误导运维），要么真实需求是 0660。代码/契约矛盾确定成立且随镜像交付（LIVE）。降级依据：实际容器运行时 cubelet 基本以 root 跑，Dockerfile 也没有 USER（插件自身 root），所以“生产 Attach 全挂”是推断而非观察到的故障。TOCTOU：窗口真实存在，但 Linux 上 connect 需要 socket 文件的写位，默认 umask 022 下新建 socket 是 srwxr-xr-x（others 无 w）→ “本机任意非特权用户都能 connect”仅在宽松 umask（002/000）下成立，属夸大。
修法：选定信任模型并三处同步（代码/Dockerfile/spec 注释）：要么 0660 root:cds，要么 0600 root:root 并删掉组逻辑；Listen 前临时收紧 umask。

### [server.go #2] 同一 volume_id 的并发 Attach/Detach/Put 在本层无限序 — **TRUE·LIVE**
证据：`server.go:165-168` `srv := grpc.NewServer()` 无任何 interceptor/限流，三个 service 共用同一个无锁 Plugin（`plugin.go:19-27`）；check-then-act 在 `plugin.go:218`（Stat stageFile）与 `plugin.go:259`（writeAtomic）；固定 tmp 名 `layout.go:96`；Detach 的 shred 在 `plugin.go:334`。
判定理由：LIVE：gRPC 每请求一 goroutine，Attach(refCount>0) 的复用判定与并发 Detach/Attach 之间无任何隔隔；Attach 与 Detach 交错可得到“先 shred 后写明文”→ 引用计数已归零、不会再来第二次 Detach → 明文永久驻留。与 plugin.go#4/#8、layout.go#2 同根（应在本层或 Plugin 内统一修）。
修法：入口层加 keyed mutex（按 volume_id）把 Attach/Detach/Put/Destroy 串行化，并改掉固定 tmp 名。

### [server.go #3] ReadSandboxFile 把 guest 可控内容当可信输入，root 身份读宿主任意文件 — **TRUE·LIVE（critical，本波次首要项）**
证据：`rootfs.go:98-111` 先 `os.Lstat(src)`，只拒目录（`rootfs.go:105-107` `if fi.IsDir() { return nil, false, nil }`），尺寸上限比较的是 Lstat 自身（`rootfs.go:108-110`，符号链接时仅为目标字符串长度），最后 `rootfs.go:111` `data, err = os.ReadFile(src)`——`os.ReadFile` 会跟随符号链接，绝对目标在宿主根命名空间解析。挂载以 root 进行（`rootfs.go:86` `exec.Command("mount", "-o", "ro,loop", ...)` 需 CAP_SYS_ADMIN）。upper 层由 guest 写入：`rootfs.go:17-25` 注释明确“guest writes flow through it”。调用链：`server.go:124-133` → 控制面 `app/services/cube_runtime_adapter.py:593`，读回路径由 guest 写入的文件驱动（`cube_runtime_adapter.py:923 / 938` 用 `results/.cds-exec/<exec_id>` 轮询），`output` 最终回给发起 exec 的一方（`cube_runtime_adapter.py:941-947`）。
判定理由：guest 可在自己 rootfs 里放 `results/.cds-exec/<exec_id> -> /etc/shadow`（沙箱内执行不可信代码是本产品的正常威胁模型），宿主侧就会以 root 读宿主文件并把字节交给控制面；同时 size 校验被绕过（链自身长度）、FIFO/字符设备（guest 可 mkfifo，`fi.IsDir()` 不拦）会让 `os.ReadFile` 永不 EOF → 插件进程 OOM/挂死，卸载 defer 被挂住→ loop 设备泄漏。未限定常规文件、未用 O_NOFOLLOW/openat2、事后重校验，完全符合“只拒目录”的描述。按铁律衡量：这不是“缺少一个可选校验”，而是把不可信输入送进了一个 root 文件读原语，critical 成立。唯一不确定的是单次可外泄的字节量受控制面 `out_size` 截断（`cube_runtime_adapter.py:933`）限制，但存在性 oracle + 前缀泄露已足够定性。
修法：`openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)` 或逐级 openat+O_NOFOLLOW 解析；只允许 `fi.Mode().IsRegular()`；打开后用 fstat 重校验并在 fd 上限长（而非预算 size）。

### [server.go #4] 无条件删除 socket 重建，缺单实例互斥 — **TRUE·LIVE**
证据：`server.go:144` `_ = os.Remove(cfg.SocketPath)` 紧接 `server.go:145` `net.Listen("unix", cfg.SocketPath)`；全模块无 dial 探活/flock/pid 文件（`grep -rn "flock|pidfile|Lock" cds-volume-plugin/*.go` 无命中）。
判定理由：LIVE（`main.go:23-25` serveCmd → `server.go:139` Serve）。systemd 双起 / 运维手动再起一个 serve 会直接掉活实例的 socket，两进程共享同一 VolumeRoot，并发 Detach/Destroy 会对同一明文/密文树互踩（叠上 #2 无锁），先实例的在途连接静默断开。medium 合理。
修法：Listen 前先试 dial，连通即报“already serving”退出；仅对 dial 失败的 stale socket 做 Remove；或对 socket 目录 flock。

### [server.go #5] mapErr 分类过粗、丢弃错误身份、把 err.Error() 直接回传 — **TRUE·LIVE**
证据：`server.go:25-35` 只有 `IsVolumeNotFound` → NotFound，其余全量 `codes.FailedPrecondition` 且原文透出。实为参数错误的下游例子：`plugin.go:30-31` invalid volume_id、`layout.go:110` `volume_base_dir must be absolute`、`layout.go:119` `host_path escapes volume_base_dir`、`plugin.go:121` `private_data not valid JSON`；KMS/后端故障同样落入 default：`kms.go:74`、`kms.go:79`、`plugin.go:386` `DEK revoke failed`。错误文案里带宿主路径：`rootfs.go:77`、`plugin.go:397`。
判定理由：三项均属实且逐 RPC 可达（五个 handler 都调 mapErr）。实际后果排序：非法输入被当作可重试的 FailedPrecondition（无效 volume_id 会被上游无限重试）> 不可用/需人工介入与业务失败不可区分 > 信息泄露（该 socket 是 root/cds 0600，接收方本就是控制面，泄露面有限）。medium 合理；`errors.Is`/`status.FromError` 未使用属实。
修法：在 Plugin 层用类型化错误（InvalidArgument/Unavailable/Internal/DataLoss），handler 按类型映射 gRPC code，对外只给简短稳定消息，详情进日志。

### [server.go #6] Detach 缺必填校验，失败路径直接造成明文残留 — **TRUE·LIVE**
证据：对比 `server.go:45-47`（Attach 校验 volume_id/volume_base_dir 并返回 InvalidArgument）与 `server.go:57-58`（Detach 零校验，metadata 可为 nil）。base_dir 缺失/为空 → `layout.go:109-111` `volume_base_dir must be absolute` → `plugin.go:314-316` 直接返回，shredFile 根本未执行；volume_id 为空但 base_dir 合法 → `stageDir` 返回 `<base>/cds-` → `plugin.go:325-329` 文件不存在 → best-effort rmdir 后 `return nil`（静默成功）。
判定理由：两条都是真实可达路径，且与描述一致：插件的核心安全承诺（session 结束即擦除明文，`plugin.go:310-312` 注释）可以在 Cubelet 重启/metadata 丢失时静默失效；最恶劣的是空 volume_id 情形会返回成功。medium 合理（medium 中偏上）。
修法：Detach 入口同样校验 volume_id/base_dir 并返回 InvalidArgument；base_dir 缺失时回退到 Attach 时记录的 stage 路径尽力擦除；“明文未擦除”用独立可告警码（DataLoss/Unavailable）。

### [server.go #7] ReadSandboxFile 在处理路径上执行不受控阻塞宿主操作，无取消/无并发上界 — **TRUE·LIVE**
证据：`server.go:124-133` 收了 `ctx` 但完全不用（`s.p.ReadSandboxFile` 无 ctx 参数，`rootfs.go:46`）；`rootfs.go:86` `exec.Command("mount", ...)` / `rootfs.go:91` `exec.Command("umount", ...)` 都无 context/超时；单次上响 64MiB（`rootfs.go:29` `maxSandboxFileBytes = int64(64*1024*1024)`）；`server.go:165` `grpc.NewServer()` 未设 MaxConcurrentStreams/max 内存；`server.go:131` `status.Error(codes.Internal, err.Error())` 外泄绝对路径。调用方是轮询循环：`cube_runtime_adapter.py:920-945`（每 0.5s 一次）。
判定理由：全部属实：mount 对后备对象文件做 journal recovery / 后端 I/O 卡住时 handler 会无限挂起且客户端取消无法中断（因为根本没把 ctx 传下去）；轮询 × 并发 × 64MiB 无上限可把内存放大到 GB 级；loop 设备/挂载点泄漏另见 rootfs.go#4。medium 合理。
修法：`exec.CommandContext(ctx, …)` + `cfg.SubprocTimeout`；按 cubebox 串行化的信号量限流；对外统一 codes.Internal + 固定消息。

### [server.go #8] RPC 边界未对 volume_id 做白名单校验 — **TRUE·LIVE**（影响面被下调）
证据：handler 只校 volume_id 非空：`server.go:45`（Attach）、`server.go:73`（Create）；Destroy(`server.go:84`)、GetVolumeInfo(`server.go:107`)、PutVolumeData(`server.go:99`) 直接透传。越出 VolumeRoot 发生在 `layout.go:58` `filepath.Join(r.cfg.VolumeRoot, volumeID)`。
判定理由：缺失校验属实且调用可达（`../../…` 确实能跳出 VolumeRoot），但本条声称的“探测/读取任意位置 manifest”“变成任意文件销毁”不可达：四个破坏/读取路径都要先过 `loadManifest`，而 `layout.go:81-83` `if m.VolumeID != volumeID { return … manifest volume_id mismatch (fail-closed) }` 要求目标目录里已有一份 `volume_id` 字段恰好等于 `../../etc/x` 的 manifest；而 `plugin.go:50` 使 CreateVolume 永远写不出这种 volume_id，所以除非攻击者已能在宿主写 manifest，该越界无法造成实际读/删。属 defense-in-depth（应修，low）。
修法：各 handler 入口统一 `validateVolumeID(req.GetVolumeId())` 并返回 InvalidArgument（Detach 同理），使“无分隔符短标识符”成为服务端强约束。

### [server.go #9] 无 recovery interceptor、未设消息尺寸/并发上限 — **TRUE·LIVE**（消息上限是硬性功能缺陷）
证据：`server.go:165` `srv := grpc.NewServer()` 零选项、零 interceptor；`config.go:64` `MaxVolumeBytes: envInt64("CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES", 1<<30)`（默认 1GiB）；Python 侧无任何 channel option 也不分块：`cube_runtime_adapter.py:682-688`（`grpc.insecure_channel(f"unix://{socket_path}")` + 单次 `stub.PutVolumeData(... data=data)`）。
判定理由：两项均 LIVE。grpc-go 默认接收上限 4MiB，而插件自定上限 1GiB、控制面不分块也不抬上限 → 任何 >4MiB 的会话数据在传输层被 RESOURCE_EXHAUSTED 拒掉，到控制面变成与业务无关的 INTERNAL_ERROR（`cube_runtime_adapter.py:690-694`）——默认 1GiB 上限实际上不可用。panic 部分：本层确实未注册任何 recover，而处理路径上有大量索引/类型操作（如 `selftest.go:44/50` 的无守卫翻转、`rootfs.go:111` 后的 64MiB 分配），handler panic 会带下整个进程，正在进行的 Detach 停在 shred 中途→明文残留。
修法：`grpc.MaxRecvMsgSize` 与 MaxVolumeBytes 对齐（或引入分块上传），加 recovery interceptor，并设 MaxConcurrentStreams。

### [server.go #10] 缺优雅停机路径 — **TRUE·LIVE**
证据：`grep -rn "signal.Notify|GracefulStop|NotifyContext" cds-volume-plugin/*.go` → 零命中；`server.go:171` `return srv.Serve(lis)`；`main.go:56` `return Serve(cfg, p)`（唯一入口，无信号处理）。
判定理由：LIVE 且后果真实：多步操作会被打断在中间态——CreateVolume 的“KMS 已注册但 manifest 未落盘”（`plugin.go:67-86`）、Detach/Destroy 的 shred 中途（`plugin.go:334 / 394`）、ReadSandboxFile 的 loop 挂载（`rootfs.go:86`）。medium 合理（与 main.go#3 同根）。
修法：`signal.NotifyContext` + `srv.GracefulStop()`（超时降级 `Stop()`），并把 ctx 透传到文件/子进程操作。

## cds-volume-plugin/layout.go

### [layout.go #1] exists() 把任何 Stat 失败都当作“卷不存在” — **TRUE·LIVE**
证据：`layout.go:44-47` `func (p volumePaths) exists() bool { _, err := os.Stat(p.mani); return err == nil }`；`layout.go:68-72` `if !p.exists() { return nil, ErrVolumeNotFound{ID: volumeID} }`；`server.go:30-31` 把它映为 `codes.NotFound`。
判定理由：错判分类且后果不对称：`plugin.go:57-61` 只在 NotFound 时继续创建 → EACCES/ELOOP/EIO/瞬时卸载会让 Create 当作新卷：向 KMS 注册新 DEK（`plugin.go:67`）并 `saveManifest` 覆盖旧 manifest（`plugin.go:84`），旧 `data.ct` 从此永久不可解密且旧 DEK 成为孤儿密钥——不可逆数据丢失。运维面同样属实：真实 I/O 故障对外展示为“卷不存在”。降级依据：插件以 root 跑（Dockerfile 无 USER），root 下 EACCES 几乎不可能，现实触发源是 EIO/ENOTDIR/ELOOP/瞬时挂载丢失，概率低但机制成立。high 可下调为 medium。
修法：`exists()` 返回 `(bool, error)`，只在 `os.IsNotExist(err)` 时视为不存在，其余错误原样上传（fail-closed）。

### [layout.go #2] 固定临时名 + 无 fsync 的“原子写” — **TRUE·LIVE**
证据：`layout.go:95-101` 全文：`tmp := path + ".tmp"` → `os.WriteFile(tmp, data, perm)` → `os.Rename(tmp, path)`；无 `Sync`、无父目录 fsync、无失败清理。
判定理由：两半均成立。并发互踩：`plugin.go:171/174/178` 对同一卷连调三次 writeAtomic，两个并发 Put（gRPC 并发 + 客户端重试，`server.go:99`）会交叉写同一 `data.ct.tmp` 并各自 rename → 可能落盘“新 ct + 旧 nonce/tag”这类撕裂组合，卷从此永久解不开；Attach 写 stageFile 与 Detach shredFile 并发时 rename 还可能把已 shred 的文件换回明文。持久性：`WriteFile` 后未 Sync，rename 可先于数据块落盘变得对目录可见 → 崩溃后 `manifest.json` 长度 0 → `layout.go:78-80` `manifest corrupt`，卷不可用。medium 合理。
修法：`os.CreateTemp(dir, base+"*.tmp")` + 写完 `f.Sync()` + rename 后对目录 `Sync`，失败路径删除 tmp。

### [layout.go #3] loadManifest 应如实区分 Stat/Read 错误与不存在 — **TRUE·LIVE**（#1 的伴生项）
证据：`layout.go:73-76` `b, err := os.ReadFile(p.mani); if err != nil { return nil, err }`；exists() 与 ReadFile 之间被并发删除时，返回的是裸 ENOENT，经 `server.go:32-33` 变成 FailedPrecondition。
判定理由：与描述一致，是 #1 的另一半（错误分类），LIVE 但单独修复价值低；另外 TOCTOU 窗口（exists → ReadFile）本身无可利用价值（同进程 root 目录 0700，`plugin.go:73` MkdirAll 0700）。
修法：ReadFile 的 `os.IsNotExist(err)` 显式映射为 `ErrVolumeNotFound`；Stat 非 ENOENT 错误不再转 NotFound（见 #1）。

### [layout.go #4] paths() 是 VolumeRoot 约束的唯一汇聚点，但未净化 volumeID — **TRUE·LIVE**（影响面被下调）
证据：`layout.go:57-58` `dir := filepath.Join(r.cfg.VolumeRoot, volumeID)`；`validateVolumeID` 全模块仅 `plugin.go:50`（CreateVolume）调用；gRPC 侧只校非空（`server.go:45/73`）。探针验证：`filepath.Join("/var/lib/cds/volumes", "../../etc/x")` → `…/lib/etc/x`（跳出）。
判定理由：本条与 server.go#8 同一事实，但“一旦被绕过，Destroy 会对任意路径 shredFile + os.Remove → 任意文件销毁”这个“一旦”不可达：`Destroy`(`plugin.go:381`) 以 `loadManifest` 为入口，而 `layout.go:81-83` 的 `m.VolumeID != volumeID` 比对要求目标目录已存在一份 volume_id 完全等于穿越串的 manifest，正常写入链永远造不出它。按铁律“校验已在别处”精神：这里属汇聚点防御缺失，应修但 low。
修法：让 `paths()` 返回 `(volumePaths, error)` 并在内部调用 `validateVolumeID`，在构造点集中 fail-closed。

### [layout.go #5] 失败路径不清 tmp；Detach 只 shred data；WriteFile 无 O_EXCL/O_NOFOLLOW — **TRUE·LIVE**
证据：`layout.go:97-100`（`os.WriteFile` 成功、`os.Rename` 失败时直接 return err，tmp 无人清理）；`plugin.go:318` `stageFile := filepath.Join(hostPath, "data")`（Detach 只认这一个文件）；`plugin.go:337` `_ = os.Remove(hostPath)`（目录非空必失败且错误被丢弃）→ `plugin.go:338 return nil`；`layout.go:97` 用的是 `os.WriteFile`（O_WRONLY|O_CREATE|O_TRUNC，无 O_EXCL/O_NOFOLLOW）。
判定理由：链条完全成立：ENOSPC/权限/跨设备导致 rename 失败 → 带完整明文的 `<stage>/data.tmp` 残留 → 下次 Detach shred 掉 `data` 后 `os.Remove(hostPath)` 因非空失败且被吞 → Detach 向 Cubelet 返回成功而明文仍在宿主，直接违反“session 结束即擦除”的核心承诺。符号链接跟随部分需要权限前提：stage 目录是 root 0700（`plugin.go:255` MkdirAll 0700），能预置同名 `data.tmp` 链接的只有 root，故该项属加固而非独立攻击面。medium 合理。
修法：失败时先 shred 再删 tmp；Detach 改为扫整个 stage 目录（而非只 shred 固定名）并用 `os.RemoveAll` 且上报错误。

### [layout.go #6] stageDir 的包含性校验是纯词法的，不解析符号链接 — **TRUE·LIVE**（medium-low）
证据：`layout.go:105-121` 只有 `filepath.IsAbs`/`Abs`/`Join`/`Rel`，全文无 `EvalSymlinks`/`Lstat`；而 `layout.go:103-104` 注释把它描述为 upstream “host_path ⊂ base_dir 的 self-proof”。`plugin.go:255` `os.MkdirAll(hostPath, 0700)` 对已存在的 symlink→目录确实静默成功（Stat 跟随），`plugin.go:259` 随后写入链接目标。
判定理由：词法校验不阻止符号链接是真实缺陷，注释夸大了它的保证。但可利用性受限：`baseDir` 不来自 guest，而来自 Cubelet 请求（`server.go:48` `req.GetVolumeBaseDir()`）——要利用需能写 Cubelet 指定的 base 树或控制 Cubelet 配置；另外 volumeID 含 `..` 时返回 `<base>/cds-..` 这类合法但奇怪的目录（探针输出），Detach 会去洗那个目录，不构成逃逸。与 plugin.go#5 不同，本条对 stageDir 的批评是止于“符号链接”的，描述准确。
修法：对 `base` 做 `filepath.EvalSymlinks`，对已存在的 `d` 做 `os.Lstat` 拒绝 `ModeSymlink`，再作包含性判定；并确保 base 不被非特权方控制。

## cds-volume-plugin/rootfs.go

### [rootfs.go #1] 只拒目录，未拦 symlink/特殊文件 → root 读宿主任意文件 + DoS — **TRUE·LIVE（critical）**
证据：`rootfs.go:98` `fi, err = os.Lstat(src)`（不跟随链接）→ `rootfs.go:105-107` 仅 `fi.IsDir()` → `rootfs.go:108` `if fi.Size() > maxSandboxFileBytes`（符号链接时是目标字符串长度）→ `rootfs.go:111` `data, err = os.ReadFile(src)`（跟随链接）；从未 `Mode().IsRegular()`、无 O_NOFOLLOW/openat2、无打开后重校验。src 在 guest 可写的 upper 层：`rootfs.go:97` `src := filepath.Join(mountPoint, rootfsUpperSubdir, cubeboxID, "upper", rel)`，`rel` 由调用方 vm_path 清洗而来（`rootfs.go:55-59`）。
判定理由：与 server.go#3 同一发现（不重复计新项，计同一问题的另一表述）。补充两点本文件独有的事实：(1) FIFO/字符设备同样能穿过 `IsDir()` 过滤，`os.ReadFile` 对 `/dev/zero`、FIFO 永不 EOF → 插件内存无上界且 `rootfs.go:90-95` 的 umount defer 被挂住 → 挂载点/loop 设备泄漏；(2) 挂载期间 guest 持续写，Lstat 与 ReadFile 之间的 TOCTOU 窗口真实存在。root 身份由 `rootfs.go:86` 的 mount 要求（CAP_SYS_ADMIN）保证，插件镜像无 USER 指令。唯一保留：外泄字节量受控制面截断（`cube_runtime_adapter.py:933`），但原语本身无争议，critical 成立。
修法：`openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)` 打开 + 要求 `Mode().IsRegular()` + 基于 fd 的限长读取，打开后 fstat 重校大小/类型。

### [rootfs.go #2] cubeboxID 未滤 glob 元字符，多匹配静默取 matches[0] — **TRUE·LIVE**
证据：`rootfs.go:47` 只拒空串/长度>200/含 `/``\`/`.`/`..`（`*?[` 全部放行）；`rootfs.go:65-69` `filepath.Glob(... "sb-<cubebox>-cds-rootfs-*")` → `volDir = matches[0]`；`rootfs.go:71-78` 只校 image 自身不是目录（`fi.IsDir()`），未校 `volDir`。关键存活证据：仓库内调用方永远传空 session_id（`app/services/cube_runtime_adapter.py:923`、`:938` 均为 `self._read_sandbox_file, sandbox_id, "", vm_*_rel`）→ glob 分支就是生产主路径。
判定理由：两个子项都真实：同一 cubebox 存在多个陈旧 session 目录时 `matches[0]` 只是字典序第一个 → 可能读到已销毁 session 的旧镜像，使调用方在轮询 marker 时误判 exists/内容（这条无需任何攻击者，纯逻辑缺陷且已在用）。元字符跨租户面（传 `*` 匹配其他 cubebox 的镜像）则需要 cubebox id 本身含 `*`，而该 id 来自控制面自生成的 sandbox id（`cube_runtime_adapter.py:868 _parse_container_id`），目前不属用户可控 → 该半段按 defense-in-depth 计。medium 合理。
修法：白名单 `^[A-Za-z0-9._-]+$`；不匹配改用 `os.ReadDir` + 前缀比较；多匹配返回歧义错误或按 mtime 最新选择并记日志；同时确认 `volDir` 是目录。

### [rootfs.go #3] mount/umount 无 context 与超时 — **TRUE·LIVE**
证据：`rootfs.go:86` `mnt := exec.Command("mount", "-o", "ro,loop", image, mountPoint)` + `rootfs.go:87` `mnt.CombinedOutput()`；`rootfs.go:91` `exec.Command("umount", mountPoint).Run()`；函数签名 `rootfs.go:46 ReadSandboxFile(cubeboxID, sessionID, vmPath string)` 根本没有 ctx，而上游 handler 手里有却不用（`server.go:124-128`）。
判定理由：与描述一致，全模块无任何子进程超时应用于此处（`config.go:60` 的 `SubprocTimeout` 只给 cipher helper，`cipher.go:61`）。底层 I/O 卡住时请求无限挂起、客户端取消无效、临时目录与 loop 设备清理同步被拖（defer 在返回时才跑）。medium 合理（与 server.go#7 同根）。
修法：`exec.CommandContext(ctx, ...)` 并套 `cfg.SubprocTimeout`；umount 失败退化为 `umount -l`。

### [rootfs.go #4] umount 失败后的清理被静默丢弃，挂载点/loop 持续泄漏 — **TRUE·LIVE**
证据：defer 为 LIFO——`rootfs.go:84` `defer os.RemoveAll(mountPoint)`（注册最早，执行最晚）晚于 `rootfs.go:90-95` 的 umount；umount 失败只 `log.Printf`（`rootfs.go:92-93` 注释“best-effort teardown”），而 `os.RemoveAll` 的 error 完全丢弃。`rootfs.go:42-44` 注释自己写了“崩溃后的残留挂载无害”。
判定理由：成立：umount busy 时后续 `RemoveAll` 在仍挂载的目录上必失败 → 临时目录与对应 loop 设备永远不回收；调用方是轮询循环（`cube_runtime_adapter.py:920-945`，每 0.5s 一次，上限 600s），长期运行下 `/run/cds-volume-plugin` 与 loop 表会累积，最终 mount 失败导致整个功能不可用。medium 合理。
修法：umount 失败用 `umount -l` 重试；记录 `RemoveAll` 错误；启动/失败路径上扫描 /proc/mounts 清理 `/run/cds-volume-plugin` 下残留只读挂载。

### [rootfs.go #5] MkdirTemp 依赖 `/run/cds-volume-plugin` 预先存在，本文件从未创建它 — **TRUE·LIVE**（low；附带理由有误）
证据：`rootfs.go:80` `os.MkdirTemp("/run/cds-volume-plugin", "ro-")` 硬编码路径；本文件无 `MkdirAll`（全文 grep：rootfs.go 里无 MkdirAll）；`config.go:56` `SocketPath: envStr("CDS_CUBE_VOLUME_PLUGIN_SOCKET", "/run/cds-volume-plugin/cds.sock")`；`server.go:140-141` `os.MkdirAll(filepath.Dir(cfg.SocketPath), 0711)`。
判定理由：主机制成立：目录存在只是“默认 socket 路径恰好与硬编码目录同前缀”的副作用，一旦运维把 socket 改到别处（`CDS_CUBE_VOLUME_PLUGIN_SOCKET=/run/cds/cds.sock`，该 env 在 `deploy/cube-node/install.sh:34,102` 可配），`MkdirTemp` 直接 ENOENT → `rootfs.go:82` 报 "mountpoint: no such file or directory"，RT-07 读回整体不可用（exec 轮询会一直等到超时）。纠正：本条举的“selftest/ctl 入口不经过该路径”不成立——`ReadSandboxFile` 只有一个调用方，即 `server.go:128`，必在 `Serve` 之后；所以触发条件是“socket 路径被改”，不是 CLI 入口。low 合适。
修法：把目录做成可配置项并在调用前 `os.MkdirAll(dir, 0700)`，或直接用 `os.TempDir()` 下的专用子目录。

## cds-volume-plugin/cli.go

### [cli.go #1] jsonLine 丢弃 json.Marshal 错误 → 空输出仍退出 0 — **TRUE·LIVE**
证据：`cli.go:47-51` `jsonLine := func(v any) int { b, _ := json.Marshal(v); fmt.Println(string(b)); return 0 }`，6 个调用点全部 `return jsonLine(...)`（`cli.go:92/105/111/116/121/127`）；文件头契约：`cli.go:22-23` “ctl commands exit 0 on success … every fail-closed condition is a non-zero exit”。
判定理由：退出码与输出不一致确实存在，与本文件自述契约直接冲突，LIVE（所有 ctl 子命令）。实际触发需 Marshal 失败：`cli.go:127` 的 `jsonLine(m)` 序列化 Manifest（全为 string/bool/uint64/time.Time）不会失败，`cli.go:105` 的 map[string]any 值也都是基本类型 → 现实概率极低。但它是“失败被当作成功”的结构性陷阱（任何后续往 map 里塞 chan/func/NaN 都会静默空输出），medium 下调为 low-medium。
修法：`b, err := json.Marshal(v); if err != nil { return fail(4, err) }`。

### [cli.go #2] context.Background() 无超时/不可取消 — **TRUE·LIVE**
证据：`cli.go:46` `ctx := context.Background()` 传入所有 Plugin 调用（`cli.go:88/101/107/113/118/123`）；KMS 默认 30s：`kms.go:40-45`（`CDS_VOLUME_PLUGIN_KMS_TIMEOUT_MS` 可调大）；tongsuo 后端额外 fork helper：`cipher.go:45`。Ctrl-C 直接杀进程，无任何清理钩子（全模块 `signal.Notify` 零命中）。
判定理由：所述中间态真实存在：Destroy 先 RevokeDEK（`plugin.go:385`）再 shred（`plugin.go:389-395`），中途 Ctrl-C 会留下“KMS 已吊销但密文树未清理”；Create 同理（`plugin.go:67-86`）。但这是 CLI（运维/集成测试用，`cli.go:11` 注释），KMS 客户端自带 30s 超时，不是无界阻塞；medium 下调 low-medium。
修法：`ctx, stop := signal.NotifyContext(...)` + `context.WithTimeout`，退出前给在途操作一个收敛窗口。

### [cli.go #3] --private-data / --aad 经 argv 传递，泄露 session_dek_ref — **TRUE·LIVE**
证据：`cli.go:81` `pd := fs.String("private-data", "", "")` → `cli.go:107` `p.Attach(ctx, *vid, *baseDir, *sandboxID, *pd, ...)`；`cli.go:78` `--aad`。对照本仓库对同类资料的策略：KMS token 刻意走环境变量 `config.go:62` `envStr("CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN", "")`，helper 协议也刻意把 key 走 stdin 而非 argv/env（AGENTS.md “key on stdin, never argv/env (avoids /proc exposure)”）。
判定理由：策略不一致确实成立，且泄露内容不是普通参数：`session_dek_ref` 是从 KMS 取回卷 DEK 的凭据（`plugin.go:115-127` ParsePrivateData；`Attach` 在 `plugin.go:207` 只校它与 manifest 相等），拿到它 + 服务 token 就能 unwrap 明文 DEK。缓解：单独拿到 ref 无 token 仍不够；且 Linux 上 `/proc/<pid>/cmdline` 默认对其他本地用户可读。属真实凭据卫生面，medium 合理。
修法：支持 `--private-data-file` / stdin / 环境变量读取，argv 方式至少废弃并文档化。

### [cli.go #4] selftest 成功行手工拼 JSON，不对 c.Name() 转义 — **TRUE·LIVE**
证据：`cli.go:59` `fmt.Println(`{"selftest":"pass","cipher":"` + c.Name() + `"}`)`；cipher 名是接口方法（`cipher.go:18` `Name() string`），当前两个实现为固定字面量（`cipher.go:42`、`cipher.go:114`）。
判定理由：卫生问题属实；两个内置名称确实安含无特殊字符（`aes-128-gcm(fallback,non-GM)` 只有括号与逗号），而下游确实会解析这一行（`tests/test_cds_volume_plugin.py` 解析 ctl/selftest 的 JSON 输出做后端断言），因此新后端返回含引号的名称就会静默破约。low 定级正确。
修法：走 `json.Marshal(map[string]string{"selftest":"pass","cipher":c.Name()})`（顺带修 #1）。

### [cli.go #5] parseAnnotationCmd 丢弃 MarshalIndent 错误 → 静默成功退出 0 — **TRUE·LIVE**
证据：`cli.go:179-181` `b, _ := json.MarshalIndent(out, "", "  "); fmt.Println(string(b)); return nil`；`cli.go:65-68` 据此返回退出码 0。
判定理由：与 #1 同类且更直接（它是注解解析的校验入口，输出为空就等于校验未发生）。但同样：`ParsedAnnotation`（`cli.go:144-150`）字段全为 string/bool/`map[string]any`，而 `map[string]any` 来自 `json.Unmarshal`（`cli.go:170`）→ 只能是 JSON 可序列化值，MarshalIndent 实际不可失败。low 定级正确。
修法：检查并返回该错误（`return err`），与 #1 一起改。

### [cli.go #6] 所有子命令无条件跑 Validate+NewCipher+RunSelfTest — **TRUE·LIVE**
证据：`cli.go:28-44` 在任何分发之前无条件执行 `cfg.Validate()`、`NewCipher(cfg)`、`RunSelfTest(c)`（失败 `return 3`，文案 “self-test failed (fail-closed)”）；而 `parse-annotation` 是纯离线 JSON 解析（`cli.go:152-181`），`info` 只读 manifest（`cli.go:122-127` → `plugin.go:404-405`），两者不碰 cipher/kms。tongsuo 下自检会 fork helper：`selftest.go:29/33` → `cipher.go:44-65`。
判定理由：所述行为完全属实：helper 缺失/超时时，`ctl info` 与 `parse-annotation` 会以退出码 3 失败，而报错文案指向密码学校验 → 排障误判。LIVE（`main.go:27-30` 把 ctl/parse-annotation 均导入 runCLI）。但这也是“宁可过火不可默过”的有意 fail-closed 选择，仅属可用性/可诊断性问题，low 正确。
修法：把 `RunSelfTest` 下移到真正加解密的分支（create/put/attach），info/parse-annotation 跳过。

### [cli.go #7] os.ReadFile(*dataFile) 无上限，MaxVolumeBytes 校验发生在读完之后 — **TRUE·LIVE**
证据：`cli.go:97` `data, err := os.ReadFile(*dataFile)`；上限检查在 `plugin.go:137-139` `if int64(len(data)) > r.cfg.MaxVolumeBytes`（默认 1GiB，`config.go:64`），即先全量读入再判。
判定理由：顺序确实反了（无 `os.Stat` 前置），误指到镜像/大文件会先把进程打爆再报错；但这是本地 CLI、操作者本就是执行者（非信任边界），且上限 1GiB 本身已能触发 OOM（Go 先分配 1GiB+ 缓冲，` Seal` 还要再复制一份密文）。low 正确。
修法：先 `os.Stat` 对齐 `cfg.MaxVolumeBytes`，超限直接拒读。

## cds-volume-plugin/main.go

### [main.go #1] 非国密降级只挨一条 WARNING 就继续 Serve — **TRUE·LIVE**（影响面低于描述）
证据：`main.go:46-49` 仅 `log.Printf("WARNING: … AES-128-GCM (non-GM, dev/test) …")` 后继续 `main.go:50` 自检、`main.go:56` `return Serve(cfg, p)`；`config.go:72-76` 无条件接受 `aes_fallback`；`config.go:58` 默认值就是 `aes_fallback`；`cipher.go:153-154` 直接返回 AES 实例。整条启动链确无硬门禁。
判定理由：代码事实逐字成立，且这确实与“铁律 fail-closed”精种不一致（合规面而非密码强度面）。但仓库已有缓解证据，不能当作“生产静默降级已发生”：`deploy/e2e-remote/remote_deploy.sh:214`、`deploy/local/local_deploy.sh:229`、`helm/cds/values.yaml:276`、`k8s/secret.yaml.example:44` 均显式 `CDS_SM4_AEAD_BACKEND: tongsuo`；且降级可被监控：cipher 名逐卷写入 manifest（`plugin.go:79` `Cipher: r.cipher.Name()`）并随 RegisterDEK 上行控制面原样审计（`kms.go:94-103`，注释明写“the audit record states which cipher actually sealed the volume”）。所以真实风险是“漏配 env 的新部署（systemd/裸机）静默写 AES 卷”，medium 合理，high 不成立。
修法：默认值置空并在 `Validate()` 要求显式选择；或允许 fallback 但必须同时 `CDS_VOLUME_PLUGIN_ALLOW_DEV_CIPHER=1`，否则拒绝启动。

### [main.go #2] KMS 客户端无启动期传输安全校验 — **TRUE·LIVE**
证据：`main.go:55` `NewPlugin(cfg, c, NewKMSClient(cfg.KMSBaseURL, cfg.KMSToken))`；`kms.go:47-53` 构造函数只设 `Timeout`，无 scheme 校验、无 `CheckRedirect`、无 TLS/mTLS 注入；`config.go:61` 默认 `http://127.0.0.1:8000`；明文 DEK 确实入请求体：`kms.go:98-103`（`"dek_hex": hex.EncodeToString(dek)`）+ `kms.go:69-71` Bearer 头；`kms.go:30-31` 只有“prod transport is mTLS”的注释没有实现。
判定理由：全部属实：把 `CDS_VOLUME_PLUGIN_KMS_BASE_URL` 指到远端而忘 https，卷主密钥与服务凭据就明文出网，启动不会报错（与注释约定不符）。降级：默认值是 loopback（本机 unix 同机部署，`cube_runtime_adapter.py:682` 也是同机），且同一 URL 不可达时首次 Create/Attach 立即失败（无离线模式，`kms.go:72-75`），属配置护栏缺失而非即时泄露。另补充：默认 client 会跟随 307/308 并将请求体重放到对端选的地址（无 CheckRedirect）——该子项同样成立。medium 合理。
修法：启动时校验 scheme（生产仅 https，http 需显式 dev 开关）+ 注入带 CA/mTLS 的 Transport + `CheckRedirect` 拒跳；`KMSToken` 为空则 fail-fast。

### [main.go #3] serve 路径无取消/优雅关闭通道 — **TRUE·LIVE**
证据：`main.go:37-56` `serveCmd` 全程无 `signal.*`，`Serve` 无 ctx 参数（`server.go:139`）；`grep -rn "signal.Notify|GracefulStop" cds-volume-plugin/*.go` → 零命中；多步操作确实存于 `plugin.go:67-86`（Register→MkdirAll→saveManifest）、`plugin.go:380-399`（Revoke→shred）、`rootfs.go:86`（loop 挂载）。
判定理由：与 server.go#10 同一根因的 main 视角（不计为新缺陷），描述准确：SIGTERM 默认终止 → orphan DEK（KMS 已注册、manifest 未落盘）与 shred 未完成的 stage 文件都是可观察的中间态。medium 合理，修一次即可。
修法：`serveCmd` 建立 `signal.NotifyContext`，传给 `Serve`，收到信号后 `GracefulStop()`（超时降级 `Stop()`）。

### [main.go #4] Validate() 不校 SubprocTimeout，<1000ms 被截成 0 秒 — **TRUE·LIVE**
证据：`config.go:68-80` `Validate()` 只校 VolumeRoot / Backend / MaxVolumeBytes，无 SubprocTimeout 任何约束；`cipher.go:152` `NewTongsuoCipher(cfg.HelperPath, int(cfg.SubprocTimeout.Seconds()))`（秒级窄化）；`cipher.go:61` `case <-time.After(time.Duration(c.timeout) * time.Second)`。默认 10000ms → 10s 正常。
判定理由：完全成立且可达：把 `CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS=500` → `int(0.5)=0` → `time.After(0)` 立即就绪 → tongsuo 后端每次 Seal/Open 都报“timeout after 0s”，而自检在启动就报错（`main.go:50-52`）→ 插件拒绝启动（fail-closed 但硬故障）；负值同理（`time.After(负)` 亦立即触发）。另注意错误文案（`cipher.go:64`）完全看不出是精度截断所致，排查成本高。low-medium（需误配，但误配概率不低且后果确定）。
修法：Config 直接传 `time.Duration` 给 cipher（删掉秒级截断），并在 `Validate()` 要求 `SubprocTimeout > 0`。

## cds-volume-plugin/selftest.go

### [selftest.go #1] 自检只验往返，不断言“确实发生了加密” — **TRUE·LIVE**
证据：`selftest.go:29-39` 只有 `Seal` → `Open` → `bytes.Equal(opened, pt)`；无 `ct != pt`、无长度断言、无换 nonce 重 Seal 比较密文。负向路径存在但只覆盖篡改：`selftest.go:41-53`。
判定理由：“缺少该断言”是代码事实，且自检是启动硬门禁（`main.go:50-52` “refusing to serve”），故 LIVE。但实际可利用性低：AES 后端（`cipher.go:124-131` 走 `g.Seal`）不可能恒等返回；tongsuo helper 的输出帧是 `[tag||ct]`，`cipher.go:88-91` 取 `out[tagLen:tagLen+len(plaintext)]` 作 ct、`out[:tagLen]` 作 tag，即使 helper 把明文放开头也会把头 16 字节当成 tag 而令往返失败；现有测试已在本仓库他处钉住了 `ct != pt`（`tests/test_cds_volume_plugin.py:94` `assert len(ct) == len(pt) and ct != pt`）。属便宜的纵深加固，medium 下调 low。
修法：加两行廉价断言：`bytes.Equal(ct, pt)` 必须为假且 `len(ct)==len(pt)`；再用另一 nonce Seal 一次断言密文不同。

### [selftest.go #2] 负向用例缺 AAD 篡改；Open 失败返回值未断言为 nil — **TRUE·LIVE**
证据：`selftest.go:45` `c.Open(key, nonce, aad, bad, tag)` 与 `selftest.go:51` `c.Open(key, nonce, aad, ct, badtag)` ——两次都传原 `aad`，确无“换 AAD 必败”用例；返回值两处均为 `_`，未断言失败时不交回明文。AAD 在生产路径确实承担绑定：`plugin.go:249` 用 `m.AAD`。
判定理由：覆盖缺口属实（自检是 boot 硬门禁，LIVE）。但该缺口已被同仓库其他层覆盖：Go 单测显式断言 tampered aad 必须拒绝（`cds-volume-plugin/plugin_test.go:343-347` “tampered aad accepted”），helper 层也有篡改检测用例（`tests/test_cds_volume_plugin.py:100-108`，并断言 `dec.stdout == b""`）。两个后端（`cipher.go:141-143` 的 `g.Open`、C helper 的 `EVP_DecryptFinal_ex` 路径）均无“忽略 AAD”的现实基础。low。
修法：补一条 `c.Open(key, nonce, []byte("other"), ct, tag)` 必败；并断言失败时返回的明文长度为 0。

### [selftest.go #3] 对 ct/tag 直接取 `bad[0]`/`badtag[0]`，后端返回空则 panic — **TRUE·LATENT**
证据：`selftest.go:42-50` `bad := make([]byte, len(ct)); copy(bad, ct); bad[0] ^= 1` 与 `badtag[0] ^= 1`，前面确无长度校验（`selftest.go:29-39` 只校 err）。调用方：`main.go:27-28` `os.Exit(runCLI([]string{"selftest"}))`、`cli.go:41`、`main.go:50`。
判定理由：“无长度守卫”属实，但 panic 在当前两个后端不可达：AES `Seal` 总返回 `out[len(pt):]` 为 16 字节 tag（`cipher.go:130`）、tongsuo `Seal` 在 `cipher.go:88-91` 已 fail-closed 短输出；而 `RunSelfTest` 的明文固定 4096B（`selftest.go:24`），故 ct/tag 非空——属“未来后端/封装缺陷才会顶爆”的潜伏项，按 LATENT 计。panic 确实会跳过可读诊断（只表现为退出码 2）并抖掉启动自检。
修法：翻转前先 `if len(ct) == 0 || len(tag) == 0 { return fmt.Errorf("self-test: backend returned empty ct/tag (fail-closed)") }`。

## alembic/versions/0012_user_mfa.py

### [0012 #1] totp_secret_encrypted 用 String(512)，同表恢复码用 Text，容量策略不一致 — **TRUE·LATENT**
证据：`alembic/versions/0012_user_mfa.py:22` `sa.Column("totp_secret_encrypted", sa.String(length=512), nullable=True)` vs `:24` `sa.Column("recovery_codes_encrypted", sa.Text(), nullable=True)`；模型同步一致：`app/models/user_mfa.py:28` `String(512)` / `:32` `Text`。密文格式：`app/api/auth.py:70-73` `_encrypt_mfa` = base64(nonce12+tag16+ct)，`app/utils/crypto.py:69-76`（nonce 12B / tag 16B）。
判定理由：不一致属实，但无存活溢出路径：TOTP 密钥由 `_new_totp_secret()`（`auth.py:106`）生成为 20 字节 base32（32 字符）→ 密文 32+12+16=60B → base64 ≈ 80 字符，约为 512 的 16%。且 `String(512)` 是本仓库对“密文/URI 类短字符串”的现行风格（`alembic/versions/0009_trained_models.py:27`、`0000_baseline_all_tables.py:399` `encrypted_storage_path String(512)`）——`verify_schema_parity.py` 与 `alembic check` 会守住 alembic↔create_all 一致，改就得两侧同步。仅当改为信封加密（写 key_id/版本前缀）才会撞 `value too long`，属未来容量问题，不构成当前缺陷。
修法（可选，非缺陷修复）：若预期信封加密，与恢复码列统一改 `sa.Text()` 并同步 `app/models/user_mfa.py:28`（需新 revision，不得改 0012 历史）。

### [0012 #2] 缺 CHECK 约束；`confirmed=true` 可伴随 `totp_secret_encrypted IS NULL` — **TRUE·LATENT**（“认证绕过”子结论 FALSE）
证据：`0012_user_mfa.py:18-27` 确实只有 PK + FK，无任何 CHECK；全仓 `grep -rn "CheckConstraint|CHECK (" alembic/versions/*.py` → 零命中（数据库层 CHECK 不是本库风格）。应验证真实实现：`app/api/auth.py:190` `if mfa and mfa.confirmed:` → `:202` `ok = False` → `:203-207` 仅当 `mfa.totp_secret_encrypted` 非空才校 TOTP → `:208-209` 否则走恢复码，而 `_consume_recovery_code` 在 `recovery_codes_encrypted` 为空时直接 `return None`（`auth.py:92-93`）→ `:210-219` 返 401 `AUTH_MFA_REQUIRED`。
判定理由：schema 允许不一致行是真事实，但两点使其降为 LATENT：(1) 写 `confirmed=True` 的唯一入口是 `/auth/mfa/verify`（`auth.py:405-413`）——先验 TOTP 通过再同时写 confirmed + 密钥 + 恢复码，应用无法产出“confirmed 但无密钥”；`/mfa/enroll`（`:380-382`）显式置 `confirmed=False`。(2) 声称的“迫使实现回退到跳过 MFA、形成认证绕过”不存在：上述代码是 fail-closed（双空 → 恒定 401，最坏是自我锁定而非绕过），按铁律判定这恰恰是合规行为。作为兼底约束可加，但不是缺陷。
修法（可选）：若仍要 DB 层兼底，在新 revision 加 `CHECK (confirmed = false OR totp_secret_encrypted IS NOT NULL)`（NOT VALID 后 VALIDATE）；不要指望它改变安全等级。

### [0012 #3] 缺索引；CASCADE 删用户时会全表扫描 user_mfa — **FALSE**
证据：`0012_user_mfa.py:21` `sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False)` + `:26` `sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE")`；模型同构（`app/models/user_mfa.py:23-27`）。另外：`grep -rn "delete(User" app/api/*.py` 无命中（用户表仅有 `app/api/users.py:33` 的 select），应用内根本不存在删用户路径。
判定理由：不成立。`user_id` 本身就是主键，PostgreSQL 会为其建唯一 btree 索引，而 CASCADE 定位子行用的正是同一个 `user_id` 列 —— 该索引完全可被引用侧 FK 检查/CASCADE 使用，不存在“全表扫描 user_mfa”。“按 confirmed 统计启用率”的查询也不存在（`grep -rn "confirmed" app/api/*.py` 只有登录门 `auth.py:190` 按 user_id 单行取与 `/mfa/disable`，无按 confirmed 过滤的查询）。
修法：无需修改；加 `ix_user_mfa_user_id` 与主键索引完全重复，部分索引则无对应访问模式。

### [0012 #4] downgrade 直接 drop_table 会不可逆清除 MFA Enrollment，属静默安全降级 — **TRUE·LATENT**
证据：`0012_user_mfa.py:30-31` `def downgrade(): op.drop_table("user_mfa")`，docstring（`:1-6`）未声明数据丢失后果。对照同库邻居（均 drop_table，也都无备份提示）：`0011_chain_anchoring_records.py:35-39`、`0013_volume_keys.py:38-41`、`0014_cube_templates.py:55-61`。
判定理由：行为属实（重建后表空 → `auth.py:189-190` 查不到行→不强制 MFA），但它不是“静默”降级：本产把“未注册 MFA”当合法状态（`auth.py:374` 注释明确重注册会重置为未确认、`auth.py:445` 还有显式 disable 路径），且 drop_table 是本库所有 add-only 迁移的统一 downgrade 写法（见上三个邻居），并非 0012 独例。因此这是一个文档/发布流程建议（low/other 定级正确），而不是迁移缺陷；仓库规则也不允许回头修改已有 revision 历史。
修法：不动 0012；在发布手册里写明“回滚 0012 前需 `CREATE TABLE user_mfa_bak_0012 AS SELECT * FROM user_mfa`”（可选：后续 revision 里给该表加迁移前备份钩子）。

## Score

| 文件 | 条数 | TRUE·LIVE | TRUE·LATENT | FALSE | 备注 |
|---|---|---|---|---|---|
| cds-volume-plugin/plugin.go | 11 | 11 | 0 | 0 | #5 的 stageDir 绕过论证不成立（仅入口校验缺失部分成立）；#4 的 tag/manifest 回滚描述不准 |
| cds-volume-plugin/server.go | 10 | 10 | 0 | 0 | #1 的 TOCTOU 子项仅宽松 umask 下成立；#8 影响面被下调 |
| cds-volume-plugin/layout.go | 6 | 6 | 0 | 0 | #4 与 server#8 同一事实；#1 为本文件首要项 |
| cds-volume-plugin/rootfs.go | 5 | 5 | 0 | 0 | #1 = server#3（同一发现）；#5 的“selftest/ctl 入口”附理由错，主机制成立 |
| cds-volume-plugin/cli.go | 7 | 7 | 0 | 0 | 全为 CLI 卫生/可用性问题，#1/#5 触发概率极低 |
| cds-volume-plugin/main.go | 4 | 4 | 0 | 0 | #1 已有四处部署强制 tongsuo + cipher 名入审计，故不是 high |
| cds-volume-plugin/selftest.go | 3 | 2 | 1 | 0 | #3 panic 在现有两后端不可达 → LATENT；#1/#2 已有 plugin_test.go:343-347 与 py 侧覆盖 |
| alembic/versions/0012_user_mfa.py | 4 | 0 | 3 | 1 | #2 的“认证绕过”子结论被 `auth.py:202-219` fail-closed 否定；#3 FALSE（user_id 即主键索引） |
| 合计 | 50 | 45 | 4 | 1 | |

先修三项（按性价比排序）：

1. **rootfs.go:98-111 的未限定文件读（唯一 critical）**：`Lstat`+`os.ReadFile` 会跟随 guest 自己放的符号链接，以 root 读宿主文件并回传控制面（server#3 / rootfs#1）；FIFO/字符设备同时造成无限内存与挂载泄漏。改 `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)` + `IsRegular()` + fd 限长读。
2. **`grpc.NewServer()` 一行引发的三类问题（server.go:165，server#2/#9）**：未抬 `MaxRecvMsgSize`（默认 4MiB）与 `MaxVolumeBytes` 默认 1GiB 相矛盾 → 当前任何 >4MiB 的卷数据在传输层就被拒（控制面看到无关的 INTERNAL_ERROR）；同处补 recovery interceptor 与按 volume_id 的 keyed mutex，一次性消除 plugin#4/#8、layout#2、server#2 的共享根因（固定 `.tmp` 名同步换 `os.CreateTemp`）。
3. **明文擦除闭环（本插件的核心承诺）**：Detach 零校验（server#6）+ 失败后明文残留（layout#5）+ Destroy 完全不管 stage 目录（plugin#7）+ shred 无 Sync 且错误被吞（plugin#6/#10）。修法收敛为一个动作：Attach 时按 volumeID 记账 stage 路径，Detach/Destroy 统一按记账扫目录、收集错误、Sync 后 unlink，错误用独立码上报。

环环境声明：本轮未运行 `go build`/`go test`（本机 go1.18 vs go.mod 要求 go1.25.0；未 vendoring 依赖且模块需 Linux-only 系统调用）；所有结论均来自逐行阅读与一次 stdlib 探针（`filepath.Join/Rel` 包含性代数，用于判定 plugin#5 / layout#4 的逃逸机制），仓库工作区未作任何修改。

