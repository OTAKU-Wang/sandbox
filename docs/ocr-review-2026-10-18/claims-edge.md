# w3 边缘波次待判清单（80 条）

## cds-volume-plugin/plugin.go (11)
### [1] high/bug
自定义 AAD 参与 Seal 但从未写回 `Manifest.AAD`，而 `Attach`（第 246 行 `r.cipher.Open(dek, nonce, []byte(m.AAD), ...)`）与后续幂等校验都使用 manifest 中的 AAD（Create 时固定为 volume name）。后果：调用方一旦传入与 volume name 不同的 aad，数据写入成功，但该卷永远无法 Attach（SM4-GCM 认证标签校验必然失败），且后续重试也会持续报“无法解密已存在数据”。必须在 Seal 前把本次 aad 固化进 manifest（或干脆禁止覆盖 AAD）。另外 `aad == ""` 的“空即默认”语义使得调用方无法表达“用空 AAD 覆写”，也会与幂等校验产生不一致。

### [2] high/bug
复用已存在 stage 文件的分支存在两个问题：
1) 该分支早于 `if !m.HasData` 检查，因此当 `PutVolumeData` 写入的是空数据（`data` 长度为 0 是合法输入）时，第一次 Attach 写出 size==0 的文件，之后的 Attach 会落入 “stage lost” 分支反复重新解密——更糟的是 `Detach(refCount>0)` 只判断文件存在即通过，最终挂载给 VM 的是一个空文件，调用方无从察觉数据丢失。
2) 复用路径不做任何完整性校验：只要 `stageFile` 存在就直接返回 hostPath，若上一次 Attach 的写入被截断/文件被外部替换（`writeAtomic` 未 fsync，崩溃后可能留下空文件或残留 .tmp），插件会把一个未经 GCM 校验的明文目录交给 Cubelet 挂进 VM，这与本文件“fail-closed、绝不返回未验证明文”的契约相悖。建议复用前校验 `st.Size() == m.DataSizeBytes`，并在异常时先 shred 再重新 stage。

### [3] medium/bug
这里直接把 `privateDataJSON(ref)` 的返回值作为最终结果，但该函数在 ref 过长（>1024B）时会返回错误。此时 manifest 已成功落盘、DEK 已在 KMS 注册，却不会走 `abortAfterRegister`，于是卷处于“已创建但 Create 返回失败”的不一致状态：控制面认为创建失败可能发起销毁/重试，而本地已残留一个可用的 DEK 引用。建议先构造 private_data 再 saveManifest，或在此处失败时调用 abortAfterRegister 做回滚。

### [4] medium/bug
两处并发/原子性问题：
1) `loadManifest` → `HasData` 判断 → 写文件 → `saveManifest` 不是原子操作，gRPC 层每个请求一个 goroutine 且插件内没有任何每卷互斥。两个并发首次 Put 都会看到 `HasData == false`，各自生成不同 nonce 依次覆盖 ct/nonce/tag，最终 ct 与 nonce/tag 可能来自不同次 Seal（Attach 必然解密失败），且 manifest 中记录的 DataSizeBytes 可能与磁盘数据不符；`readStagedPlain` 的幂等校验同样可能在 ct/nonce/tag 被另一路半程覆写时读到撕裂状态。
2) `writeAtomic(p.ct)` / `writeAtomic(p.nonce)` 已经成功，但 tag 写入失败时只回滚了 ct 与 nonce，没有回滚 tag（也没有回滚 manifest），可能遗留 tag 孤儿文件。
建议在 Plugin 中增加按 volumeID 的互斥（如 `map[string]*sync.Mutex` + 保护锁），并在失败分支统一清理/回滚所有已写对象。

