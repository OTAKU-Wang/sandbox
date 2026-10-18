# w3 收尾增量（E2 切片之后新出的条目）

## cds-volume-plugin/.gitignore (4)
### [cds-volume-plugin/.gitignore] medium/maintainability
现码：
```
sm4gcm/sm4_gcm
sm4gcm/sm4_gcm-static
```
构建产物忽略不完整：`sm4gcm/Makefile` 只清理 `sm4_gcm` 和 `sm4_gcm-static` 两个显式目标，但实际开发中该目录还会出现编译器中间产物（`*.o`/`*.d`/`*.gch`）以及按 Dockerfile 习惯重命名出来的静态助手（`mv sm4_gcm-static /out/cds-sm4-gcm`，开发者在本地执行同样命令时会留下 `cds-sm4-gcm`）。这些名字不在当前两条精确规则内，容易被 `git add .` 误提交成二进制。建议补一组目录内通配规则，避免逐个追名字。

### [cds-volume-plugin/.gitignore] medium/security
现码：
```
bin/
tongsuo-build/
```
敏感产物缺少忽略规则：`config.go` 显示服务凭据 `CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN` 来自环境，`AGENTS.md` 说明生产 KMS 走 mTLS，因此本地调试几乎必然会产生 `.env`、client 证书/私钥（`*.pem`/`*.key`/`*.crt`/`*.ca`）之类的文件；`make test`/`docker` 排障也会留下日志。当前 5 行规则一个都没覆盖，一旦开发者把凭据或密钥材料放进仓库目录就会随 `git add .` 泄露。建议增加一组凭据/密钥/日志忽略规则（并在需要时用 `!` 明确放行必须提交的示例文件）。

### [cds-volume-plugin/.gitignore] low/maintainability
现码：
```
*.tmp
```
`*.tmp` 只在 Linux 上匹配小写扩展名（gitignore 通配默认大小写敏感，仅 `core.ignorecase` 的 macOS/Windows 才不敏感），同时也覆盖不到编辑器/工具产生的临时文件（`*~`、`*.swp`、`*.bak`、`.idea/`、`.vscode/`）。另外 `layout.go` 的 `writeAtomic` 生成的是 `path + ".tmp"`，若在 repo 内的 VolumeRoot 下崩溃会留下 `manifest.json.tmp`——这类残留与运行时数据同样不应入库。建议显式补上常见临时/编辑器产物。

### [cds-volume-plugin/.gitignore] low/maintainability
现码：
```
tongsuo-build/
```
还缺少 Go/C 混合工程常规测试与分析产物：`make test`（`go test ./...`）在指定 `-o` 或 `-coverprofile` 时会生成 `*.test`、`coverage.out`、`*.prof`、`cpu.pprof` 等文件，`make docker` 也可能留下导出的镜像 tar；这些都不在当前规则内，会被误提交。另外若后续为离线构建引入 `vendor/`，也需要一并约定（是否入库需明确，不能靠默认行为）。建议补一条测试/覆盖率产物规则。

## cds-volume-plugin/Dockerfile (9)
### [cds-volume-plugin/Dockerfile] high/test
现码：
```
RUN /out/cds-sm4-gcm frobnicate; test $? -eq 1 && echo helper-usage-ok
```
helper 的构建期校验严重不足，而“静态链接成功”恰恰是本镜像最关键的隐含假设：
1) 没有 `ldd`/`file` 断言。第 10 行注释宣称“静态链接 libcrypto.a 所以运行阶段不需要 Tongsuo 共享库”，但 Makefile 只把 libcrypto.a 静态链进来，libc 仍是动态链接；如果 libcrypto.a 是用非默认 PIC/依赖（如 `-lcrypt`、provider 模块）构建的，最终 slim 镜像里会以 `error while loading shared libraries` 或运行时 dlopen 失败的形式炸掉，构建阶段完全看不出来。
2) 唯一的冒烟测试只跑了 usage 分支（退出码 1），从未执行过一次真实 encrypt/decrypt，即 `EVP_sm4_gcm` 是否真的可用、`sm4_gcm_open` 的 fail-closed 路径是否正确，全部未覆盖。
3) `cmd; test $? -eq 1 && echo ok` 这种写法可读性差且不 fail-fast（依赖 `$?` 的展开时机，后续任何人加一行命令就会静默失效）。
建议改为真正跑一遍 round-trip 并断言静态依赖闭包。

