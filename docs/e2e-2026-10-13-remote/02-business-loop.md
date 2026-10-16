# 02 — S2 平台 API key 业务闭环测试（SS-14 要求 2）

- 时间窗口: 2026-10-13（远程 100.112.5.x:22022，脚本 `deploy/e2e-remote/e2e_business_loop.py`）
- 判据: 使用平台 API key（SS-12）完成业务闭环：资源 → 产品 → 契约 → 会话 → 执行 → 审计；scoped 权限 key 的 scope 负例；human-only 面拒 key。
- 证据: 每步 JSON 记录（step/status/status_code/note/excerpt）落 `e2e_business_loop_evidence.json`（远程 /root/，关键摘录回填本文档）。

## 1. 测试面（脚本步骤清单）

| 组 | 步骤 | 契约 |
|---|---|---|
| 基础 | health | /health 200 |
| 引导 | admin.login | 既有 admin 登录（bootstrap 在 01 部署轮完成） |
| SS-12 | apikey.create.full / .readonly | 201，裸 key 仅一次返回（cds_ak_ 前缀，len=50） |
| SS-12 | apikey.auth.meta | key 主体在 meta 面可认证（200） |
| SS-12 负例 | apikey.scope.write_denied | readonly key 写端点 → 403 API_KEY_SCOPE_DENIED |
| SS-12 负例 | apikey.scope.human_only_denied | full key 打 /api-keys → 403（human-only 门） |
| SS-12 负例 | apikey.scope.users_denied | full key 打 /users/options → 403（human-only 面） |
| 闭环 | loop.resource.upload | key 主体上传数据资源（multipart，角色门按签发人裁决——D-e2e-09） |
| 闭环 | loop.product.create | key 主体建产品（关联资源）→ 201，owner=签发人 |
| 闭环 | loop.product.submit / .approve / .publish | 提交 → 人类 admin 批准 → 发布（发布需关联资源，否则 400） |
| 闭环 | loop.session.create | l2c 会话创建：有节点 → 201；无节点 → **诚实 400 RUNTIME_UNAVAILABLE（视为 PASS，执行腿归本机节点轨 RT-07）** |
| 闭环（节点轨） | loop.file.upload / execute / download.gated / usage / proof-bundle / terminate | 上传/执行/输出治理门禁下载/用量/证据包/终止（仅 201 分支执行） |
| 审计 | audit.api_key_created | 人类 admin 审计面含 api_key.created |
| SS-12 | apikey.list.no_bare_key | list 仅 key_prefix（无裸 key 字段、无裸 key 串） |

## 2. 执行记录

### Round A（API5 镜像，deploy#13）
- 结果: **12/15 PASS**。
- PASS: health / admin.login / apikey.create.full(prefix=cds_ak_KuDF len=50) / apikey.create.readonly / apikey.auth.meta / apikey.scope.write_denied(403) / apikey.scope.human_only_denied(403) / apikey.scope.users_denied(403) / **loop.product.create(201，D-e2e-09 生效：key 以签发人身份建产品)** / loop.product.submit / loop.product.approve（人类 admin）/ audit.api_key_created。
- FAIL 三处定位:
  1. `loop.product.publish` 400 "Product must be linked to a data resource before publishing" —— 脚本缺资源环节（非产品缺陷）。
  2. `loop.session.create` 400 "Data product is not published" —— ①级联。
  3. `apikey.list.no_bare_key` —— 脚本断言误报（`"key" in json.dumps(...)` 命中 `key_prefix` 子串；实测 list 响应仅 key_prefix，服务端干净）。
- 真缺陷（本轮暴露）: `require_roles` 仍 user-only 解析 → key 主体在角色门路由（data-resources upload 的 DATA_PROVIDER/ADMIN 门）401 —— **D-e2e-09 残面**，已修（require_roles 经 get_business_user，key 按签发人角色裁决）。