### [5] medium/security
volumeID 只在 `CreateVolume` 里做过 `validateVolumeID`，Attach/Detach/PutVolumeData/Destroy/GetVolumeInfo 都直接使用调用方（Cubelet gRPC 请求）传入的 volumeID，且 volume_base_dir 也是请求字段。当前 `stageDir` 的越界自检依赖 `rel` 的前缀判断，而 `filepath.Join(base, "cds-"+volumeID)` 会把 `<base>/cds-..` 这类以 `cds-` 开头、内含 `..` 路径段的组件判为“仍在 base 内”，因此该校验并不能保证结果路径真的落在 base 之内。更直接的修法是：在 Attach/Detach 入口统一调用 `validateVolumeID`（拒绝 `/`、`\`、`.`、`..`），并在 stageDir 内用“逐段前缀比较”而不是字符串前缀做包含性检查。

### [6] medium/bug
shred 的可信度问题：写入零块后没有 `f.Sync()` 就把文件 unlink。在没有 fsync 的情况下，页缓存中的“已覆盖”数据可能尚未落到块设备，掉电/快照恢复后原始明文仍可恢复；同时若中途 `WriteAt` 失败（firstErr 非 nil），代码仍然继续执行 `os.Remove(path)`，只是把错误返回——此时文件已被删除但覆盖并未完成，调用方拿到的“plaintext retained”错误描述与真实情况不符（文件其实已不在）。建议在 unlink 前强制 Sync，并在覆盖失败时明确区分“未擦除但已删除”的状态。

### [7] medium/bug
Destroy 只清理卷目录（密文 + manifest），但仍在使用的 staging 明文目录 `<base_dir>/cds-<volume_id>` 完全不在考虑范围内：即使有 sandbox 正以 refCount>0 挂载该卷，Destroy 也会成功，而明文副本会永久留在宿主机上（Attach 写的 0400 文件），同时 manifest 已被删，后续 Detach 因 `stageDir` 不依赖 manifest 仍会“成功”，但再也无法 Attach/审计。建议在 Destroy 前校验引用计数/本地 staging 记账，并在销毁时一并 shred 已知的 stage 路径。

### [8] medium/bug
两个相关风险都来自 `writeAtomic`（固定使用 `path+".tmp"` 作为临时名，失败时不清理）：
1) 并发同名冲突：两个不同 sandbox 同时 Attach 同一 volumeID（refCount==0）时，都会向 `stageFile+".tmp"` 做 O_TRUNC 写入 + rename，两个 rename 都可能重命名到一个正处于半写入状态的临时文件，导致交给 VM 的明文被混写/截断（无 GCM 校验保护）。
2) 残留 .tmp 造成永久失败：stage 文件权限为 0400，若一次写入/重命名失败留下了 `data.tmp`，非 root 进程下次 `os.WriteFile` 打开已存在的 0400 文件时会直接拿到 EACCES，Attach 从此无法成功且无自愈路径。
建议：临时文件用 `os.CreateTemp(dir, ".stage-*.tmp")` 生成唯一名字 + 失败时删除，并在 rename 前 Sync；同时在 Attach 入口对同一 volumeID 加互斥。

### [9] low/bug
Detach 完全信任调用方传入的 refCount 与 baseDir（server.go 里 baseDir 直接取自 `req.GetMetadata()["base_dir"]`）。这带来两个后果：1) 客户端传 refCount>0 即可让明文长期驻留在宿主机 staging 目录，绕开“会话结束即擦除明文”的安全约束，插件侧没有任何兜底（如 TTL/最大驻留时间或按 sandboxID 记账）；2) baseDir 由客户端决定且 `stageDir` 不校验它与 Attach 时是否一致，传错路径时函数静默成功，明文实际并未被擦除。建议至少把 staging 位置记录在本地（按 volumeID 记账）并在 Detach 时交叉校验。

### [10] low/bug
这里丢弃了 `shredFile` 的错误（函数注释又强调 shred 失败要 fail-closed），导致“密文/manifest 明文擦除失败”时 Destroy 仍可能返回成功，控制面据此认为数据已彻底销毁。此外 `os.Remove(p.dir)` 在目录非空（例如残留 `*.tmp` 临时文件）时会失败，但前面的 shred 失败已被吞掉，排障时无法定位真实原因。建议收集 shred 错误并在最终返回值中体现。

### [11] low/security
幂等校验路径解密出的 `existing` 是完整明文，但在这里使用完（bytes.Equal）后从未像其他路径那样 `zeroize`；同样 `existing`/`data` 在函数返回后仍可达，明文会一直留在堆上直到 GC 复用。本文件对 dek/plain 都严格零化，这里是明显遗漏。建议 `defer zeroize(existing)`（readStagedPlain 返回后立即注册）。

## cds-volume-plugin/cipher.go (10)
### [1] critical/security
帧构造完全不做长度校验，错误长度会静默改变字段偏移或截断密钥：
1. `copy(in, key)`：若 `len(key) < dekLen`，剩余 key 槽位保持全零 → 实际使用了“零填充密钥”而非报错；若 `len(key) > dekLen`，密钥被静默截断。两种情况都会让调用方以为在用某个 DEK，而 helper 用的是另一个密钥，导致数据不可解密或跨后端不一致。
2. `nonce` 长度未校验：helper 严格按 key(16)||nonce(12)||aad_len(4) 读取（sm4_gcm.c:140-144）。nonce 短于/长于 12 字节时，后面的 `aad_len` 与 aad 会被当成 nonce 读入，AAD 认证域被静默改写；只有恰好越界才会 exit 2，否则可能返回“成功”的密文（AAD 绑定失效）。
3. `Open` 中 `mid`（tag）长度不等于 tagLen 时同理：tag 尾部/头部会被当作 ciphertext，密文与 tag 边界错乱。
4. `binary.BigEndian.PutUint32` 对 `len(aad)` 做无检查窄化（>4GiB 时回绕）。
建议在 Seal/Open 入口显式 fail-closed 校验并返回错误。

### [2] critical/security
Seal/Open 是暴露给上层（KMS 下发的 DEK、卷数据）的边界，但没有对 key/nonce/tag 做任何长度校验，直接把参数拼进固定偏移的二进制帧。建议在调用 `run` 之前 fail-closed：`len(key) != dekLen || len(nonce) != nonceLen` 直接返回错误；`Open` 还需 `len(tag) != tagLen` 报错。否则错误长度会静默产生“偏移错乱但退出码为 0”的密文（详见 helperFrame 上的评论），破坏 AAD 绑定与可解密性；而 AESGCMCipher 走 `NewGCMWithNonceSize` 允许任意 nonce 长度，两个后端行为不一致，会让同一份数据在切换后端后无法解密。

### [3] high/bug
`Open` 未校验 `tag` 长度，且这里的 fail-closed 保证只依赖 helper 的退出码：helper 按 RD_FULL(tag) 读满 16 字节后才读 body，因此 `len(tag) != tagLen` 时 tag/body 边界会错位（tag 短 → 密文前若干字节被当成 tag），虽然通常最终会因认证失败退出 3，但一旦部署的 helper 版本行为不同（返回 0 并输出解密结果），这里会把未认证数据当 plaintext 直接返回。建议在入口显式校验 `len(tag) == tagLen`，并考虑对成功路径做最小一致性断言。

### [4] high/bug
超时时序有两个问题：
1. `Kill()` 的错误被丢弃后无条件 `<-done`。若 Kill 失败（权限、进程已 stop/僵尸化等非“已退出”场景），`cmd.Wait()` 可能永不返回，调用方（gRPC 请求路径）将无限阻塞，恰好违背这里声称的 fail-closed 超时保证；且日志里看不到 Kill 失败的原因。建议记录 kill 错误，并对 `<-done` 再加一层兜底超时（超时后返回错误并让 goroutine 自行退出，done 有缓冲不会泄漏）。
2. `time.After` 每次调用都新建 timer，且这里的 timeout 精度只有秒（见 NewCipher 处的评论）。

### [5] high/bug
`int(cfg.SubprocTimeout.Seconds())` 把 Duration 窄化为整数秒，会静默丢失精度：`CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS` 只要小于 1000（如 200ms、500ms）或被配成 0/负值，`c.timeout` 就变成 0 或负数，`time.After(0)` 立即触发 —— 于是**每一次** helper 调用都以 “timeout after 0s” 失败，插件完全不可用，且错误信息指向超时而非真实原因（配置精度被截断）。同时 `c.timeout == 0` 与“未配置”无法区分。建议 Config 侧直接保存 `time.Duration` 并校验 > 0。

### [6] medium/security
helper 以 `exec.Command(c.helperPath, op)` 启动，而 `HelperPath` 默认值是相对名 `"sm4_gcm"`（config.go:59），会在 `PATH` 中查找可执行文件；同时未设置 `cmd.Env`，子进程继承插件的完整环境，包含 `CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN`。组合效果：一个可写 PATH 目录的攻击者放入伪造的 `sm4_gcm`，即可（a）从 stdin 拿到明文 DEK（协议刻意把 key 放 stdin 以避免 /proc 暴露，但进程环境里的 KMS token 照样泄露给这个低可信二进制），（b）返回任意“合法退出码 + 任意 tag/密文”，绕过本文件声称的 fail-closed 认证语义。建议要求并校验 HelperPath 为绝对路径（必要时校验属主/权限），并显式设置 `cmd.Env = []string{}` 或最小化环境。

### [7] medium/bug
这里丢弃了 `g.Open` 返回的原始错误（`cipher.ErrOpen`），改用现场构造的 `errors.New("auth failed")` 包装，导致：调用方无法用 `errors.Is(err, cipher.ErrOpen)` 判断，`err` 变量在此仅用于判空后被丢弃，真实失败原因（含未来可能的非认证类错误，如 nonce 长度非法）被统一伪装成认证失败，误导排障。直接透传原错误即可。

### [8] low/bug
错误链在此被 `%v` 截断：`cmd.Wait()` 返回的 `*exec.ExitError`（携带 helper 的退出码 1/2/3，语义在 sm4_gcm.h 中明确区分 usage/io/cipher/auth-fail）无法被 `errors.As` 取出，上层无法区分“认证失败”与“IO/参数错误”，也无法对 auth 失败做专门审计。同类问题也出现在上面的 timeout 分支。建议用 `%w`。

### [9] low/security
包含 DEK 明文的帧缓冲 `in`（以及 `out` 中的明文）在函数返回后没有任何清零，会长期留在 Go 堆上；本项目已有 `zeroize` 并在 selftest.go 中对 key 使用，说明存在密钥驻留内存的卫生要求。这里恰恰是密钥以明文连续排布、最易被 core dump/内存扫描提取的位置。建议在 `run` 返回（含错误路径）前对 stdin 缓冲做清零。

### [10] low/maintainability
提醒：`errors` 包在本文件仅用于 Open 中那个现场构造的 `errors.New("auth failed")`。若按建议在 Open 里改为直接 `%w` 透传 `g.Open` 的原始错误，需同时删除这里的 `"errors"` 导入，否则编译不通过。

## cds-volume-plugin/server.go (10)
### [1] high/bug
Unix socket 权限与注释声明的访问模型自相矛盾：这里强制 chmod 0600，随后又把属主设为 root:cds 并注释“Cubelet's runtime user must be in that group to dial the socket”。0600 下组权限位为空，非 root 的 cds 组成员连接必然 EACCES —— 要么 Cubelet 只能以 root 运行（那么 lookup group + chown 到 cds 组毫无意义且误导运维），要么真实需求是 0660，本处会让生产环境 Attach/Detach 全部失败。建议按实际信任模型改为 0660（并保留 root:cds），或删掉组相关逻辑并显式记录“仅 root 可连接”。

另有一个 TOCTOU 窗口：AF_UNIX socket 文件由 net.Listen 以 0777 & ~umask 创建（常见 umask 022 下为 srwxr-xr-x），而 socketDir 是 0711（其他用户可穿越），因此从 Listen 成功到 Chmod 生效之间，本机任意非特权用户都能 connect 并调用 Attach/Destroy/ReadSandboxFile（以 root 身份操作卷、读取宿主文件）。建议在 Listen 前临时收紧 umask（或在该窗口内先 bind 到仅 root 可见的目录），使 socket 从创建起就不对外开放。

### [2] high/bug
同一 volume_id 的并发 Attach/Detach/PutVolumeData 在本层完全没有限序，直接落到无锁的共享 Plugin 上，存在明文撕裂与明文残留风险：
- plugin.Attach 是 Stat(stageFile) → 解密 → writeAtomic 的 check-then-act；layout.writeAtomic 使用固定 `path+".tmp"` 名（os.WriteFile 只有 O_TRUNC，没有 O_EXCL/唯一后缀），因此两个并发 Attach 会写同一个 `data.tmp`，交错内容后再各自 rename，VM 可能读到被污染的明文（且 SM4-GCM 校验发生在写出之前，无法在 stage 阶段发现）。
- Attach 与 Detach 并发时，Detach 的 shredFile 可能先把文件清零/unlink，随后 Attach 再写出明文；此时引用计数已归零，不会再有 Detach 来擦除它 → 明文永久残留。
- refCount 完全由调用方提供，服务端没有权威计数，乱序/重复请求（重试导致同一 refCount 重放）本身就会让 stage 生命周期判断出错。
建议在本层或 Plugin 内引入 per-volume 互斥（如 keyed mutex / `singleflight`），并让 writeAtomic 使用 os.CreateTemp 生成唯一临时名 + rename；Detach 与 Attach 必须串行在同一把卷锁下。

### [3] critical/security
该 RPC 把 guest 可控内容当作可信输入，存在以 root 身份读取宿主任意文件的路径。rootfs.go 只用 `path.Clean("/"+vmPath)` 消除了 `..`，但 vm_path 最终拼成 `<mountPoint>/disk/<cubebox>/upper/<rel>`，随后用 `os.Lstat` + `os.ReadFile` 读取。rootfs upper 层由 guest 写入，攻击者可在自己 rootfs 里放一个符号链接（如 `/results/out -> /etc/shadow` 或指向 `../../../../../../etc/shadow`）：Lstat 不跟随链接所以 `fi.IsDir()`/size 检查都能通过（符号链接自身很小），但随后的 `os.ReadFile(src)` 会跟随链接 —— 绝对目标在宿主根命名空间解析，相对目标可越出 mountPoint，最终以 root 读取宿主任意文件并通过 `Data` 字段回传控制面。建议：用 `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)` 或逐级 openat + O_NOFOLLOW/dirfd 方式在 mount 内解析，并在读取前用 `filepath.EvalSymlinks`/`syscall.Fstat` 确认目标仍位于挂载点之内；只允许常规文件（`fi.Mode().IsRegular()`）。

### [4] medium/security
无条件删除并重建 socket 路径，缺少单实例互斥：如果已有一个插件实例在运行（例如 systemd 双起、或运维手动再起一个 serve），这里会直接 unlink 活跃 socket 并接管路径。结果是两个进程同时持有同一份 VolumeRoot：并发 Attach/Detach/Destroy 会同时 shred 同一份明文与密文树，且第一个实例的连接全部静默断开（Cubelet 侧表现为 attach/detach 失败，明文可能残留）。建议先尝试 dial 该 socket 判断是否已有活实例（存在则直接报错退出），或对本进程的 socket 路径加 flock/pid 文件；仅对确认 stale 的 socket 做 Remove。

### [5] medium/bug
错误分类过粗且丢失错误语义：
1) 所有非 NotFound 都变成 FailedPrecondition。但下游大量错误其实是参数错误（plugin.go 的 `validateVolumeID`、layout.go 的 `volume_base_dir must be absolute` / `host_path escapes volume_base_dir`、`private_data not valid JSON`），语义上应是 InvalidArgument。FailedPrecondition 在 Cubelet/CSI 侧通常被当作可重试错误，非法输入会被无限重试，而不是快速失败上抛。
2) KMS/后端故障（kms.UnwrapDEK、helper 调用失败、`DEK revoke failed`）也被映射成 FailedPrecondition，丢失 Unavailable/DeadlineExceeded/Internal 语义，且 `err` 身份没有被保留（没有用 status.FromError/errors.Is），调用方无法区分“重试可能成功”与“必须人工介入”。
3) 直接把 err.Error() 回传给 RPC 调用方会把内部绝对路径、KMS dek_ref、镜像路径等细节带出边界（例如 rootfs.go 的 `rootfs image %s is a directory`、plugin.go 的 `volume dir removal: %v`）。建议显式分类（InvalidArgument / FailedPrecondition / Unavailable / Internal），对外返回稳定的简短消息，详细原因只写日志。

### [6] medium/bug
Detach 缺少必填字段校验，且失败路径会导致明文残留：Attach 校验了 volume_id/volume_base_dir，但 Detach 直接取 `req.GetMetadata()["base_dir"]`，不校验 volume_id 是否为空、metadata 是否为 nil、base_dir 是否存在。此时 stageDir 只会返回一个通用错误并被 mapErr 归为 FailedPrecondition，plugin.Detach 根本没执行到 shredFile —— 已经落盘的解密明文（stage 文件）被永久保留在宿主上，而这正是该插件的核心安全承诺（session 结束即擦除）。Cubelet 重启/metadata 丢失时这条路径很可能被触发。建议：显式校验 volume_id 与 base_dir 并返回 InvalidArgument；base_dir 缺失时回退到配置/上次 Attach 记录的 stage 目录尽力擦除，并把“明文未能擦除”作为需要告警的独立错误码（如 codes.DataLoss/Unavailable）而非普通 FailedPrecondition。

### [7] medium/bug
该 handler 在请求路径上执行不受控的阻塞型宿主操作，却没有取消/并发上界：
1) `s.p.ReadSandboxFile` 内部是 `exec.Command("mount", ...)`（rootfs.go，无 context、无 SubprocTimeout）+ 全文件 ReadFile。rootfs 镜像正被 VMM 持有，底层 I/O 阻塞时 mount 可无限期挂住；handler 也不检查 ctx，客户端超时/断开后 goroutine 与 loop 设备/挂载点会一直泄漏，最终耗尽 loop 设备或把插件打成不可用（Attach/Detach 同样受影响）。
2) 单次响应最大 64MB（maxSandboxFileBytes），gRPC server 端没有并发流/内存限制，多个控制面调用方并发轮询即可造成 GB 级内存放大。
3) 错误同样直接 `err.Error()` 外泄宿主绝对路径与镜像路径。
建议：改为 `exec.CommandContext(ctx, ...)` 并套 cfg.SubprocTimeout，给该服务加信号量限流（同一 cubebox 串行化复用挂载），响应侧分块流式返回或降低上界，对外返回 codes.Internal + 通用消息。

### [8] medium/security
RPC 边界没有对 volume_id 做白名单校验，路径拼接依赖下游“恰好”做了校验。`validateVolumeID` 只在 CreateVolume 里调用，因此 Destroy/GetVolumeInfo/PutVolumeData/Attach 可以把含 `/`、`..` 的 volume_id 直接交给 `Plugin.paths()` 的 `filepath.Join(cfg.VolumeRoot, volumeID)`，把访问范围扩展到 VolumeRoot 之外：`GetVolumeInfo("../../x")` 可探测/读取 VolumeRoot 之外任意位置、volume_id 字段恰好匹配的 manifest.json；`Destroy` 在 manifest 匹配成功时会 shred 该目录并 `os.Remove(dir)`。虽需要目标目录已存在合法 manifest，属于受限的信息泄露/删除面，但边界校验不应由内部不变量兜底。建议在本文件各 handler 入口统一调用 `validateVolumeID(req.GetVolumeId())` 并返回 InvalidArgument（Detach 同理），使“volume_id 是无分隔符的短标识符”成为服务端强制约束。

### [9] medium/bug
grpc-go 默认不会 recover handler 里的 panic，未注册 recovery 拦截器意味着任一 handler（含未来新增逻辑、cgo/helper 边界）panic 都会让整个插件进程崩溃：正在进行的 Detach 会停在 shredFile 中途，明文文件残留在宿主上且不满足 fail-closed 承诺；同时所有正在进行的 Attach 失败。另外这里也没有设置 `grpc.MaxRecvMsgSize`/`MaxSendMsgSize`/MaxConcurrentStreams：默认 4MB 接收上限远小于 cfg.MaxVolumeBytes（默认 1GB），PutVolumeData 传大卷会被传输层直接拒绝并表现为与业务无关的错误，而发送方向可返回 64MB 响应，缺少内存上界。建议加 `grpc.UnaryInterceptor(recoveryInterceptor)`，并把 MaxRecvMsgSize 显式配置为与 MaxVolumeBytes 一致（或引入分块上传），同时限制并发流数。

### [10] medium/bug
缺少优雅停机路径：Serve 返回只发生在 Stop/监听器错误，而进程收到 SIGTERM/SIGINT 时（默认 disposition）会立刻终止，没有任何清理。后果不是单纯的“socket 文件残留”（下次启动靠 Remove 兜底），而是正在执行的 Detach/shredFile、CreateVolume（saveManifest/abortAfterRegister 的 DEK revoke）、ReadSandboxFile 的 loop 挂载会被打断：明文可能只写未擦、KMS 中残留已注册但无 manifest 的 DEK、/run 下残留挂载点。建议在 Serve 起 goroutine 监听 signal.NotifyContext，收到信号后 `srv.GracefulStop()`（配合超时降级 `Stop()`），等待在途卷操作完成后再返回。

## cds-volume-plugin/sm4gcm/sm4_gcm.c (8)
### [1] critical/security
AAD 分配失败被静默忽略，导致认证上下文降级。当 `aad_len > 0` 而 `malloc` 返回 NULL 时，条件 `aad && fread(...)` 短路，错误路径不会触发；随后 `sm4_gcm_seal/open` 收到 `aad == NULL, aad_len > 0`，`if (ok && aad && aad_len)` 分支被整体跳过，即 **AAD 完全没有参与 GHASH 计算**。

后果：
- 加密侧会生成一个“未绑定 AAD”的 tag，调用方以为 AAD 已被认证（例如卷号/密钥代次等上下文信息），实际完整性保护被静默绕过；
- 解密侧在 OOM 时会用不带 AAD 的 ctx 去校验带 AAD 的 tag，虽然结果是认证失败（可用性故障），但同一函数内 NULL 入参被当成“无 AAD”处理，属于危险的语义歧义。

必须把分配失败当作致命错误（fail-closed），并且不要用 `aad` 指针本身作为“是否存在 AAD”的判据。

### [2] high/bug
`size_t -> int` 截断未被校验，且自校验逻辑无法发现截断。`in_len` 来自主流程中无上限的 `read_growing(body)`，当 `in_len > INT_MAX`（≥2GiB）时，`(int)in_len` 取低 32 位截断值；OpenSSL 只处理截断后的字节数并返回同样的截断值，于是 `l == (int)in_len` **意外成立**，函数返回成功。

后果：`out` 中只有一小部分被真正加密，`out + 截断长度` 之后的未初始化堆内存被当作“密文”写入 stdout —— 既造成静默数据损坏，也会泄露堆上残留内容（可能包含上一轮的密钥/明文）。注意此处 `al == (int)aad_len` 因 `SM4GCM_MAX_AAD`（16KiB）尚安全，但 `in_len` 无上限保护。

建议：进入加解密前显式拒绝超长输入，并用 `size_t` 比较长度。需要 `#include <limits.h>`。