### [cds-volume-plugin/Dockerfile] high/bug
现码：
```
RUN groupadd --system cds \
 && useradd --system --gid cds --no-create-home --shell /usr/sbin/nologin cubelet
```
这里创建 `cds` 组与非 root 的 `cubelet` 用户，注释声明的策略是“socket root:cds 0600，Cubelet 运行用户加入 cds 组”，但该策略自相矛盾：server.go 的 `Serve()` 执行的是 `Chmod(socket, 0600)` + `Chown(path, 0, gid)`，0600 是**仅属主可读写**，组位为 0，因此属于 cds 组的非 root 用户 `cubelet` 根本无法 dial 这个 socket（只能 root 访问）。结果是：要么 Cubelet 必须以 root 运行（则本处建用户的意义落空），要么插件起来后控制面/节点侧连接全部 permission denied。
另外此处只定义了身份，却没有 `USER` 指令：容器实际仍以 root 运行；若部署方改用 `--user cubelet`，`Serve()` 中的 `Chown(...,0,-1)`、socket 目录创建以及 rootfs.go 的 loop mount 都会失败，而这些失败在代码里只是 `log WARN`，会造成“看似正常但权限不合规”的静默降级。
建议：明确策略（组可访问用 0660，root 独占则去掉 cubelet 用户与组声明），并把 UID/GID 固定（`--uid/--gid`）以保证多节点挂载卷目录时属主一致。

### [cds-volume-plugin/Dockerfile] high/security
现码：
```
COPY --from=plugin /out/cds-volume-plugin /usr/local/bin/cds-volume-plugin
COPY --from=helper /out/cds-sm4-gcm /usr/local/bin/cds-sm4-gcm
```
最终镜像基于 `debian:bookworm-slim`，未安装 `ca-certificates`（且没有 apt 层），镜像内不存在 `/etc/ssl/certs/ca-certificates.crt`。而 kms.go 的注释明确“mTLS with certificate_service CA is the production transport”，插件用默认 `*http.Client`（走系统根证书池）访问 KMS。因此生产环境一旦把 `CDS_VOLUME_PLUGIN_KMS_BASE_URL` 配成 https，所有 DEK 注册/分发调用都会在 TLS 握手阶段失败（x509: certificate signed by unknown authority）；由于 KMS 客户端是 fail-closed、无离线模式，CreateVolume/Attach 会全线不可用，且这个故障与镜像构建无关，只在运行期出现。

### [cds-volume-plugin/Dockerfile] medium/test
现码：
```
&& make -j"$(nproc)" > /dev/null \
 && make install_sw \
 && ls /tongsuo/lib*/libcrypto.a
```
Tongsuo 阶段的验收只到“文件存在”这一层：`no-tests` 关掉了测试，`ls ... libcrypto.a` 只证明产物存在，不证明它包含本插件真正依赖的 SM4-GCM 算法。若 Configure 出来的配置缺 SM4 provider/算法（或 ABI 与 helper 期望不一致），构建照样全绿，故障会推迟到容器启动时的 boot self-test 才暴露（serveCmd 里 self-test 失败即拒绝 serve），排障成本极高。另外 `make -j"$(nproc)" > /dev/null` 把 stdout 全部丢弃，只换来“日志干净”，却丢失了编译告警与失败上下文（错误仅在 stderr 里，跨 stage 定位困难）。建议：显式加一步符号存在性断言，并保留可诊断的输出。

### [cds-volume-plugin/Dockerfile] medium/bug
现码：
```
&& make PREFIX=/tongsuo sm4_gcm-static \
```
libdir 假设不一致：上一阶段用 `ls /tongsuo/lib*/libcrypto.a` 通配来兼容 `lib`/`lib64`，但 sm4gcm/Makefile 里 `TSLIB := $(abspath $(PREFIX)/lib64)` 把路径硬编码成 `lib64`。一旦基础镜像/Configure 把静态库装到 `/tongsuo/lib`（Debian 上 `--libdir=lib` 是常见配置），`ls` 检查会通过而 helper 编译会因找不到 `/tongsuo/lib64/libcrypto.a` 失败，报错信息（cc: No such file or directory）也看不出根因是 libdir。建议在 Configure 里固定 libdir，或把实际库目录显式传给 make。

