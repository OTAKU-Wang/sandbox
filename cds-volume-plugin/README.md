# cds-volume-plugin — CDS 加密卷插件 (SS-10 CB-03)

Cubelet VolumePlugin 协议的 CDS 加密数据卷插件：数据进域侧 at-rest **SM4-GCM**
加密（libtongsuo 链接 C helper，spec §2.9；`enc` 类 CLI 一律禁用于 AEAD）。

- 协议：`VolumePluginService`(Attach/Detach) + `VolumeControllerService`(Create/Destroy)，
  proto 钉自上游 `CubeSandbox/pkgs/proto/services/volumeplugin/v1/volumeplugin.proto`
  （上游仓库只读；stub 生成物在 `gen/`）。
- CDS 扩展：`cdsvolume.v1.CDSVolumeExtension`（PutVolumeData/GetVolumeInfo，
  自有 proto `proto/cdsvolume/v1/cdsvolume.proto`——非上游协议）。
- 铁律：fail-closed / 禁伪造。解密失败 = 错误，绝无明文回退；
  `CDS_SM4_AEAD_BACKEND=tongsuo` 时 helper 缺失或自检失败 = 启动失败。

## 构建

```bash
# 1) vendored Tongsuo（标准 make 产物，不改源码）:
cd ../Tongsuo && ./Configure --prefix=$PWD/../cds-volume-plugin/tongsuo-build no-tests \
  && make -j$(nproc) && make install_sw
# 2) 本地构建（helper + 插件二进制 → bin/）:
make local
# 3) 镜像（在仓库根）:
docker build -f cds-volume-plugin/Dockerfile -t cds-volume-plugin:dev .
```

### 基础镜像钉定（A205④）

Dockerfile 四个 `FROM` 全部钉到不可变 digest（tag 保留只为可读，真正拉取的是 digest）。
当前钉定值、取的时间与取法：

| 基础镜像（钉定引用） | 取到时间 | 阶段 |
|---|---|---|
| `golang:1.25-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437` | 2026-10-20 18:07 +08:00 | tongsuo / helper / plugin |
| `debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587` | 2026-10-20 18:07 +08:00 | 运行期镜像 |

取法（本机直连 registry，无代理；两个值都各自以 digest 为 ref 回验过一次清单请求返回 `200`）：

```bash
# 1) 取 pull token
TOK=$(curl -sS "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/golang:pull" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
# 2) 读 Docker-Content-Digest（把 golang/1.25-bookworm 换成 library/<repo>/<tag> 即可复用）
curl -sSI -H "Authorization: Bearer $TOK" \
  -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json' \
  https://registry-1.docker.io/v2/library/golang/manifests/1.25-bookworm | grep -i docker-content-digest
# 3) 以 digest 为 ref 再请求一次，确认 200 且回显的 Docker-Content-Digest 一致
```

**`ARG GO_VER` 与 digest 的口径（二者择一，现已取后者）**：旧 Dockerfile 用
`ARG GO_VER=1.25` + `FROM golang:${GO_VER}-bookworm` 让构建期浮动解析 tag；digest 是对
**某一刻某个 tag 清单**的指纹，若继续允许 `--build-arg GO_VER=…` 改写镜像名而 digest 原地不动，
钉定就变成假的（拉到的字节与记录的指纹无关）。因此 `ARG GO_VER` 已删除，**digest 是唯一点**；
要换 Go 工具链，先按上面的取法重取 digest，再把三条 `golang:` 行一起改（`a205c_digest_pin_test.go`
会在三者不一致时红）。同理，上游正常重推浮动 tag 不会让门禁红：那条断言只钉「每个 FROM 都带
`@sha256:` 且 digest 为 64 位十六进制」的**形状**，绝不钉具体值。

## 配置（env）