### [3] high/bug
解密路径存在同样的 `size_t -> int` 截断：`in_len` 无上限，超过 `INT_MAX` 时 `(int)in_len` 截断，且 `l == (int)in_len` 会因两侧同为截断值而误判成功，最终只解密/校验了一部分数据就返回 1，`main` 会把未认证的原始输入字节当作“已认证明文”输出。需要与 `sm4_gcm_seal` 一致地加上 `in_len > INT_MAX` 前置校验，并以 `size_t` 比较长度（`#include <limits.h>`）。

### [4] medium/security
函数头注释声称 “a tampered tag/ciphertext/aad returns 0 and **out is untouched**”，但实现与之矛盾：`EVP_CipherUpdate` 在 tag 校验之前就已把解密出的明文字节写入 `out`（GCM 是流模式，边解密边输出），标签校验发生在 `EVP_DecryptFinal_ex`。因此返回 0 时 `out` 中**已存在未认证明文**。

当前 `main` 恰好没有在该分支写 stdout，才勉强满足 fail-closed，但这个契约完全依赖调用方自觉，一旦该函数被其他调用方复用（头文件已作为公共 API 导出）就会泄露未认证明文，同时缓冲区残留内容也会留在堆上。建议在失败路径主动清零，并修正注释表述。

### [5] medium/security
`read_growing` 对 stdin 消息体没有任何上限：`cap` 从 1MiB 起按 2 倍无限 `realloc`，攻击者只需持续喂数据即可让进程 OOM（DoS），且 `main` 随后还会再 `malloc(body_len)` 一份 `out`，实际内存占用约为输入的 2 倍。另外两个隐患：
1. `realloc` 失败时直接 `return 0`，原 `*buf` 泄漏且指针悬垂（调用方 `main` 目前靠 `die()` 退出掩盖了问题，但函数契约不安全）；
2. `cap *= 2` 无溢出保护，理论上溢出为 0 后 `realloc(ptr, 0)` 可能返回非空小对象，紧随其后的 `memcpy` 将越界写。