### [cds-volume-plugin/Dockerfile] medium/test
现码：
```
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/cds-volume-plugin .
```
插件阶段没有跑 `go vet`/`go test`，也没有对成品二进制做任何自检。仓库里存在 `plugin_test.go` 与 `selftest` 子命令（main.go 的 `selftest` 会走 `RunSelfTest(cipher)`，即真正调起 helper），但镜像构建完全没用到——tongsuo 后端 + 静态 helper 的端到端组合直到容器启动才第一次被执行。建议在把二进制交给运行阶段前，用 tongsuo 后端跑一次 selftest（需要临时把 helper 拷进来），这样 libcrypto 静态链接、helper 协议、后端选择能在构建期一次性验证。

### [cds-volume-plugin/Dockerfile] medium/security
现码：
```
CDS_VOLUME_PLUGIN_SM4_HELPER=/usr/local/bin/cds-sm4-gcm
```
ENV 只设置了 socket/卷根/helper/后端，`CDS_VOLUME_PLUGIN_KMS_BASE_URL` 与 `CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN` 留给运行时注入。config.go 对前者给了明文本地默认值 `http://127.0.0.1:8000`，对后者默认空串，而 KMS 报文里传的是 `dek_hex` / `key_hex` 明文数据密钥、鉴权只是 `Bearer <token>`。也就是说：部署时漏配这两个变量不会有任何报错，插件会安静地用空 token 以明文 HTTP 传输 DEK（或连到根本不存在的 8000 端口）。对生产加密插件镜像而言，这类“安全参数有可用但不安全的默认值”应在镜像层消除。

### [cds-volume-plugin/Dockerfile] medium/bug
现码：
```
VOLUME ["/var/lib/cds/volumes"]
```
把卷数据目录声明为 `VOLUME` 有两个实际风险：1) 若编排方没有显式挂载 hostPath/named volume，Docker 会创建匿名卷，容器被删除/重建后密文数据即与匿名卷一起丢失，而本插件是 fail-closed、密文丢失等于数据永久不可恢复；2) 匿名卷/host 挂载的属主由宿主决定，而镜像没有预创建目录并设置 `root:cds` 所有权（见上一处反馈），首次写入时的权限完全取决于隐式行为。同时 `VOLUME` 会让构建期对该目录的任何 chown/chmod 被运行时覆盖，属于容易被误用的指令。

### [cds-volume-plugin/Dockerfile] low/maintainability
现码：
```
ARG GO_VER=1.25
```
构建不可复现：`golang:${GO_VER}-bookworm`（浮动 `1.25` 标签）与 `debian:bookworm-slim` 均未固定 digest，apt 安装的 `build-essential/gcc/libc6-dev` 也没有版本 pin，两次构建会产生不同的二进制与镜像层，无法回溯“线上这个镜像到底是哪套工具链产出的”。对一个加密插件镜像（供应链敏感）建议按 digest 固定基础镜像，并在 CI 中记录最终 digest。另：go.sum 校验本身没有被绕过（`go build` 默认 `-mod=readonly` 会校验），这里只是缺 pinning。

## cds-volume-plugin/Makefile (4)
### [cds-volume-plugin/Makefile] high/test
现码：
```
test:
	go test ./...
```
`test` 目标不依赖 `helper`，但 plugin_test.go:318-323 里 tongsuo 后端是“存在才测”（`if _, err := os.Stat("../sm4gcm/sm4_gcm"); err == nil`）：helper 没构建时该子测试被**静默跳过**，`go test ./...` 依然返回 0。这意味着最关键的 SM4-GCM/GM 加密路径（含防篡改、AAD 校验等负向用例）在干净 checkout 和 CI 上从来没跑过却显示绿色。另外本文件与镜像只构建 `bin/sm4_gcm`/`cds-sm4-gcm`，测试读的却是源码树路径 `../sm4gcm/sm4_gcm`，两条路径易漂移。建议让 test 依赖 helper，并像 Dockerfile:32 那样在缺少 helper 时显式失败而非跳过；插件带 mutex/子进程超时，也应开 `-race`。

