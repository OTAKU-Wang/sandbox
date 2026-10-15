# 05 — S4 可信数据空间（TDS）场景测试（SS-14 要求 4）

- 时间窗口: 2026-10-13（远程 100.112.5.x:22022，脚本 `deploy/e2e-remote/e2e_tds_scenarios.py`）
- 场景来源: specs SS-01..SS-09 场景契约（T1 契约化访问 / T2 跨方隔离 / T3 输出治理 / T4 审计完整 / T5 会话证明 / T6 合规表面）。
- 证据: `e2e_tds_scenarios_evidence.json`（远程 /root/，关键摘录回填本文档）。

## 1. 场景契约

| 场景 | 远程无节点形态 | 节点轨形态（RT-07 后） |
|---|---|---|
| T1 契约化访问（contract-bound access） | 契约→会话路径完整，l2c 创建诚实 400 RUNTIME_UNAVAILABLE（无节点） | 契约绑定会话真实创建+执行 |
| T2 跨方隔离（cross-party isolation） | SKIP（需活会话） | 两方会话互不可见对方数据/文件 |
| T3 输出治理（output governance） | SKIP（需活会话） | 输出策略（脱敏/拦截）在活会话生效 |
| T4 审计完整（audit completeness） | 全流程动作审计齐全（注册/资源/产品/契约/会话/审计查询） | 同远程 + 执行腿动作 |
| T5 会话证明（session proof bundle） | SKIP（需活会话）；meta 披露面（trust_root/engine）远程可验 | 活会话 proof-bundle 下载+校验 |
| T6 合规表面（compliance surface） | `GET /compliance/reports/quick` 200 | 同 |

## 2. 执行记录

### Round B（API6 首跑）
- 1/2：`setup.buyers` FAIL——buyer 注册邮箱 `@e2e.local` 特殊 TLD 被 email-validator 拒（S2 同族已用 `@cds-e2e.cn`，S4 脚本未同步）→ 修。

### Round C（API6 + 部署 D-e2e-11）
- 5P/3S/2F：`setup.buyers` 转 PASS（TLD 修复生效）；`setup.product_published` FAIL（资源上传被 D-e2e-12 HSM 缺陷 500 阻断——脚本已加上传显式检查，失败原因首次可读）；T1 契约路径 PASS（400 = 无节点 fail-closed，契约腿完整）；T4 审计完整 PASS（缺失动作: none）；T5 meta 披露 PASS；T6 FAIL（路径错：`/compliance/reports` 是 POST 生成端点，GET 面是 `/reports/quick`——脚本已修）；T2/T3/T5 活会话腿 SKIP（无节点，归节点轨）。

### Round D（API7：D-e2e-12）
- 2P/0S/1F：`setup.product_published` 仍 FAIL（wrap DEK 编码缺陷 D-e2e-13 阻断上传）——脚本严格化生效（失败原因可读：resource upload failed 500）。

### Round E（API8：D-e2e-13）——全绿（远程形态）
- **7 passed / 3 skipped / 0 failed**（证据 /root/e2e_tds_scenarios_evidence.json）：setup 三腿全 PASS（admin / 两个隔离 buyer 身份 / 产品发布链）→ T1 契约化访问 PASS（契约→会话路径完整，l2c 创建诚实 400 无节点）→ T4 审计完整 PASS（缺失动作: none）→ T5 meta 披露 PASS（trust_root + sandbox_engine 诚实）→ T6 合规表面 PASS（GET /reports/quick 200）；T2 跨方隔离 / T3 输出治理 / T5 proof-bundle SKIP（需活会话，归节点轨 RT-07）。
- **S4 判定: PASS（远程无节点形态）**——活会话场景待节点轨回填 §3。

## 3. 节点轨活会话场景（RT-07，2026-10-14 本机 KVM，已回填）

同一脚本对节点轨（http://127.0.0.1:8000 + 本机 Cube 节点）执行，**12 passed / 0 skipped / 0 failed**（证据 evidence-node/tds_scenarios_node.json）：

| 场景 | 结果 | 证据 |
|---|---|---|
| T1 契约化访问 | PASS | admin 会话 on published product（真实 l2c 创建 + running） |
| T2.setup_buyer_session | PASS | buyer A 自建会话（D-e2e-21 属主隔离修正：TDS 正典流程 = buyer 买断会话） |
| T2.setup_upload | PASS | buyer A 上传自己会话（admin 创建的会话对 buyer 403 = 租户隔离正确行为） |
| T2 跨方隔离 | PASS | buyer B 读 A 会话文件 → 403/404 |
| T3 输出治理 | PASS | 活会话文件下载携带 `X-CDS-Output-Review='passed; findings=0'`（l2c 下载 T5 审查分支，D-e2e-20） |
| T5 会话证明 | PASS | 活会话 proof-bundle evidence_hash 在档（不可抵赖） |
| T5 meta 披露 | PASS | trust_root + sandbox_engine 诚实 |
| T4 审计完整 | PASS | 缺失动作: none（含节点轨执行腿动作） |
| T6 合规表面 | PASS | GET /reports/quick 200 |

## 4. 结论
- **远程形态: PASS（7P/3S/0F）**——T1/T4/T5-meta/T6 全绿 + setup 链完整。
- **节点轨形态: PASS（12P/0S/0F）**——T2 跨方隔离 / T3 输出治理 / T5 proof-bundle 活会话腿全部真实执行全绿（l2c 活会话）。
- 两形态总定稿：T1..T6 六场景在两条形态下全部 PASS，无 SKIP 遗留。