建议引入 `SM4GCM_MAX_BODY` 上限，并在失败路径 `free(*buf); *buf = NULL;`。

### [6] medium/security
敏感数据清理不完整：
1. 只清理了 `key`，解密得到的明文 `out`、输入密文/明文 `body`、`aad`、`tag` 在 `free()` 前均未清零，可能残留在堆页中被 core dump / 换页 / 后续 malloc 复用读取；
2. 更关键的是本行位于唯一的成功路径上——所有 `die()` 都调用 `exit()`，而此时 `key`（以及部分 `body`）已从 stdin 读入栈/堆中，却从未经过 `explicit_bzero`。建议把密钥擦除下沉到 `die()`（或注册 `atexit`）中，保证任何退出路径都清理；
3. 释放后建议将指针置 NULL，避免后续维护引入悬垂使用。

### [7] low/bug
解密路径先用 `EVP_EncryptInit_ex()` 设定 cipher，再用 `EVP_DecryptInit_ex(ctx, NULL, NULL, ...)` 将方向切回解密。这依赖“cipher 为 NULL 时沿用已有 cipher 但重置方向”的实现细节，在 OpenSSL/Tongsuo 版本升级或 provider 实现变化时较为脆弱。建议解密函数中统一使用 `EVP_DecryptInit_ex(ctx, EVP_sm4_gcm(), NULL, NULL, NULL)` 作为首次初始化，语义更明确且与官方 GCM 解密范例一致。

### [8] low/maintainability
`RD_FULL` 依赖 `sizeof(buf)` 推导长度，一旦日后传入指针参数（而非数组）将静默退化为 `sizeof(pointer)`（如 8 字节），造成协议错位而无编译错误；同时宏在函数尾部未 `#undef`，作为了函数内部宏泄漏到后续代码。建议改用 `static int read_full(unsigned char *p, size_t n)` 函数，并在 `main` 末尾 `#undef RD_FULL`。

## cds-volume-plugin/cli.go (7)
### [1] medium/bug
`jsonLine` 忽略了 `json.Marshal` 的错误：序列化失败时 `b` 为 nil，会打印空行并返回退出码 0，与文件头声明的契约“ctl 命令成功才退出 0，任何 fail-closed 情况都必须非零退出”直接冲突。调用方（集成测试/运维脚本）会以退出码判定成功，却拿到空输出，从而把失败当成功。建议检查并上报错误。

### [2] medium/bug
`ctx := context.Background()` 无超时、无取消、也不响应信号，却被传入所有可能阻塞的 Plugin 方法：`CreateVolume`/`PutVolumeData`/`Attach`/`Destroy` 都会走 `KMSClient`（默认 `Timeout` 30s，且可被 `CDS_VOLUME_PLUGIN_KMS_TIMEOUT_MS` 调大），tongsuo backend 还会 fork helper 子进程。对 `Destroy`（先 RevokeDEK 再 shred）这类多步操作，Ctrl-C 直接杀进程可能停留在“KMS 已吊销但密文树未清理”的中间态。建议用 `signal.NotifyContext` + `context.WithTimeout` 给 CLI 一个显式 deadline 与可取消边界。

### [3] medium/security
`--private-data` 携带 `session_dek_ref`（等价于卷 DEK 的 KMS 取回凭据），`--aad` 也是加密上下文。经 argv 传递会暴露在 `/proc/<pid>/cmdline`（同机任意本地用户可读）、shell history 与进程审计/日志中，构成敏感信息泄漏面；本仓库对 KMS token 已刻意改用环境变量（`CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN`），此处策略不一致。建议支持 `--private-data-file` / stdin / 环境变量读取。