### [cds-volume-plugin/Makefile] medium/bug
现码：
```
helper:
	$(MAKE) -C sm4gcm PREFIX=$(abspath $(TS_PREFIX)) sm4_gcm
	mkdir -p bin
	cp -f sm4gcm/sm4_gcm bin/sm4_gcm
```
`helper` 目标盲信子 make 的产物：既没有校验前置依赖 `tongsuo-build/` 是否已 `make install_sw`（头文件/`lib64/libcrypto.*` 是否存在），也没有确认 `sm4gcm/sm4_gcm` 真的生成出来就直接 `cp`。一旦 Tongsuo 未安装（CI、新克隆的机器，`tongsuo-build/` 已被 .gitignore 排除），子 make 的报错是一串晦涩的链接错误；而在 `make -k`/`make -i` 或子 make 静默不产物的场景下，`mkdir -p bin` 已执行、`cp` 失败，只留下一个不完整的 `bin/` 目录。建议加前置断言 + 产物存在性校验，并复用 Dockerfile:32 已有的 usage 冒烟检查（退出码 1 视为 OK），保证本地交付的 helper 至少可执行。

### [cds-volume-plugin/Makefile] medium/security
现码：
```
docker:
	cd .. && docker build -f cds-volume-plugin/Dockerfile -t cds-volume-plugin:dev .
```
`docker` 目标存在三个问题：(1) 以仓库父目录作为构建上下文，而仓库根目录没有 .dockerignore（当前仅 cds-frontend/.dockerignore），按 Dockerfile:4-5 的说明上下文会连带 406MB 的 vendored Tongsuo，外加本地 make 产物 `cds-volume-plugin/tongsuo-build/`、`bin/`、`sm4gcm/*.o` 等，构建极慢且很容易把本机 Tongsuo 安装树/密钥类文件送进 daemon；(2) 标签硬编码 `:dev`，任何人构建都会覆盖他人本地同名镜像，也无法产出可追溯版本；(3) 不依赖 `test`，镜像构建绕过了全部测试门禁。

### [cds-volume-plugin/Makefile] low/maintainability
现码：
```
local: helper
	mkdir -p bin
	go build -o bin/$(BIN) .
```
`local` 产出的运行形态与镜像/配置默认值不一致，容易在本地“构建成功但跑不起来”：Dockerfile:49-53 把 helper 安装为 `/usr/local/bin/cds-sm4-gcm` 并显式注入 `CDS_VOLUME_PLUGIN_SM4_HELPER`，而 `local` 只把 helper 落在 `bin/sm4_gcm`；config.go:59 的默认值是裸名 `sm4gcm`（依赖 PATH 查找），因此本地以 `CDS_SM4_AEAD_BACKEND=tongsuo` 启动 `bin/cds-volume-plugin` 时找不到 helper。此外镜像用 `CGO_ENABLED=0` 构建（Dockerfile:41），本地未固定该变量，本机若存在 CGO 环境会产出与镜像行为不一致的二进制。建议 helper 命名与镜像对齐，并在 local 目标显式固定 CGO_ENABLED。

## cds-volume-plugin/sm4gcm/Makefile (7)
### [cds-volume-plugin/sm4gcm/Makefile] high/security
现码：
```
$(BIN): sm4_gcm.c sm4_gcm.h
	$(CC) $(CFLAGS) -I$(TSINC) -o $@ sm4_gcm.c -L$(TSLIB) -lcrypto -Wl,-rpath,$(TSLIB)
```
构建前置条件缺失，且存在**静默链接到系统 libcrypto** 的风险：`-L$(TSLIB)` 指向不存在的目录时链接器只会警告/忽略（部分配置下连警告都没有），随后会从 `/usr/lib`、`/usr/lib64` 等默认搜索路径里解析 `-lcrypto`。这样即使 vendored Tongsuo 没有安装/构建失败，`make` 依然“成功”，但产出的二进制链接的是系统 OpenSSL（可能不含 SM4-GCM，或版本/行为不同），错误被推迟到运行时甚至静默降级——对 AEAD 这种安全关键路径不可接受。建议加上显式前置依赖，让缺失时在编译前就失败：

```make
TSLIB_DEP := $(wildcard $(TSLIB)/libcrypto.so)

$(BIN): sm4_gcm.c sm4_gcm.h $(TSLIB_DEP)
	@if [ -z "$(TSLIB_DEP)" ]; then \
	  echo "error: $(TSLIB)/libcrypto.so not found; build ../../Tongsuo first" >&2; exit 1; \
	fi
	$(CC) $(CFLAGS) -I$(TSINC) -o $@ sm4_gcm.c -L$(TSLIB) -lcrypto \
	  -Wl,-rpath,'$$ORIGIN/../lib64' -Wl,-rpath,$(TSLIB)
```

