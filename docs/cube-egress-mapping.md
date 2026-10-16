# SS-10 CB-04 — 策略包字段 → CubeNetworkConfig 映射表

策略包（SS-02）→ `CubeNetworkConfig` 的完整字段映射，含凭据注入映射与
优先级语义。编译入口：`app/services/policy_compiler.py:499`
（`compile_cube_network`，类方法在 `:130`）。

Proto 字段名/类型均于 2026-10-13 用 venv python 从 CB-00 桩
（`app/_stubs/cube/cubebox/v1/cubebox_pb2.py`，上游 9b75d33）**内省核实**，
非猜测。

## 1. 消息级映射（CubeNetworkConfig）

| CubeNetworkConfig 字段 | proto 类型 | 策略包来源字段 | 映射规则 | 编译位置 |
|---|---|---|---|---|
| `allow_internet_access` (num=1, bool, oneof presence) | bool | **无来源 — 恒 `false`** | 铁律：default-deny 基线硬编码，永不读输入；输入试图置 `true` → `CDSError(POLICY_COMPILE_ERROR)` 拒绝（fail-closed） | `policy_compiler.py:247` |
| `allow_out` (num=2, repeated string) | []string | `allowed_ips`（ip/cidr）+ `allowed_domains`（dns） | 直通透传：ip/cidr 条目在前、dns 条目在后，各自保序；dns 条目经 CubeVS DNS A 记录学习放行（spec §2.8）。白名单字段实载于 CDS 网络托管模型 `app/models/network_policy.py:33-35`（`allowed_ips` / `allowed_domains`） | `policy_compiler.py:176-178` |
| `deny_out` (num=3, repeated string) | []string | `deny_out`（`NetworkPolicyConfig.deny_out`，`app/services/network_policy.py:40`） | 显式拒绝条目直传；缺省 `[]` | `policy_compiler.py:178` |
| `rules` (num=4, repeated EgressRule) | [EgressRule] | `rules`（合约级 L7 规则，`contracts.terms["egress_rules"]`） | 见 §2 规则级映射；**顺序 = 策略包规则顺序**（first-match-wins 优先级 = 列表序，编译期稳定保序，无重排） | `policy_compiler.py:184-222` |

### 全量态不变式（spec §2.8：Create/Update 传全量；omitted field = cleared upstream）

- 常量 `CUBE_NETWORK_FULL_STATE_FIELDS = ("allow_internet_access", "allow_out", "deny_out", "rules")` — `policy_compiler.py:128`
- 每次编译**显式赋齐**四个字段（空列表 = 明确清空，不是省略）
- 自检 `_check_cube_network_full_state`（`policy_compiler.py:280`）在每次输出前断言：
  1. 桩无字段漂移（四字段仍在 `CubeNetworkConfig.DESCRIPTOR` 中，否则提示按 CB-00 钉版重新推导）
  2. `allow_internet_access` **显式设置**（presence 字段）
  3. 值为 `False`（default-deny 基线永不被放宽）
- 公开校验入口 `verify_cube_network_full_state` — `policy_compiler.py:509`
- **无增量路径**：规则删除后的新编译直接不再含该规则（测试
  `test_full_state_rule_removal_no_incremental` 覆盖）；同一输入两次编译
  字节级一致（`SerializeToString` 相等，`test_full_state_invariant_identical_recompiles`）

## 2. 规则级映射（rules[i] → EgressRule）

输入形状：`{name?, host|domain, path?, method?|methods?, allow?=true, audit?}`

| EgressRule 字段 | proto 类型 | 输入字段 | 映射规则 |
|---|---|---|---|
| `name` (num=1, string) | string | `name` | 缺省 `rule-<i+1>`（1-based 包内序号） |
| `match` (num=2, EgressRuleMatch, presence) | msg | host/domain, path, method(s) | 至少一项约束，否则拒绝编译（空 match 会放行一切 — fail-closed） |
| `action` (num=3, EgressRuleAction, presence) | msg | `allow`, `audit` | `allow` 缺省 `true`；`audit` 可选透传 |