### [4] low/bug
`selftest` 成功输出用字符串拼接手工构造 JSON，未对 `c.Name()` 做转义。当前两个内置名称恰好安全，但 cipher 名称是接口方法返回值（新 backend/测试替身可返回任意字符串），一旦含 `"`、`\\` 或控制字符就会输出非法 JSON，而消费方（集成测试解析该行的 `cipher` 字段做 M-01 后端断言）会解析失败。建议统一走 `json.Marshal`。

### [5] low/bug
`parseAnnotationCmd` 同样丢弃了 `json.MarshalIndent` 的错误：序列化失败时打印空行并返回 nil，`runCLI` 随即返回退出码 0，即“成功但无输出”。作为注解解析的校验入口，静默成功会掩盖真实失败。

### [6] low/maintainability
所有子命令都无条件执行 `cfg.Validate()` + `NewCipher` + `RunSelfTest`。其中 `parse-annotation` 是纯离线 JSON 解析、`info` 只读 manifest，并不需要密码后端可用；在 tongsuo backend 下 `RunSelfTest` 会 fork `sm4_gcm` helper，helper 缺失/超时时这两个命令会以退出码 3（“self-test failed (fail-closed)”）失败，排障时极易误判为密码学故障。建议仅对真正涉及加解密的命令执行自检。

### [7] low/performance
`os.ReadFile(*dataFile)` 会把整个文件无上限读入内存，而体积上限 `cfg.MaxVolumeBytes` 的校验发生在 `PutVolumeData` 内部（读完之后）。传入一个超大文件（如误指到镜像/稀疏文件）会在守卫生效前就把进程打爆，且随后还要再复制一份密文。建议先 `os.Stat` 对齐 `cfg.MaxVolumeBytes` 再读取。

## cds-volume-plugin/config.go (6)
### [1] high/bug
子进程超时存在两处数值缺陷：
1) 溢出：`envInt64` 返回的毫秒值直接乘以 `time.Millisecond`（1e6），当环境变量被设成 ≥ ~9.22e12 的毫秒数（例如误配 `CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS=99999999999999`）时会溢出为负 Duration，`Validate()` 又不校验 `SubprocTimeout > 0`，该值会一路传到 `NewCipher` → `NewTongsuoCipher(helperPath, int(Seconds()))`，`time.After(负值)` 立即触发 → helper 被 `Kill()`，spec §2.9 的子进程超时保护实际失效/行为未定义。
2) 单位截断：消费方 `cipher.go:152` 用 `int(cfg.SubprocTimeout.Seconds())` 只保留整秒，任何 <1000ms 的配置都被截成 0 秒，等价于“启动即超时”，helper 永远跑不完，整个加解密通道不可用（fail-closed 但属硬性故障），且 32 位平台上 float64→int 还存在窄化风险。

建议：在 `LoadConfig` 中做溢出安全的换算（如用 big/边界判断或直接用 `time.ParseDuration` 字符串配置），并在 `Validate()` 中强制 `SubprocTimeout > 0` 且不小于消费方可表示的最小粒度（≥1s），或在 `Config` 中直接以“秒”为单位暴露给 `NewCipher`。

### [2] high/security
默认后端就是仅供 dev/test 的 `aes_fallback`，而 `Validate()` 又无条件下接受它。这意味着生产部署只要漏配 `CDS_SM4_AEAD_BACKEND`（环境变量丢失、编排模板漏渲染、容器重启后 env 未继承），插件就会以非 GM 的 AES-128-GCM 静默加密用户卷数据：`main.go:46` 只在启动时打一条 WARNING，进程照常 `Serve()`，没有任何机制阻止合规性被破坏，事后新写入的卷还会把 `cipher` 记成 fallback 名字（`layout.go` manifest），事后需重新加密。

对合规敏感的加密后端，建议改成显式选择：`Backend` 默认值置空并在 `Validate()` 中要求必须显式设置；若确需保留 fallback，则要求同时存在显式开关（如 `CDS_VOLUME_PLUGIN_ALLOW_NON_GM_BACKEND=1`），否则返回错误拒绝启动，并考虑用 `log.Fatal` 之外的方式（如上报控制面/健康检查置为不健康）让降级可被发现。

### [3] high/security
`tongsuo` 后端完全依赖这个 helper 路径，但 `Validate()` 既不要求它非空，也不要求它是受控的绝对路径，默认值 `sm4_gcm` 还是个裸名字。`cipher.go:45` 直接 `exec.Command(c.helperPath, op)`，当 `helperPath` 不含 `/` 时 Go 会走 `$PATH` 查找——进程环境中的 `PATH` 一旦包含可被低权限写入的目录，即可劫持该“helper”；而 stdin 帧里前 `dekLen`(16) 字节就是明文 DEK（`helperFrame` + `copy(in, key)`），劫持者可直接窃取卷主密钥并伪造 tag/密文，直接击穿 fail-closed 前提。此外 `tongsuo` 后端下 `HelperPath=""` 只会在每次 Seal/Open 时才在 `cmd.Start()` 报错，属运行期故障而非启动期拒绝。

建议在 `Validate()` 中：当 `c.Backend == "tongsuo"` 时要求 `HelperPath` 非空且 `filepath.IsAbs(...)`，并进一步校验其归属/权限（如 `os.Open` 后 `Stat` 检查属主为 root、无 group/other 写位），拒绝任何依赖 `PATH` 解析的配置。

### [4] medium/bug
`envInt64` 把 `strconv.ParseInt` 的错误完全丢弃，非法数值（`"abc"`、`"10s"`、超 int64 的字面量）会被静默替换成默认值，运维误配无法被发现。这里的数值都是安全边界（`CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES` 是单卷明文/密文上限，`_SUBPROC_TIMEOUT_MS` 是 helper 超时保护），静默降级会让实际生效的边界与运维预期不一致，而且启动日志里只打印了 backend/node/root/socket，根本看不出数值被回退过。

建议：改成返回 `(int64, error)`（或在回退时 `log.Printf` 明确告警），让 `LoadConfig`/`Validate` 能够以 fail-closed 的方式拒绝非法配置；同时顺手 trim 空格并拒绝带前后空白的值。

### [5] medium/security
`Validate()` 对这两个控制面凭据零校验，而 `kms.go:64` 是裸字符串拼接 `c.base+path` 后交给 `http.NewRequestWithContext`，`kms.go:69` 在 token 为空时干脆不发 `Authorization` 头：
- URL 未做 scheme/host 合法性与白名单校验：运维若把它写成 `kms.internal:8000`（缺 scheme）会在每个 DEK 操作上运行期报错；写成 `http://kms.internal` 时 `KMSToken` 会以 `Bearer` 明文走公网/内网链路，与注释声明的 “mTLS 是生产传输” 不符，属凭据明文外泄；拼接语义还让 `base` 带尾斜杠/多余 path 时产生非预期路由。
- token 为空也照常启动，插件会对控制面发未认证请求，故障只在第一次 Attach/Create 才暴露，而不是启动时 fail-closed。

建议：在 `Validate()` 中用 `url.Parse` 校验 scheme 只允许 `https`（或 `http` 且 host 为 loopback，匹配现有默认值），拒绝空 host / 带 userinfo / 带 path 的 base，并要求 `KMSToken != ""`（或显式的 dev 开关）。同时把 `c.base` 规范化去掉尾 `/`，避免拼接出 `//api/...`。

### [6] medium/bug
`Validate()` 的覆盖面明显小于消费方预期：
- `SubprocTimeout` 未校验为正（见前述毫秒溢出 → 负 Duration → helper 立即被 Kill，§2.9 超时保护失效）。
- `SocketPath` 未校验为绝对路径（与 `VolumeRoot` 的处理不一致）。`server.go:140-149` 直接 `filepath.Dir` + `net.Listen("unix", ...)`，相对路径会让 socket 落在进程 CWD，规范要求的 `root:cds 0600` 端点位置随之漂移，Cubelet 可能连到非预期路径。
- `VolumeRoot` 只检查首字符为 `/`，`"/"`、`/..`、含 `..` 段的路径都能通过；随后 `layout.go:58` 用 `filepath.Join(r.cfg.VolumeRoot, volumeID)` 构造卷目录并落盘 `data.ct`/manifest，root 本身被污染时卷数据会落到非预期目录。