### [cds-volume-plugin/sm4gcm/Makefile] high/security
现码：
```
$(CC) $(CFLAGS) -I$(TSINC) -o $@ sm4_gcm.c -L$(TSLIB) -lcrypto -Wl,-rpath,$(TSLIB)
```
运行时库搜索安全问题：现在只写了 `-Wl,-rpath,$(TSLIB)`，有两点风险。
1. 现代链接器默认输出 `DT_RUNPATH` 而不是 `DT_RPATH`，`RUNPATH` 的优先级**低于 `LD_LIBRARY_PATH`**。sm4_gcm 是卷插件里的加密辅助进程，任何能控制其启动环境的一方都能用 `LD_LIBRARY_PATH` 把它换成另一个 `libcrypto.so`，从而绕过 vendored Tongsuo 的 SM4-GCM 实现。
2. `$(TSLIB)` 是构建机的绝对路径（`abspath` 展开），二进制一旦烘焙进镜像/搬到别的目录，该路径通常不存在，加载器会回落到系统库路径，同样出现“静默换了加密实现”。

建议用 `$ORIGIN` 相对路径（Makefile 中必须写成 `$$ORIGIN`，否则会被 make 吃掉）并追加 RUNPATH 语义收紧：

```make
	$(CC) $(CFLAGS) -I$(TSINC) -o $@ sm4_gcm.c -L$(TSLIB) -lcrypto \
	  -Wl,-rpath,'$$ORIGIN/../lib64' -Wl,-rpath,$(TSLIB) -Wl,--disable-new-dtags
```
同时请在 CI 里用 `readelf -d $(BIN)` / `ldd $(BIN)` 断言解析到的是 vendored 库。

### [cds-volume-plugin/sm4gcm/Makefile] high/bug
现码：
```
$(BIN)-static: sm4_gcm.c sm4_gcm.h
	$(CC) $(CFLAGS) -I$(TSINC) -o $@ sm4_gcm.c $(TSLIB)/libcrypto.a -ldl -pthread
```
静态变体依赖不完整，很可能链接失败：Tongsuo/OpenSSL 的 `libcrypto.a` 通常还引用 `z`（RAND/压缩）、`m`、`rt`（getentropy/时间）、以及 dl/pthread；另外静态档案存在库间循环引用，单个 `libcrypto.a` 放在命令行末尾时，若解析顺序不满足会报 undefined reference。建议：

```make
STATIC_LIBS := -Wl,--start-group $(TSLIB)/libcrypto.a -Wl,--end-group -ldl -lpthread -lz -lm -lrt

$(BIN)-static: sm4_gcm.c sm4_gcm.h $(TSLIB)/libcrypto.a
	$(CC) $(CFLAGS) -I$(TSINC) -o $@ sm4_gcm.c $(STATIC_LIBS)
```
另外注意：多数发行版默认 PIE，若 Tongsuo 静态库不是以 `-fPIC` 构建，这里还会报 `relocation R_X86_64_32 against ... can not be used when making a PIE object`；需要在 Tongsuo Configure 时加 `no-shared -fPIC`，或在本规则上补 `-no-pie`。建议在目标开头加一行 `[ -f $(TSLIB)/libcrypto.a ] || { echo "missing $(TSLIB)/libcrypto.a" >&2; exit 1; }`。

### [cds-volume-plugin/sm4gcm/Makefile] medium/bug
现码：
```
TSLIB  := $(abspath $(PREFIX)/lib64)
TSINC  := $(abspath $(PREFIX)/include)
```
库目录被硬编码为 `lib64`，但 Tongsuo/OpenSSL 的安装布局依赖 `--libdir`/平台：Debian/Ubuntu、Alpine/musl、aarch64 上很多构建实际装到 `<prefix>/lib`（注释里记录的 Configure 命令也没有显式 `--libdir=lib64`）。一旦换平台或换构建参数，`TSINC`/`TSLIB` 就指向不存在的目录，而 `-L` 对不存在的目录是**静默忽略**的（见下一条），最终表现为“链接到了别的 libcrypto”而不是清晰报错。建议把两者改成可覆盖并做一次存在性探测，例如：