| EgressRuleMatch 字段 | proto 类型 | 输入字段 | 映射规则 |
|---|---|---|---|
| `sni` (num=1, string, presence) | string | — | 不生成（CDS 用 `host`，二者在上游等价维度；§2.8 匹配维度 sni/host/method/path） |
| `host` (num=3, string, presence) | string | `host` 或 `domain` | 域名字面量；上游 L7 按 SNI/Host 匹配 |
| `method` (num=4, **repeated** string) | []string | `methods`（list）或 `method`（str→[str]） | HTTP 方法集合 |
| `path` (num=5, string, presence) | string | `path` | 路径前缀/字面量（语义随节点 `rule_matches` 实现） |
| `scheme` (num=7, string, presence) | string | — | 不生成 |
| `port` (num=8, int32, presence) | int32 | — | 不生成（端口约束走 `allowed_ports`/allow_out 维度，见 L0 等价） |

**AND 匹配**：一条规则内 `host`/`path`/`method` 全部满足才命中（上游
`CubeEgress/lua/access_phase.lua` `rule_matches`，2026-10-13 源码核实）。
**优先级**：`rules[]` 列表序 = first-match-wins 优先级 = 策略包规则顺序
（编译期顺序保持，稳定；无排序步骤 — 测试
`test_rules_priority_order_preserved` 用 4 条规则断言）。

## 3. 凭据注入映射（app_credentials → action.inject，spec §2.8 凭据注入下沉）

| EgressRuleInject 字段 | proto 类型 | 来源 | 映射规则 |
|---|---|---|---|
| `header` (num=1, string) | string | 凭据条目的 `header` | 缺省 `"Authorization"`（`CUBE_INJECT_DEFAULT_HEADER`，`policy_compiler.py:122`） |
| `secret` (num=2, string) | string | 凭据条目的 `secret`（控制面持有的明文值，见下） | 必填非空；**空 secret 绝不发出**（跳过并披露，禁伪造） |
| `format` (num=3, string, presence) | string | 凭据条目的 `format` | 缺省 `"Bearer ${SECRET}"`（`CUBE_INJECT_DEFAULT_FORMAT`，`policy_compiler.py:123`） |

### `format` 语义核实结果（spec CB-04 步骤 2 "实施时核实"）

- proto 层：`format` 是**普通字符串模板**（num=3, string, oneof presence）——
  无枚举、无内建占位符定义。
- 节点层（上游核实，`CubeEgress/lua/access_phase.lua:168-195`
  `render_inject`）：模板中**字面占位符是 `${SECRET}`**，由节点 egress 替换为
  原始 secret 值后写 header；**不含 `${SECRET}` 的 format 按字面量原样发送**
  （上游记 WARN 并写入审计 detail）。
- 因此 spec 样例的 `Bearer {{secret}}` **不匹配**上游占位符语法，本编译器
  输出 `Bearer ${SECRET}`（adapted，如实记录）。
- 挂载规则：凭据挂到 `match.host == cred.domain` 的**第一条**规则；无匹配规则
  则在其后**追加**合成 allow 规则 `inject-<credential_id>`（合成规则恒在合约
  规则之后）。

### 凭据取值来源与诚实降级（禁伪造）

- 托管记录：`app_credentials`（`app/models/app_credential.py:34`）只存
  `app_secret_hash`（SM3）——**DB 中无明文**，明文只在创建时一次性返回。
- 因此注入值来源 = `contracts.terms["credentials"]`（合约签署时控制面持有的
  明文值：`{credential_id|app_id, secret, domain, header?, format?}`），与
  `app_credentials` 的有效绑定（status=active、未过期）按
  `credential_id`/`app_id` 联接。
- 无法解析明文的凭据 → **跳过该 inject 条目**，`logger.warning` + 审计 detail
  `skipped_credentials` 披露（不静默、不伪造）。组装见
  `app/services/cube_egress.py:105`（`load_policy_package`）。
- 已知缺口（后续）：app_credentials 的 KMS 封装明文托管落地后，
  `load_policy_package` 的取值来源可扩为"terms.credentials ∪ KMS 托管"。

## 4. 热换（hot-swap）流程（spec §2.8 Update 全量替换）

**挂钩的现有合约更新流程**：网络策略（重）下发链路
`network_policy_engine.remove_policy() + create_policy()` —— 现用于
`PUT /sessions/{id}/network-policy`、`PATCH /network-policies/{id}`（hot
reload enforcement）、`DELETE /network-policies/{id}`、会话创建/终止/清理。

