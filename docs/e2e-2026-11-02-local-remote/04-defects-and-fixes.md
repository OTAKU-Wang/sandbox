# E2E 缺陷账本 — 2026-11-02（本地 docker 全栈 + 远端 SS-14 宿主机复验）

范围：用户指令「进行 e2e 测试」。双轨：①本地 docker-compose 全栈（py3.12 生产镜像 +
真 PG16/Redis/ClickHouse/MinIO/OPA/Vault，镜像口径权威）；②远端 SS-14 宿主机复验。
所有 SKIP/降级均如实标注，不粉饰。git 提交链：08dc702 → 6e3814f → 7c572e5 → 023e902 → cm4。

## D-e2e-20【产品缺陷·高】py3.12 上 A96 投递后 communicate 必炸（部署态专属盲区）
- 现象：本地全栈 dev-sandbox L3 执行 `exit_code=-1`，output=`I/O operation on closed file.`
  ——生产镜像（py3.12）上每次沙箱执行 100% 挂。
- 根因：`GuestSecrets.deliver()` 关闭 `proc.stdin` 后仍挂在 Popen 上；py3.12
  `subprocess._communicate` 对挂着的 stdin 无条件 `flush()` → ValueError。py3.14 该
  flush 被 guard 吞掉，且测试装置进程内不跑镜像 = 双重盲区（本机 3.14 结构上看不见）。
- 修复：`_run_guest_argv` 与 firecracker 仿真腿 deliver 后置 `proc.stdin = None`
  （所有权移交显式化，3.12/3.14 双稳）；6e3814f。
- 回归针：`tests/test_e2e_20261102_guest_stdin_py312.py`（假 Popen 复刻 3.12 语义走
  真函数 + deliver→communicate 站点 AST 结构针），修复缺席即红。
- 验证：热换后执行 `print('EXEC_42')` exit=0、输出回读、terminate 200。

## D-e2e-21【产品缺陷·中】执行面吞栈（诊断面缺失）
- 现象：D-e2e-20 现场 0 日志——L0/L3 except 把带 exception 的返回值写进 output 但不
  `logger.exception`，多花一整轮定位。
- 修复：两站点补 `logger.exception`（同 6e3814f）。

## D-e2e-22【部署配置缺陷】dev compose 在 Ubuntu 宿主跑不了 bwrap（L3 开箱即坏）
- 现象：api 容器内 bwrap `Failed to make / slave: Permission denied`，尽管
  cap SYS_ADMIN+NET_ADMIN、seccomp unconfined 齐全（文档化三件套不完整）。
- 定位：A/B 隔离矩阵实证 = docker-default AppArmor 档拦 mount propagation；
  `security_opt: apparmor=unconfined` 一行即愈。
- 修复：docker-compose.yml cds-api 增该宽免 + 证据注释；7c572e5。prod compose/helm
  无此段不受影响（L3 本就不在 prod 面）。

## D-e2e-23【构建缺陷】Dockerfile.api DEBIAN_MIRROR 对 http:// 源静默失效
- 现象：受限网络构建 apt 挂死 DNS 超时。trixie 基础镜像 debian.sources 用 http://，
  sed 只替换 https:// ⇒ 镜像参数完全没生效。
- 修复：`-E 's|https?://deb.debian.org|${DEBIAN_MIRROR}|g'`；7c572e5。

## D-e2e-24【产品缺陷·配置健壮性】照抄 .env.example 让 Settings 71 错起爆
- 现象：仓根存在 `.env`（照抄 example，含 compose 域键）时，本机整个 pytest 收集期
  炸 + 文档化本地开发路径自断。
- 根因：pyd-settings dotenv 源把文件全部键以小写原名交上（env_prefix 只管环境变量
  源），v2 无 extra 时默认 forbid ⇒ ValidationError 从第一个字段起爆，
  `validate_security_config` 的 fail-closed 面永远到不了。
- 修复：`settings_customise_sources` 给 dotenv 源套「命中模型字段才放行」过滤器
  （环境变量侧语义零改动）；08dc702。
- 如实在册（同针）：`CDS_*` 环境变量拼错键名=静默忽略——上游 pyd-settings 语义，
  本次未改变；若要 typo-fail-loud 需另立工作项（先清点 helm/k8s 注入键）。
- 回归针：`tests/test_dotenv_compose_keys.py` 双条。

## D-e2e-25【架构级流程缺口】真实 PG 迁移链从未演练过——0030 起爆
- 现象：远端 `alembic upgrade head`（0026→head）在 0029→0030 抛
  `value too long for type character varying(32)`。alembic 版本表写死 String(32)，
  0030/0033 的 ID 各 34 字符。
- 盲区结构：本机/CI 链跑 SQLite（VARCHAR 不执法度）；远端 PG 上次过链停在 0026
  （短 ID 时代）。**生产 PG 升级路径 10-20 之后从未真正演练**——流程缺口比代码缺陷
  更值得警惕：ci/known-failures 基线与套件全绿都没覆盖它。