```make
PREFIX ?= ../tongsuo-build
TSINC  := $(abspath $(PREFIX)/include)
# 允许显式覆盖；默认优先 lib64，找不到则回退 lib
TSLIB  ?= $(shell p=$(abspath $(PREFIX)); if [ -d $$p/lib64 ]; then echo $$p/lib64; else echo $$p/lib; fi)
```
并在下方增加对 `$(TSLIB)/libcrypto.*` 与 `$(TSINC)/openssl/evp.h` 的依赖校验。

### [cds-volume-plugin/sm4gcm/Makefile] medium/maintainability
现码：
```
$(CC) $(CFLAGS) -I$(TSINC) -o $@ sm4_gcm.c $(TSLIB)/libcrypto.a -ldl -pthread
```
依赖追踪不足，会造成静默的陈旧产物：目标只声明了 `sm4_gcm.h`，而 `sm4_gcm.c` 还包含 `<openssl/evp.h>` 及其大量间接头文件；同时也没有把 `libcrypto`（.so/.a）作为依赖。典型后果是重新构建/升级 vendored Tongsuo 后 `make` 认为“up to date”，二进制仍链接或按旧头文件的 ABI/内联函数编译，出现版本不一致甚至运行期崩溃（GCM tag 语义变化时更隐蔽）。建议开启编译器自动依赖并纳入头文件与库：

```make
DEPFLAGS = -MMD -MP
CFLAGS  += $(DEPFLAGS)

$(BIN) $(BIN)-static: sm4_gcm.c sm4_gcm.h $(TSINC)/openssl/evp.h
	...

-include $(BIN:.c=.d) $(BIN)-static.d
```
并在 `clean` 中一并 `rm -f *.d`（新增 `-MMD` 后当前的 `clean` 会残留依赖文件）。若工程有 `make distclean` 约定，也建议区分清理构建产物与 vendored Tongsuo 目录，避免误删 `../../Tongsuo` 的标准 make 输出。

### [cds-volume-plugin/sm4gcm/Makefile] medium/security
现码：
```
CFLAGS ?= -O2 -Wall -Wextra
```
加密辅助工具的编译加固缺失：`CFLAGS ?= -O2 -Wall -Wextra` 没有固定语言标准，也没有任何安全加固选项；同时用的是 `?=`，外部/CI 环境变量传入 `CFLAGS` 会**整体替换**掉这里的默认值（例如丢掉 `-Wall -Wextra`），而非追加。对这个处理密钥/明文的进程，建议：

```make
CFLAGS  = -O2 -std=c11 -Wall -Wextra -Werror -g
CFLAGS += -D_FORTIFY_SOURCE=2 -fstack-protector-strong -fPIE
LDFLAGS += -Wl,-z,relro,-z,now
```
即：用 `=` 或 `override` 保证关键标志不被外部覆盖，把用户可附加的部分放到 `EXTRA_CFLAGS`；`-Werror` 可避免警告（如 EVP 返回值被忽略）在 CI 中被无声吞掉。

### [cds-volume-plugin/sm4gcm/Makefile] low/maintainability
现码：
```
all: $(BIN)
```
可维护性/构建契约问题：`all` 只构建动态版本，而注释说明镜像烘焙实际使用的是 `$(BIN)-static`，也就是说默认 `make` 产物与部署产物不一致，且没有任何目标/校验能证明静态二进制可用（是否存在未解析符号、是否误链到系统 libcrypto）。建议显式提供入口并加入轻量自检：

```make
all: $(BIN) $(BIN)-static

check: $(BIN)-static
	ldd -r $(BIN)-static | grep -q 'not found' && { echo 'unresolved symbols' >&2; exit 1; } || true
	ldd $(BIN) | grep -q '$(TSLIB)' || { echo 'sm4_gcm not linked against vendored libcrypto' >&2; exit 1; }
```
另外 `PREFIX`/`TSINC`/`TSLIB` 展开后的路径含空格时会破坏 `-I$(TSINC)` 这类裸拼接，建议在 recipe 中统一加引号。