建议在此处补齐：`c.SubprocTimeout > 0`、`filepath.IsAbs(c.SocketPath)`、以及 `c.VolumeRoot` 必须等于其 `filepath.Clean` 结果且长度 > 1（拒绝 `/` 与含 `..` 的形态）。

## cds-volume-plugin/kms.go (6)
### [1] high/bug
`io.ReadAll` 的错误被丢弃，并且只把 `>=300` 当失败，这与文件宣称的 fail-closed 语义相矛盾：
1) 响应体读取中途失败（连接被重置、chunked 截断、TLS 错误）时 `data` 可能为空/半截，但只要 `StatusCode < 300` 就返回 nil（成功）。对 `RevokeDEK`（`out == nil`）而言，这会向上层（plugin.go `abortAfterRegister`）谎报“DEK 已销毁”，实际密钥仍留在控制面，属于安全侧的 fail-open。
2) 状态码语义未按接口约定校验：`RevokeDEK` 契约是 204，任何 2xx 都被接受；`200` + 空/HTML 响应体也会被当作撤销成功。

建议：显式处理读取错误；对需要读取响应体的调用（Register/Unwrap）在 `len(data)==0` 时报错；对 DELETE 校验 `StatusCode == http.StatusNoContent`（可通过给 `do` 增加期望状态码参数实现）。

### [2] high/security
HTTP 失败时把原始响应体直接拼进 error，而 error 会被上层原样记录/透出（plugin.go:70 包装后返回 gRPC，plugin.go:94 直接 `log.Printf`）。`RegisterDEK` 的请求体里带的是**明文 DEK 的 hex**，一旦控制面返回 4xx 校验类错误（例如 Pydantic/FastAPI 的 422 会把字段 `input` 回显到错误 detail 中），明文 DEK 就会随响应体进入 error 并落盘到日志，造成密钥泄漏；同理 Bearer token 也可能出现在某些鉴权错误的响应体里。

建议：对透出前做截断+脱敏（例如只保留状态码与最多 N 字节的白名单化错误摘要），或在带敏感字段的 `RegisterDEK` 路径上只记录状态码，不携带响应体。

### [3] medium/security
构造函数对传输安全没有任何约束：不校验 `baseURL` 的 scheme，也不设置 `CheckRedirect`。结合 config.go:61 的默认值 `http://127.0.0.1:8000`，一旦部署时把 `CDS_VOLUME_PLUGIN_KMS_BASE_URL` 配成 `http://<远端>`，`RegisterDEK` 会把明文 DEK、`UnwrapDEK` 会把明文 DEK、以及 `Authorization: Bearer <service token>` 全部以明文发送，与注释里“mTLS 是生产传输层”的约定不符且不会报错。

另外默认 `http.Client` 会跟随重定向：对 307/308 会向新目标重放请求体，即把明文 DEK 再次发给对端选择的地址。

建议：在 `NewKMSClient` 中拒绝非 `https` 的 baseURL（或仅在显式的 dev/insecure 开关下允许），并注入 `CheckRedirect` 拒绝跨主机重定向；同时在注释中明确注入的 `*http.Client` 必须携带控制面 CA。

### [4] medium/security
`ref` 未经校验/转义就拼进 URL 路径。`ref` 并非只来自本地：`RegisterDEK` 直接返回控制面响应里的 `key_id`（未做格式校验）并写入 manifest 与 private_data，随后 `UnwrapDEK`/`RevokeDEK` 原样拼接。若控制面（或被篡改的 manifest / private_data 与 manifest 同源于外部输入）给出含 `..`、`/`、`?`、`#`、`%2f` 或 `@`/CRLF 的 `key_id`，请求会被发到非预期路由（例如 `POST /api/v1/kms/volume-keys/../../admin/.../distribute`，或直接改变查询串/片段），造成越权取密钥、误撤销其他卷的 DEK 等。plugin.go:207 的 `ref != m.DekRef` 比较不能防护这一点，因为 `m.DekRef` 本身就是这个未校验的 `key_id`。

建议：对 `ref` 做白名单格式校验（如仅允许 `[A-Za-z0-9_-]{1,128}`），不合法即 fail-closed，并使用 `url.PathEscape(ref)` 拼接。

### [5] medium/security
同 `UnwrapDEK`：`ref` 直接拼进 DELETE 路径，未做格式校验与 `url.PathEscape`。撤销接口的破坏性更强——一个含 `../` 或额外路径段的 `key_id` 会让请求命中控制面的其他路由，可能误删/误操作非本卷的密钥记录。建议与 `UnwrapDEK` 共用同一个 `validateKeyRef` 白名单校验（在 `RegisterDEK` 拿到 `key_id` 时即校验一次，实现 fail-closed）。

### [6] low/bug
两处小问题：1) `c.base+path` 为裸字符串拼接，`CDS_VOLUME_PLUGIN_KMS_BASE_URL` 若带结尾 `/` 或额外 path 段，会产生 `//api/v1/...` 或把 `/api/v1` 追加到错误前缀下，表现为难以定位的 404；建议在 `NewKMSClient` 中用 `url.Parse` + `strings.TrimRight(base, "/")` 规范化，并对空/非法 baseURL 直接返回错误（当前构造函数无错误返回，配置错误只能在首次 RPC 时才暴露）。2) 这里与上面的 `json.Marshal` 错误未带任何 kms 上下文，与其他分支错误风格不一致，排障时难以判断是 KMS 客户端问题。

## cds-volume-plugin/layout.go (6)
### [1] high/bug
`exists()` 把 **任何** `os.Stat` 失败都当成“卷不存在”，而 `loadManifest` 又据此返回 `ErrVolumeNotFound`。这不是无害的错误转换：

1. `Plugin.CreateVolume` 的逻辑是 `loadManifest` 报 NotFound 才继续创建。若 manifest 实际存在但 Stat 因 `EACCES`/`ELOOP`/瞬时 `EIO`/目录被临时卸载等原因失败，Create 会当成新卷：向 KMS 注册**新 DEK**并 `saveManifest` **覆盖旧 manifest**，旧 `data.ct` 是用旧 DEK 加密的，从此永久不可解密（数据不可逆丢失），同时旧 DEK 成为孤儿密钥。
2. `Destroy`/`GetVolumeInfo`/`Attach` 在真实 I/O/权限故障时会向调用方返回 `codes.NotFound`（`mapErr`），运维侧看到的是“卷不存在”，掩盖了节点故障。
3. `exists()` 与随后的 `ReadFile` 之间还存在 TOCTOU。

应只在 `os.IsNotExist(err)` 时视为不存在，其他错误原样返回（fail-closed），由 `loadManifest` 区分 `ErrVolumeNotFound` 与真实故障。

### [2] high/bug
固定临时文件名带来两个问题：

1. **并发互相覆盖**：同一 `path` 的两个并发写共享同一个 `data.ct.tmp`。`PutVolumeData` 对 `ct`/`nonce`/`tag` 三次调用 `writeAtomic`，两个并发 Put（gRPC 并发 + 客户端重试）会交叉写同一个 tmp 并各自 rename，可能落盘“新 ct + 旧 nonce/旧 tag”这种撕裂组合，卷从此永久解密失败；`Attach` 写 `stageFile` 与 `Detach`/`shredFile` 并发时，rename 还可能把已 shred 的文件替换回明文。
2. **无持久性保证**：`WriteFile` 后未 `File.Sync()`，rename 后也未 fsync 父目录。崩溃时 rename 可能先于数据块落盘对目录可见，重启后 `manifest.json` 是 0 长度 → `manifest corrupt`，卷不可用（这正是原子写想避免的）。

建议用 `os.CreateTemp(dir, base+"*.tmp")` 生成唯一名，写完 `Sync`，rename 后对目录 `Sync`，并在 `Manifest` 写入后校验可读。

### [3] medium/bug
配合上面的 `exists()` 修改：这里应把 `Stat` 的非 ENOENT 错误如实返回，而不是落入 `ErrVolumeNotFound`；`os.ReadFile` 返回 `ENOENT`（exists 与 Read 之间被并发删除）时也不应被当成“文件损坏/成功”，需显式映射为 `ErrVolumeNotFound`，否则上层（`mapErr`）拿到的是 `FailedPrecondition`，语义与并发删除的真实情况不一致。