### Round B（API6 镜像首跑，deploy#14）
- 结果: **S2 8/9**。PASS 组新增：scope 负例三连 + 资源环节前的全部门面；`loop.resource.upload` 500 "HSM unavailable — cannot generate KEK"——暴露 **D-e2e-11**（部署链缺 Vault transit 引擎）。
- 同轮 S3 8/9（failclosed.l2c_no_node 得 400 但为"产品未发布"——探针产品未走发布生命周期，脚本修订：资源→产品→提交/批准/发布全链严格化）；S4 5P/3S/2F（buyer 注册 `.local` TLD 被拒 → 修 `@cds-e2e.cn`；T6 compliance 路径错 → `GET /reports/quick`）。

### Round C（API6 + 部署脚本 D-e2e-11 修复，deploy#15/16/17 三连）
- 部署链本身两败（vault CLI `-address` 根 flag 误用 → `VAULT_ADDR` 环境变量形态；token 传递）后 transit 引擎启用成功。
- S2 仍 8/9（同一 HSM 500）→ api 日志定讫 **D-e2e-12**（hvac `create_key(type=)` 参数名错，真应为 `key_type=`；本地软件 HSM 结构性失明）；S3 探针 PASS 被证实为假阳性（上传 500→rid=None→发布 400 被吞）→ 探针严格化（每腿显式检查）；S4 setup.buyers 转 PASS（TLD 修复生效），T4 审计完整性 PASS。
- 代码修复（D-e2e-12）→ API7 镜像（hvac 内省签名比对 + KMS/HSM 套件 30/30 绿 + 3.12 冒烟过）。

### Round E（API8 镜像：D-e2e-13 修复）——全绿
- 结果: **16/16 PASS，0 failed**（证据 /root/e2e_business_loop_evidence.json，摘录见 01-deployment.md 证据文件清单）。
- 关键腿: `loop.resource.upload` 200（真 Vault transit KEK 生成+wrap/unwrap 全链——D-e2e-12/13 最终验收）→ `product.create` 201（key 主体签发人身份，D-e2e-09 契约）→ `submit/approve/publish` 全 2xx → `session.create` 诚实 fail-closed 400（无节点远程，执行腿归节点轨 RT-07）→ `audit.api_key_created`（审计留痕）→ `no_bare_key`（列表仅 key_prefix）。
- scope 负例三连全 PASS（readonly 写拒 403 / 人类专属面拒 / users 面拒）。
- **S2 判定: PASS**（平台 API key 业务闭环，无节点形态全绿；活执行腿待节点轨）。
- 脚本修订汇总: 资源上传步骤 + 产品关联 resource_id + session 级别 l2c + no_bare_key 精确断言（S2）；S3 探针严格化；S4 TLD + T6 路径 + 上传显式检查。

## 3. 节点轨执行腿（RT-07，2026-10-14 本机 KVM，已回填）

同一脚本对节点轨（http://127.0.0.1:8000 + 本机 Cube 节点）执行，**22/22 PASS，0 failed**（证据 evidence-node/business_loop_node.json）：

- `session.create` **201（level=l2c status=running）**——有节点分支真实创建活会话（远程轨为诚实 400 分支，两分支均实证）。
- 执行腿全绿: `loop.file.upload` → `loop.execute`（python print via sandbox，exec 文件中转契约 D-e2e-19）→ `loop.file.download.gated`（**X-CDS-Output-Review='passed; findings=0'**，l2c 下载 T5 审查分支 D-e2e-20）→ `loop.usage`（l2c 分支：数据卷字节 + VM 目录统计，D-e2e-20）→ `loop.proof_bundle`（evidence_hash + bundle_meta）→ `loop.session.terminate`。
- SS-12 面不变：scope 负例三连 + no_bare_key + audit.api_key_created 全 PASS（节点轨同样成立）。
- **S2 判定: PASS（两形态全绿）**——无节点形态（16/16，诚实 fail-closed）+ 节点轨形态（22/22，真实执行腿）。

## 3b. 缺陷关联
- D-e2e-09（key 主体业务面 401，含 require_roles 残面）→ 见 04。
- 脚本侧修订（资源环节/L0→l2c/no_bare_key）为测试面问题，非产品缺陷，随 Round B 复验。

## 4. 结论
- 无节点形态: 16/16 PASS（Round E，API8）。
- 节点轨形态: 22/22 PASS（RT-07，本机 KVM）。
- 平台 API key 业务闭环在两条形态下全部可达、全部留痕，无 SKIP 遗留。