- 修复：0030 upgrade() 首行 PG 守卫加宽版本表→VARCHAR(128)（幂等；stamp 在
  upgrade() 之后，新 revision 结构上救不了自己；SQLite 侧 dialect 守卫完全短路，
  不违反「不改历史」）；downgrade 不缩回。023e902。
- 验证：本地真 PG16 全新库 0000→0034 全绿；远端 0026→0034 全绿（生产复验）。
- 回归针：`tests/test_alembic_version_id_width.py` 三条静态针。
- 流程建议（未落地，待拍板）：CI 增加一次真 PG `alembic upgrade head`（临时容器）。

## D-e2e-26【harness 缺陷】续行中注释截断 docker run（两脚本同款）
- 现象：远端 deploy 半途 `-e: command not found`（192 起的 api docker run 在注释处
  被截断）。波3收口（10-30）插入的 EGRESS 注释卡进反斜杠续行；SS-14 后脚本未真跑，
  潜伏两周。`bash -n` 抓不到（语法合法，纯运行期事故）。
- 修复：注释迁出命令块（remote_deploy.sh + local_deploy.sh 同款雷一并排雷）；
  test_deploy_manifest_gates.py 新增五脚本静态针（上一行 `\` + 本行 `#` 形状）。

## D-e2e-27【本轮作业自纠】docker commit 打镜像补丁把 CMD 烘焙成 `true`
- 现象：远端 cds-api `Restarting (0)`、日志全空——`docker create … true` + commit
  把 CMD 覆盖烘进镜像。
- 处置：重传原始 tar 重新 load，从**不改命令**的 create 容器重打补丁；教训入册：
  给镜像热补文件必须保持 Config.Cmd/Entrypoint 原样。披露：远端证据跑在
  「build7 镜像 + 0030 热补」上，非重建镜像（0030 修复已在 git，下次重建自然收敛）。

## 配置形状披露（非缺陷）
- security 层 3 项 legacy 探针 FAIL（L0/L1/L2 期望 400 实得 201）：dev compose 有意
  携带 `CDS_SANDBOX_LEGACY_ENABLED: "true"`（docker-compose.yml 注释明示的 dev 决策，
  prod 面启动即拒该旗标）。gate 代码正确——另用一次性 `LEGACY_ENABLED=false` 复跑证明
  拒绝路径已由远端实锤：远端车道 CDS_SANDBOX_LEGACY_ENABLED=false，
  security 层 failclosed.legacy_l0/l1/l2 全 PASS（400 legacy_level_retired）。
- 远端环境漂移（非代码）：SS-14 期 api 容器从未发布端口（无 -p）；/root/cds.env 缺
  CDS_COLUMN_ENCRYPTION_KEY 等 A301 必检键（已生成补齐并披露）；minio 需本地 retag
  （远端拉不到 docker.io）；远端无嵌套虚拟化（NO_KVM）→ l2c fail-closed 400 在远端是
  正确行为（SS-13 铁律），l2c 真活路径只在有 KVM 的节点验证（本轮 SKIP，口径不变）。

## 本地三档读数（docker compose 全栈，修复后终态）
| 档 | 结果 | 说明 |
|----|------|------|
| business_loop | **17/17 PASS** | SS-12 scoped-key 闭环 |
| tds_scenarios | 7 PASS / 3 SKIP / 0 FAIL | SKIP=KVM-only（诚实降级） |
| sandbox_security | 7/10 | 3 FAIL=legacy 配置形状（见上，非代码；门拒路径见远端 10/10） |
| dev-sandbox 执行 | PASS（修复后） | D-e2e-20 引爆并熄灭 |

## 远端三档读数（待填：SS-14 宿主机，build7 镜像 + 0030 热补）

| 档 | 结果 | 说明 |
|----|------|------|
| business_loop | **17/17 PASS** | 修复+热补后的 build7 镜像，prod 姿态（DEBUG=false 由脚本钉） |
| sandbox_security | **10/10 PASS** | 含 legacy 三门拒（=dev compose 3 FAIL 的反证）+ l2c 无 KVM fail-closed 400 |
| tds_scenarios | 7 PASS / 3 SKIP / 0 FAIL | SKIP=活会话三件套需 KVM 宿主（诚实口径与 SS-14 一致） |

远端环境处置（披露）：SS-14 遗留僵尸容器 cds-api-old 与两周 unhealthy 的 cds-sink
（旧镜像 /sink.py 实验件，非产品面）已删；/root/cds.env 补齐 A301 必检键；车道
admin 密码经 app 自身散列函数重置（lane 测试身份，非真实凭据）。

结论：镜像口径（py3.12 生产镜像 + 真 PG16）下，本轮全部 D-e2e-20..26 修复在
两条独立轨道上端到端复验通过；唯一开放硬件面仍是 KVM 活会话（口径不变）。