| 键 | 默认 | 说明 |
|---|---|---|
| `CDS_CUBE_VOLUME_PLUGIN_SOCKET` | `/run/cds-volume-plugin/cds.sock` | 插件 unix socket（root:cds 0600） |
| `CDS_SM4_AEAD_BACKEND` | `aes_fallback` | **生产必须 `tongsuo`**（spec §6.1/§2.9） |
| `CDS_VOLUME_PLUGIN_VOLUME_ROOT` | `/var/lib/cds/volumes` | 密文库目录 |
| `CDS_VOLUME_PLUGIN_SM4_HELPER` | `sm4_gcm`（**裸名**） | helper 二进制路径：生产**必须给绝对路径**（A175，见下），镜像内固定 `/usr/local/bin/cds-sm4-gcm` |
| `CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS` | `10000` | helper 子进程超时 |
| `CDS_VOLUME_PLUGIN_KMS_BASE_URL` | `http://127.0.0.1:8000` | CDS 控制面 KMS 基址（非回环必须 `https`，A179） |
| `CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN` | — | 服务凭据（Bearer） |
| `CDS_VOLUME_PLUGIN_KMS_CA_FILE` | — | 控制面 CA（PEM，追加到系统根证书） |
| `CDS_VOLUME_PLUGIN_KMS_CLIENT_CERT` / `CDS_VOLUME_PLUGIN_KMS_CLIENT_KEY` | — | 本节点 mTLS 客户端证书对（两者必须同时给） |
| `CDS_VOLUME_PLUGIN_KMS_ALLOW_INSECURE` | `false` | 显式接受**非回环**明文 http（启动 WARNING） |
| `CDS_VOLUME_PLUGIN_KMS_INSECURE_SKIP_VERIFY` | `false` | 显式跳过服务端证书校验（仅测试网段，启动 WARNING） |
| `CDS_VOLUME_PLUGIN_NODE_ID` | `local` | 节点标识（日志/审计） |
| `CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES` | `1073741824` | 单卷数据上限 |
| `CDS_VOLUME_PLUGIN_ALLOW_NON_ROOT` | `false` | 显式允许以非 root 身份运行（A205①）：默认非 root 直接拒启动（loop mount / socket chown 需要特权），开后打 WARNING |

## 国密 helper 路径（A175）

helper 的 stdin 里就是**明文 DEK**（所以上 argv/env），而 `CDS_VOLUME_PLUGIN_SM4_HELPER`
的**代码默认值是个裸名** `sm4_gcm` —— `exec.Command` 会按宿主 `PATH` 解析它。节点 PATH
里有非特权用户可写的目录很常见：那个目录里的同名二进制会被以插件权限执行，递给它的是
DEK，返回什么密文/tag 也由它定 ⇒ 卷加密与密钥托管同时失效。

- **镜像内不存在该问题**：`Dockerfile` 把变量钉为绝对路径 `/usr/local/bin/cds-sm4-gcm`，
  并在构建期 `test -x` 该路径（`a204_build_gates_test.go` 钉住该口径）。
- **主机/cubelet 直接拉起插件（非镜像）时，运维必须显式设绝对路径**，例如
  `CDS_VOLUME_PLUGIN_SM4_HELPER=/usr/local/bin/cds-sm4-gcm`；全仓目前**只有镜像**会自动设
  这个变量（`deploy/cube-node/install.sh` 不写它）。
- **已知缺口（本轮未修，`Config.Validate()` 属已收口面）**：代码层尚未在启动时拒相对名 /
  检查文件属主与可写位。精确改点：`config.go` `Validate()` 内
  `if c.Backend == "tongsuo"` 分支加 `filepath.IsAbs(c.HelperPath)` 否则拒，并要求目标
  `uid==0`、非 group/world-writable（fail-closed，不降级为 WARNING）。

## 加密姿态 `crypto_posture`（启动机器可读披露）

`serve` / `selftest` 启起时输出一行可直接 grep 的记录，字段名与 `CryptoPosture` 结构体
的 json tag 一致（`a175_crypto_posture_doc_test.go` 用反射钉住，改名即红）：

```
crypto_posture={"aead_backend":"aes-128-gcm(fallback,non-GM)","cipher":"…","gm_compliant":false,
"degraded":true,"disclosure":"…NOT the GM/T SM4-GCM…","production_requires":"CDS_SM4_AEAD_BACKEND=tongsuo"}
```