### [4] medium/security
`paths()` 是 VolumeRoot 目录约束的唯一构造点，但完全没有净化 `volumeID`：`validateVolumeID`（禁 `/`、`\`、`.`、`..`）只在 `CreateVolume` 入口调用，`Attach`/`PutVolumeData`/`Destroy`/`GetVolumeInfo` 都没调用，而 gRPC 侧 `server.go` 对 `volume_id` 只校验了非空。于是 `volume_id = "../../etc/x"` 这类输入会让 `dir` 跳出 `VolumeRoot`。目前仅靠 `loadManifest` 的 `m.VolumeID != volumeID` 兜底（要求越界目录里恰好存在 `volume_id` 字段完全一致的 manifest），一旦被绕过，`Destroy` 会对任意路径执行 `shredFile` + `os.Remove(p.dir)`，变成任意文件销毁。建议在构造路径的这个汇聚点集中做 fail-closed 校验，而不是依赖每个调用点自觉。

### [5] medium/security
`Attach` 通过 `writeAtomic(stageFile, plain, 0400)` 落盘明文，但这里的 tmp 在失败路径上不会被清理：只要 `os.WriteFile` 成功而 `os.Rename` 失败（ENOSPC/权限/跨设备），带完整明文的 `<stage>/data.tmp` 就会残留。而 `Detach` 只 shred `filepath.Join(hostPath, "data")`，随后的 `os.Remove(hostPath)` 又因目录非空失败且返回值被 `_ =` 丢弃 —— 结果是明文在 session 结束后仍留在 host staging 目录，违反“session 结束即擦除明文”的约定，且 `Detach` 仍向 Cubelet 返回成功。另外 `os.WriteFile` 不带 `O_EXCL`/`O_NOFOLLOW`，残留的 `data.tmp`（或攻击者预置的同名符号链接）会被直接跟随覆写。建议失败时删除并（明文场景）shred tmp，tmp 用 `O_CREATE|O_EXCL` 创建。

### [6] medium/security
这里的 containment 校验是纯词法的：`filepath.Abs`/`Join`/`Rel` 都不解析符号链接，而函数注释把它描述成 upstream `host_path ⊂ base_dir` 的 “self-proof”。实际可绕过：若 `baseDir` 的任一层级（或已存在的 `cds-<volume_id>` 目录）是指向别处的符号链接，返回的 `hostPath` 在文本上仍在 base 内，但实际解析到 base 之外；`Attach` 里 `os.MkdirAll(hostPath, 0700)` 对已存在的 symlink→dir 会静默成功，随后明文被写进链接目标目录，并把该 `hostPath` 作为 host_path 交给 Cubelet 以 virtiofs RO 挂进 VM —— 等价于把明文暴露到约束之外的路径。建议对 `base` 与 `d` 做 `filepath.EvalSymlinks`（或至少对已存在的 `d` 做 `os.Lstat` 拒绝 `ModeSymlink`）后再做包含性判断，并保证 base 及其父链不被非特权方控制。

## cds-volume-plugin/rootfs.go (5)
### [1] critical/security
**[critical / security] 只拒绝目录，未阻止 symlink/特殊文件 —— guest 可控 rootfs 可读取宿主文件并造成 DoS**

rootfs 镜像是 guest 正在写入的文件（见文件头注释），`upper/` 下的内容完全由 guest 控制。这里用 `os.Lstat` 只判断了 `IsDir()`：
1. 若 `src` 是指向宿主路径的 symlink（如 guest 写 `results/out -> /etc/shadow`，或相对链接 `../../../../../../etc/passwd`），`Lstat` 得到的是链接本身（非目录、`Size()` 只是目标字符串长度），随后 `os.ReadFile(src)` 会跟随链接解析到 **挂载点之外** 的宿主文件系统。插件以 root 运行（需要 CAP_SYS_ADMIN 才能 mount），因此这是一个稳定的宿主任意文件读取原语；同时 64MB 上限检查也被绕过（校验的是链接长度而非目标大小）。
2. 若 `src` 是 FIFO/字符设备，`os.ReadFile` 会一直读到“无 EOF”，请求线程被永久挂起且内存无上界；Lstat 之后 guest 仍可把普通文件替换为 symlink（TOCTOU），因为挂载期间 guest 持续在写。

修复：在打开时限定不跟随链接并重新校验文件类型，读取时按 fd 限长，而不是依赖事前的 Lstat。

### [2] high/security
**[high / security] `cubeboxID` 未过滤 glob 元字符，且多匹配时静默取 `matches[0]`**

`cubeboxID` 只校验了长度与 `/\`，但 `*`、`?`、`[` 等会原样进入 `filepath.Glob` 模式：控制平面（或任何能调用该 RPC 的一方）传入 `*` 即可匹配到其它 cubebox 的 `cds-rootfs-*` 镜像，从而跨沙箱读取别的租户 rootfs；即使不含元字符，同一 cubebox 存在多个历史/陈旧 session 目录时，`matches[0]` 只是字典序第一个，返回的可能是**已销毁 session 的旧镜像**，导致读到错误字节或错误的 exists=true/false（调用方在轮询 marker 文件时会误判）。

建议：对 ID 做白名单校验（如 `^[A-Za-z0-9._-]+$`）以消除 glob 注入；不匹配模式改用 `os.ReadDir` + 前缀比较；多匹配时返回歧义错误或按明确规则（如 mtime 最新）选择并记录日志，同时确认 `volDir` 是目录。

### [3] medium/bug
**[medium / bug] mount/umount 无 context 与超时，阻塞会挂死请求路径并拖延清理**

`mount -o loop` 可能对后备 XFS 对象文件做 journal recovery 或因后端 I/O 卡住而长时间不返回；`umount` 在目标 busy 时同样可能长时间阻塞。调用方 `extensionService.ReadSandboxFile` 已持有 `ctx` 但被丢弃，因此 gRPC handler 会无限期挂起（客户端取消也无法中止子进程），挂载点/loop 设备与 `defer` 的临时目录清理同时被推迟，并发请求可耗尽插件的 handler。

建议：使用 `exec.CommandContext(ctx, ...)`（或独立的 `context.WithTimeout`）给 mount/umount 设置上界，并在正常 umount 失败时退化为 `umount -l`。

### [4] medium/bug
**[medium / bug] umount 失败后的清理被静默丢弃，临时目录与 loop 设备会持续泄漏**

由于 defer 是 LIFO，`umount` 先于 `defer os.RemoveAll(mountPoint)` 执行；一旦 umount 返回 busy（该函数明确承认这种情况只“日志记录”），随后的 `RemoveAll` 会在仍挂载的目录上失败，错误被完全忽略：临时挂载点与对应 loop 设备永远得不到回收。该 RPC 由调用方轮询 marker 文件反复触发（见函数注释），长时间运行后 `/run/cds-volume-plugin` 与 loop 设备表都会累积，最终 mount 失败（资源耗尽）导致功能整体不可用。

建议：umount 失败时用 `umount -l` 重试；`RemoveAll` 的 error 一并记录；启动/失败路径上对遗留挂载做一次清理（读取 /proc/mounts 或 `findmnt` 后 umount+删除）。

### [5] low/bug
**[low / bug] `MkdirTemp` 依赖 `/run/cds-volume-plugin` 预先存在，但本文件从未创建它**

`os.MkdirTemp` 不会创建父目录，父目录缺失时直接返回 ENOENT，使整个 `ReadSandboxFile` 以 "mountpoint: no such file or directory" 失败。该目录只是恰好等于默认 socket 的目录（`Serve` 里 `os.MkdirAll(filepath.Dir(cfg.SocketPath))`），而 socket 路径可配置（`CDS_CUBE_VOLUME_PLUGIN_SOCKET`），插件也不保证先经过该路径（selftest/ctl 入口），因此不能依赖此副作用。

建议：显式 `os.MkdirAll(dir, 0700)` 后再在其下 `MkdirTemp`，并把目录作为可配置项。

## cds-volume-plugin/main.go (4)
### [1] high/security
非国密降级只打印 WARNING 就继续 `Serve`，与本文件开头声明的“fail-closed”铁律不一致：`Config.Validate()`（config.go:72-76）显式接受 `aes_fallback`，`NewCipher()`（cipher.go:153-154）也直接返回 AES 实例，整条启动链路上没有任何硬门禁。而 `CDS_SM4_AEAD_BACKEND` 的默认值就是 `aes_fallback`（config.go:58），因此任何未显式注入该 env 的部署（systemd/裸机启动、K8s 里 env 被覆盖或 typo 成非法值以外的字符串之外的场景）都会静默以 AES-128-GCM 提供生产加密卷服务，仅留下一行日志；一旦写入了 AES 密文卷，之后切回 tongsuo 又会造成 manifest cipher 与实际后端不一致。建议把降级改为需显式 opt-in（例如仅当 `CDS_VOLUME_PLUGIN_ALLOW_DEV_CIPHER=1` 时允许），否则在 `Validate`/此处直接返回错误拒绝启动；至少应在 tongsuo 后端下强制自检 helper 存在（现已由 RunSelfTest 覆盖），并对降级加上可被监控捕获的独立退出码/审计日志。

### [2] medium/security
KMS 客户端在此处构造后直接进入长驻服务，但没有任何启动期安全校验：`KMSBaseURL` 默认是 `http://127.0.0.1:8000`（config.go:61），`NewKMSClient` 也只用默认 `&http.Client{Timeout:...}`，未注入 TLS/mTLS 配置（kms.go:47-53），而 `RegisterDEK` 会把 DEK 明文 `dek_hex` 放进请求体、`Authorization: Bearer <token>` 放进 header（kms.go:68-71, 98-103）。也就是说，只要部署把 `CDS_VOLUME_PLUGIN_KMS_BASE_URL` 指向远端而忘记 https/未挂 mTLS，密钥材料与服务凭据就会明文出网，与 kms.go 注释声明的“prod transport is mTLS”只有文档、没有实现。建议：启动时校验 `KMSBaseURL` 的 scheme（生产仅允许 https，明文 http 需显式 dev 开关）、按需注入带 CA/mTLS 的 `*http.Transport`，并考虑对空 `KMSToken` 做 fail-fast 校验，避免服务起来后每次 Create/Attach 才在运行时失败。

### [3] medium/bug
serve 路径没有任何取消/优雅关闭通道：`Serve` 内部 `srv.Serve(lis)`（server.go:165-171）阻塞，而这里既没有 `signal.NotifyContext`/`signal.Notify`，也没有把 context 传下去，容器停止或 systemd restart 收到 SIGTERM 时进程会被直接终止。由于 Attach/PutVolumeData/Destroy 都是“KMS 调用 + 多步文件写入”的组合操作（plugin.go:67-87, 171-187, 380-399），中途被杀会留下 KMS 侧已注册但 manifest 未落盘的 orphan DEK、或 shred 未完成的明文 staging 文件。建议在 `serveCmd` 建立 signal 派生的 context，并将其传递给 `Serve`，在收到 SIGTERM/SIGINT 时调用 `srv.GracefulStop()`（配合 socket 清理），使 in-flight 请求能收敛。

### [4] low/bug
`cfg.Validate()` 没有校验 `SubprocTimeout`，而 `NewCipher` 用 `int(cfg.SubprocTimeout.Seconds())` 传秒（cipher.go:152）：把 `CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS` 配成 500（或任意 <1000 的值）会被截断为 0，`time.After(0)` 立即就绪，导致 tongsuo 后端每次 helper 调用都判为超时；配上负值同理。表现是自检随机失败/拒绝服务，排查成本高。建议在 `Validate` 中要求 `SubprocTimeout >= 1s`（或直接把 `time.Duration` 透传给 `TongsuoCipher`，避免秒级截断）。

## alembic/versions/0012_user_mfa.py (4)
### [1] medium/bug
`totp_secret_encrypted` 用 `String(512)` 固定长度，但同表的 `recovery_codes_encrypted` 用 `Text`，两者都是 AES/SM4-GCM（nonce 12B + tag 16B + 密文）再 base64 编码的密文，容量策略不一致。TOTP 密钥当前只有几十字节密文，512 足够；但一旦加密实现改为信封加密（写入 key_id/算法版本前缀）或该列被复用来存更长的 blob，会直接触发 PostgreSQL `value too long for type character varying(512)` 写入失败，而 MFA  enroll/轮换失败对用户是不可恢复的。建议与恢复码列保持一致使用 `sa.Text()`（或至少放宽到足够容纳信封加密后的长度），并让 `app/models/user_mfa.py` 同步调整。

### [2] medium/bug
缺少状态一致性约束：当前 schema 允许 `confirmed = true` 且 `totp_secret_encrypted IS NULL`（以及 `confirmed = true` 但没有恢复码）。按模型注释，`confirmed` 为真时才强制 MFA 登录，这种不一致行会让用户被要求做 MFA 却没有可用密钥，从而永久无法登录（或迫使实现回退到跳过 MFA，形成认证绕过）。建议加数据库级 CHECK 约束兜底，例如 `CHECK (confirmed = false OR totp_secret_encrypted IS NOT NULL)`。

### [3] low/bug
缺少索引：`user_id` 作为主键已经建了唯一索引，PostgreSQL 会为主键自动创建索引，但**外键 `user_id -> users.id` 的引用侧不需要索引，而 CASCADE 删除用户时会全表扫描 `user_mfa` 来定位子行**（表变大后账号删除会明显变慢/持锁变长）。此外如果业务存在按 `confirmed` 统计/查询 MFA 启用率的场景，目前也没有对应索引。请确认账号删除路径的访问模式，必要时补 `op.create_index` 到 `user_id`（若主键索引不覆盖该查询模式）或 `confirmed` 的部分索引。

### [4] low/other
`downgrade()` 直接 `drop_table("user_mfa")` 会不可逆地清除所有用户的 TOTP 密钥与恢复码，而重新 `upgrade` 后表为空意味着这些账号变为“未开启 MFA”，属于安全防护等级静默降级（而不是回到上一个已知状态）。这与同库中普通业务表的回滚不同，建议在迁移 docstring 中显式声明“回滚会丢失全部 MFA  Enrollment”，并在部署流程中要求先备份该表（如 `CREATE TABLE user_mfa_bak_0012 AS SELECT * FROM user_mfa;`）后才允许 downgrade。

## cds-volume-plugin/selftest.go (3)
### [1] medium/test
自检只验证了往返一致，没有断言“确实发生了加密”：若某个后端（尤其是 tongsuo 子进程 helper）出现 Seal 直接把明文当密文返回、或忽略 nonce 的实现缺陷，本自检仍会通过，插件随即开始服务并以“明文即密文”落盘，违反本项目的 fail-closed/禁伪造铁律。建议在拿到 ct/tag 后加两条廉价断言：ct 长度应等于明文长度且 ct 内容不得等于明文；同时用不同 nonce 再 Seal 一次，断言密文不同（证明 nonce 真正参与）。这样负向路径 + 正向路径才能共同覆盖“helper 只做转发/未加密”这类失效模式。

### [2] medium/test
负向覆盖缺少 AAD 篡改用例：Cipher 契约（cipher.go 中 Open 必须对 tag/ciphertext/**aad** 任一不匹配报错）包含 AAD，而生产路径 Attach 用的是 manifest 里的 m.AAD（卷名），PutVolumeData 允许调用方传入 aad。若底层 helper 忽略 AAD（GCM 中 GHASH 未覆盖 aad 是常见实现错误），本自检仍会通过，导致密文可被跨卷/跨标签替换挂载而不被发现。建议在标签篡改用例后再加一条“换 AAD 必须失败”的断言；同时 Open 失败时不要直接丢弃返回值——契约要求不得返回未认证明文，可断言返回的明文为 nil，避免实现把未认证明文回传后被上层误用。

### [3] medium/bug
对 ct/tag 直接取 `bad[0]`/`badtag[0]` 做翻转：Cipher 是接口，实现（含未来新增后端或有缺陷的 helper 封装）若返回长度为 0 的 ciphertext 或 tag，这里会 panic（index out of range）。自检的职责是把不可用的后端以错误形式拒绝服务，panic 会丢失可读诊断且绕过调用方的错误链（main 中 selftest 子命令走 os.Exit(runCLI(...))，panic 只能靠退出码体现）。建议先做显式长度校验，再执行翻转。

