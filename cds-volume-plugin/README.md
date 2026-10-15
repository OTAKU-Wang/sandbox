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

## 配置（env）

| 键 | 默认 | 说明 |
|---|---|---|
| `CDS_CUBE_VOLUME_PLUGIN_SOCKET` | `/run/cds-volume-plugin/cds.sock` | 插件 unix socket（root:cds 0600） |
| `CDS_SM4_AEAD_BACKEND` | `aes_fallback` | **生产必须 `tongsuo`**（spec §6.1/§2.9） |
| `CDS_VOLUME_PLUGIN_VOLUME_ROOT` | `/var/lib/cds/volumes` | 密文库目录 |
| `CDS_VOLUME_PLUGIN_SM4_HELPER` | `sm4_gcm` | helper 二进制路径 |
| `CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS` | `10000` | helper 子进程超时 |
| `CDS_VOLUME_PLUGIN_KMS_BASE_URL` | `http://127.0.0.1:8000` | CDS 控制面 KMS 基址 |
| `CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN` | — | 服务凭据（Bearer） |
| `CDS_VOLUME_PLUGIN_NODE_ID` | `local` | 节点标识（日志/审计） |
| `CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES` | `1073741824` | 单卷数据上限 |

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