- `create_policy`（`app/services/network_policy.py:319`）：l2c 分支
  （`:336` 起）在 netns/iptables **之前**早退 —— 记审计
  （`level:l2c`，`enforced_by=cube_dataplane`），调
  `cube_egress.apply_network_policy_to_cube(session_id, config)`
  （`app/services/cube_egress.py:186`）：加载策略包 → 全量编译 →
  `Update(CubeNetworkConfig)`（sandboxID + 全量 cube_network_config，
  requestID 由 `cube_node_client` 自动戳）。**推送失败 → 抛出**（fail-closed：
  API 面出错而非假称已更新）。
- `remove_policy`（`app/services/network_policy.py:432`）：l2c 分支
  （`:443` 起）—— 无宿主工件可删；推送**全量 default-deny**
  （`default_deny=True`：policy removed ⇒ deny all，零信任）。best-effort：
  终止/清理链不可被阻断，失败记 `security_event` 审计 + error 日志，不抛。
- 非 l2c 会话：`apply_network_policy_to_cube` 返回
  `{"applied": False, "reason": "not_l2c"}` no-op（`:199-207`）；L0/L3
  宿主 netns/iptables 路径零改动（回归测试
  `test_l0_path_byte_identical_iptables_ruleset` 断言命令集逐条一致）。
- 会话创建时（Create RPC）：适配器 `_network_config(session_id)`
  （`app/services/cube_runtime_adapter.py:404`，CB-01 预留的
  调用点 `:380`）加载策略包全量编译；加载/编译失败 → default-deny 配置
  + 警告（honest degradation，不中断 provision）。API 层随后的
  `create_policy(deny_all)` 以同一全量态再断言一次。
- 幂等：`Update` 全量替换，`SANDBOX_NOT_FOUND` → no-op + 审计 note。

## 5. l2c 分支（spec §3.5）

- 判定：`NetworkPolicyEngine._session_is_l2c`
  （`app/services/network_policy.py:290`）— `sandbox_level == "l2c"`；
  DB 异常/非 UUID → 宿主路径默认（告警）。
- l2c 会话**永不**执行 netns/iptables（`create_policy` 早退 `:336-371`、
  `remove_policy` 早退 `:443-471`）；零副作用由
  `test_l2c_create_policy_zero_netns_iptables_side_effects`（monkeypatch
  计数器）断言。

## 6. CA bake（spec §4 CB-04 步骤 4）

- 场景镜像构建链：`Dockerfile.cube-sandbox`（仓库既有 Dockerfile 体系新增
  入口）+ `scripts/build-cube-sandbox-image.sh`。
- build-arg：`CUBE_EGRESS_CA`（缺省 `./cube-egress-ca.crt` = 构建脚本拷贝的
  固定名）；CA 工件 = CB-02 节点 bring-up 产出的节点文件
  `/etc/cube/ca/cube-root-ca.crt`（上游 `CubeEgress/nginx.conf`
  `ca_cert_path`，2026-10-13 核实）的 CDS 侧拷贝。
- bake：`COPY` 进 `/usr/local/share/ca-certificates/` + `update-ca-certificates`
  + `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` 指向系统信任库。COPY 缺失 = build
  期硬失败（不静默）。
- **CB-05 模板 probe 挂钩函数（留名接口）**：
  `app.services.cube_egress.check_cube_egress_ca_baked(ca_path=None)`
  （`app/services/cube_egress.py:57`）— 解析顺序：显式参数 >
  `CDS_CUBE_EGRESS_CA` > 节点缺省路径；缺失/空/不可读 → `CDSError`
  （诚实错误，模板构建必须失败）；成功返回
  `{ca_path, sha256, size_bytes}`。构建脚本 pre-flight 与 CB-05 probe
  调用**同一函数**。
- 新配置键：`CDS_CUBE_EGRESS_CA`（`app/core/config.py` CUBE 块；同步于
  `.env.example`、`docker-compose.yml`、`docker-compose.prod.yml`、
  `helm/cds/values.yaml`）。

## 7. 测试

`tests/test_policy_compile_cube.py`（16 项）：proto 字段级断言
（default-deny/allow_out/inject/4 规则优先级保序）、全量态不变式
（同输入字节级一致 + 规则删除无增量）、l2c 零 netns/iptables 副作用
（monkeypatch 计数器）、L0 回归（iptables/netns 命令集逐条快照比对）、
热换 Update 全量推送（sandboxID + 全量 config + 非 l2c no-op +
default-deny 推送）、CA probe（缺失/空/成功三态）。