## sdk/pyproject.toml (6)
### [sdk/pyproject.toml] high/bug
现码：
```
stream = ["websocket-client>=1.6"]
readme = "README.md"
```
`readme` 被错误地放进了 `[project.optional-dependencies]`。该表的值必须是「extras 名 → 依赖列表」的映射（PEP 621 要求 array of strings），这里给出的是一个字符串 `"README.md"`，既不是合法 extras 也不是合法依赖：
- 使用 setuptools>=68 构建时，解析 metadata 会报错或把 `readme` 当作 extras 名去解析依赖，导致 `pip install .` / `python -m build` 失败；
- 即使侥幸通过，PyPI 上的 long_description 依然为空（因为 `[project]` 中并没有 `readme`），与注释里“核心 SDK 不依赖 stream”的意图无关。

应删除此行，并把 `readme = "README.md"` 移到 `[project]` 段（sdk/README.md 与 pyproject.toml 同级，路径正确）。

### [sdk/pyproject.toml] medium/maintainability
现码：
```
version = "0.3.0"
```
版本号存在两处独立来源：这里声明 `0.3.0`，而 `sdk/cds_sdk/__init__.py` 中 `__version__ = "0.2.0"`。发布后 `pip show cds-sdk` 显示 0.3.0，但 `cds_sdk.__version__` 仍是 0.2.0，排障与兼容性判断会被误导（也常被用于上报 client 版本头）。
建议只保留一个来源：例如在 pyproject 中使用 `dynamic = ["version"]` + `[tool.setuptools.dynamic] version = {attr = "cds_sdk.__version__"}`，或把 `__init__.py` 改为从 importlib.metadata 读取。

### [sdk/pyproject.toml] medium/maintainability
现码：
```
dependencies = ["httpx>=0.27.0"]
```
核心与可选依赖只有下限、没有上限：`httpx>=0.27.0` 与 `websocket-client>=1.6`。httpx 目前仍在 0.x 系列演进（0.x 之间允许破坏性变更，且未来 1.0 会再次调整 API），`client.py` 直接依赖 httpx 的 client/异常接口，一旦解析到不兼容的新版本，已安装用户会在运行时才崩掉，SDK 无法自愈。建议加上兼容性上限，例如 `httpx>=0.27,<1.0`、`websocket-client>=1.6,<2.0`。

### [sdk/pyproject.toml] low/maintainability
现码：
```
[project]
name = "cds-sdk"
version = "0.3.0"
```
`requires = ["setuptools>=68"]` 缺少上限，且 `readme` 字段位置错误（见下条）时，不同版本 setuptools 的行为差异会被放大。建议在 `README` 配置修正后同时固定构建后端上限（如 `setuptools>=68,<75`），或在 CI 中同时校验 sdist/wheel 的 METADATA 是否包含 `Description-Content-Type`，避免发布产物元数据不完整。

### [sdk/pyproject.toml] low/test
现码：
```
stream = ["websocket-client>=1.6"]
```
`stream = ["websocket-client>=1.6"]` 同样缺少上限（建议 `>=1.6,<2.0`）。另外，运行时的降级路径本身是正确的（`stream.py::_import_websocket` 捕获 ImportError 并抛出带安装指引的 `CDSError(501, ...)`），但目前测试目录只有 `tests/test_sdk_client.py`，缺少针对“未安装 websocket-client 时 `stream_exec` 抛 CDSError 而非 ImportError”的回归用例；建议补一条用 monkeypatch/mock 让 import 失败的测试，否则后续重构可能悄悄退化成裸 ImportError。

### [sdk/pyproject.toml] low/maintainability
现码：
```
[tool.setuptools.packages.find]
include = ["cds_sdk*"]
```
打包范围 `include = ["cds_sdk*"]` 与 `sdk/cds_sdk/` 目录命名一致，模块不会漏发；但该 SDK 对外宣称是 “typed sync client”，而包内没有 `py.typed` 标记文件，`[tool.setuptools.packages.find]` 默认只收集包目录、不会带上这类数据文件之外的标记（`py.typed` 需位于包内并在 sdist/wheel 中保留）。若希望下游 mypy/pyright 能使用类型信息，需要在 `cds_sdk/` 下新增空的 `py.typed` 文件；同时建议为 `packages.find` 显式指定 `where = ["."]`，避免后续目录结构调整（例如源码搬进 `src/`）时静默匹配不到任何包、产出空 wheel。