| 字段 | 含义 |
|---|---|
| `aead_backend` | 当前 at-rest 后端（`tongsuo` / `aes_fallback`） |
| `cipher` | 实际 AEAD 算法名（含 `(fallback,non-GM)` 后缀时为降级） |
| `gm_compliant` | 是否 GM/T SM4-GCM |
| `degraded` | 是否处于降级（铁律：降级必须自己说出来） |
| `disclosure` | 人读降级说明 |
| `production_requires` | 生产必须设置的变量（仅降级时出现） |

部署门禁应读 `gm_compliant`/`degraded` **字段**而不是抓 WARNING 文本：
`CDS_SM4_AEAD_BACKEND` 默认是 `aes_fallback`，未显式设为 `tongsuo` 的节点不得上生产。

## KMS 传输（A179，2026-10-18 OCR 轮）

KMS 腿传的是**明文 DEK hex + 服务 Bearer token**，因此代码层强制：非 `https`
一律拒启动（fail-closed），除非目标主机是**回环 IP 字面量**（开发/测试，含本
表默认值）或运维显式设置 `CDS_VOLUME_PLUGIN_KMS_ALLOW_INSECURE=1` /
`serve --allow-insecure-kms`。两种降级都会打印启动 WARNING，并由
`TransportMode()` 如实报告（`https+mtls(ca=…)` / `http-plaintext(…)`）——
绝不把明文说成 mTLS。重定向一律拒绝（307/308 会带着 DEK 请求体重放）。

## KMS 契约（控制面待实现 — FLAG）

spec CB-03 指定语义（`distribute_key` 复用、session-scoped、TTL），但未给定
HTTP 路径。本插件按以下**文档化路径**实现客户端，控制面 `app/api/kms.py` 尚无
对应路由（属 CB-01 范围，需服务级鉴权而非用户 JWT）：

- `POST   /api/v1/kms/volume-keys`                body {volume_id, name, dek_hex} → {key_id}
- `POST   /api/v1/kms/volume-keys/{ref}/distribute`  body {session_id} → {key_hex}
- `DELETE /api/v1/kms/volume-keys/{ref}`          → 204

单元测试/集成测试以 mock 控制面验证（`plugin_test.go` mockKMS、
`tests/test_cds_volume_plugin.py` 内嵌 HTTP mock）。

## 磁盘布局

```
<VolumeRoot>/<volume_id>/
  manifest.json  {volume_id, name, cipher, dek_ref, aad, created_at,
                  data_size_bytes, has_data}
  data.ct        密文
  data.nonce     12B GCM nonce
  data.tag       16B GCM tag
```

Attach 期间明文库（宿主侧，virtiofs RO 入 VM）：`<volume_base_dir>/cds-<volume_id>/data`
（0400；Detach ref_count=0 时 shred）。AAD = 卷名/label。

## private_data 流转（spec §2.4）

`Create` 返回 `{"session_dek_ref": "<wrapped key id>"}`（≤1024B）。CDS 控制面
持久化并在沙箱创建请求的 `plugin-volume-sources` 注解携带：
`[{"name":"cds-data-<sid>","driver":"cds-encrypted","private_data":"<json>"}]`。
`VolumeSource.plugin_volume{driver}` 回退路径**不携带** private_data ——
插件对空 private_data 的 Attach 直接拒绝（fail-closed）。

## CLI（无 gRPC 依赖，供测试/运维）

```
cds-volume-plugin selftest
cds-volume-plugin ctl create --volume-id V --name N
cds-volume-plugin ctl put    --volume-id V --data-file F [--aad A]
cds-volume-plugin ctl attach --volume-id V --base-dir B --sandbox-id S --private-data P [--ref-count N]
cds-volume-plugin ctl detach --volume-id V --base-dir B --ref-count 0
cds-volume-plugin ctl destroy --volume-id V
cds-volume-plugin ctl info   --volume-id V
cds-volume-plugin parse-annotation '[{"name":...,"driver":"cds-encrypted","private_data":"..."}]'
```
