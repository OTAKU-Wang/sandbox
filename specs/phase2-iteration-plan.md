# CDS v2.0 — Phase 2 迭代计划

> 版本：v1.0
> 日期：2026-06-04
> 作者：@架构师
> 输入：@Andrew gap 分析 + @测试工程师 QA 审查 + @Cindy spec 对标

---

## 系统现状

| 维度 | 状态 |
|------|------|
| 后端 API | 10 模块, 201 测试全通过 |
| 前端页面 | 16 页面, tsc 0 errors, Vite 300ms/419KB |
| 安全基础 | SM2签名 + DLP审查 + DP噪声 + 审计日志 |
| 沙箱 | L3 bwrap 单层隔离 |
| 合规 | TC609/GB-T 44249 框架对齐 |

---

## Gap 分类与优先级

### P0 — 阻塞核心流程

| ID | Gap | 说明 | Sprint |
|----|-----|------|--------|
| G01 | 任务执行管线 | task/submit + task/result API, Celery队列, Worker, 代码沙箱执行 | S2-S3 |
| G02 | 数据目录搜索 | 买方发现数据产品入口, /catalog API + 前端搜索页 | S1 |
| G03 | OPA策略编译完善 | Rego模板→真实OPA编译, 策略包SM4加密存储 | S1 |
| G04 | MinIO加密存储 | 数据产品文件上传/下载/SM4加密 | S1 |
| G05 | DataProduct模型补全 | +security_level, +allowed_operations, +output_constraints | S1 |

### P1 — 重要功能缺口

| ID | Gap | 说明 | Sprint |
|----|-----|------|--------|
| G06 | 前端页面补全 | /catalog, /console, /identity/keys, /identity/certs, /audit/verify | S2-S3 |
| G07 | KMS/Vault集成 | DEK管理, 密钥轮换, SM4-GCM端到端加密 | S3 |
| G08 | ClickHouse审计 | 列式存储, TTL策略, 高频写入优化 | S3 |
| G09 | Merkle proof完善 | merkle_leaves表, 第三方可验证证明 | S2 |
| G10 | SM2证书认证 | X.509证书身份, 证书链验证 | S3 |
| G11 | 输出水印 | 文本隐写 + LSB图像水印 | S2 |
| G12 | SM2输出签名 | 计算结果SM2签名, 防篡改 | S2 |

### P2 — 增强与合规

| ID | Gap | 说明 | Sprint |
|----|-----|------|--------|
| G13 | Dashboard图表 | 趋势图/饼图/实时事件流 | S4 |
| G14 | 角色视图 | 数据商/买方/运营/安全运维差异化UI | S4 |
| G15 | 数据产品版本管理 | 版本号/变更历史/回滚 | S4 |
| G16 | 沙箱适配器升级 | L2 Firecracker + L1 SGX (硬件就绪后) | S5+ |
| G17 | 跨空间互联 | Connector Gateway, 数据同步Worker | v3.0 |
| G18 | 非结构化管线 | OCR/人脸/ASR/视频/DICOM | v3.0 |
| G19 | TLCP国密TLS | GM/T 0024 协议支持 | v3.0 |

### 安全加固 (来自 QA 审查)

| ID | 问题 | 严重度 | Sprint |
|----|------|--------|--------|
| S01 | CORS `*` 通配 | P0 | S1 |
| S02 | JWT 24h无刷新 | P1 | S1 |
| S03 | 审计无RBAC | P1 | S1 |
| S04 | 存储路径遍历风险 | P2 | S2 |
| S05 | 测试覆盖不足 | P1 | S1-S2 |

---

## Sprint 规划

### Sprint 1 — 安全加固 + 核心补齐 (2周)

**目标**: 修复安全缺口 + 数据产品可被发现

| 任务 | 负责人 | 估时 |
|------|--------|------|
| S01: CORS白名单配置 | @Andrew | 0.5d |
| S02: JWT refresh token | @Andrew | 1d |
| S03: 审计RBAC权限 | @Andrew | 1d |
| G05: DataProduct模型补全 | @Andrew | 1d |
| G02: 数据目录API + 前端搜索页 | @Andrew | 3d |
| G03: OPA策略编译完善 | @Andrew | 2d |
| G04: MinIO加密存储 | @Andrew | 2d |
| S05: 补充output_control/audit测试 | @测试工程师 | 2d |

### Sprint 2 — 输出安全 + 审计完善 (2周)

**目标**: 输出全链路安全 + 第三方可验证

| 任务 | 负责人 | 估时 |
|------|--------|------|
| G11: 输出水印(文本隐写+LSB) | @Andrew | 3d |
| G12: SM2输出签名 | @Andrew | 2d |
| G09: Merkle proof完善 | @Andrew | 2d |
| G06: /audit/verify前端页 | @Andrew | 1d |
| S04: 存储路径遍历防护 | @Andrew | 1d |
| S05: Vitest前端测试框架 | @测试工程师 | 2d |
| 回归测试 | @测试工程师 | 2d |

### Sprint 3 — 任务执行管线 (3周)

**目标**: 沙箱任务可提交、可执行、可查询

| 任务 | 负责人 | 估时 |
|------|--------|------|
| G01-a: Task模型+API(submit/result/list) | @Andrew | 3d |
| G01-b: Celery任务队列+Worker | @Andrew | 3d |
| G01-c: L3 Docker沙箱适配器 | @Andrew | 3d |
| G01-d: /console前端页(提交+结果) | @Andrew | 3d |
| G07: KMS Vault集成 | @Andrew | 3d |
| G08: ClickHouse审计存储 | @Andrew | 2d |
| E2E任务执行全流程测试 | @测试工程师 | 3d |

### Sprint 4 — 增强功能 (2周)

| 任务 | 负责人 | 估时 |
|------|--------|------|
| G10: SM2证书认证 | @Andrew | 3d |
| G13: Dashboard图表增强 | @Andrew | 2d |
| G14: 角色视图差异化 | @Andrew | 2d |
| G15: 数据产品版本管理 | @Andrew | 2d |
| 安全渗透测试 | @测试工程师 | 3d |

### Sprint 5+ — 远期 (v3.0)

- G16: L2 Firecracker / L1 SGX 适配器
- G17: 跨空间互联 (Connector Gateway)
- G18: 非结构化数据管线 (OCR/ASR/视频)
- G19: TLCP国密TLS
- MPC密钥共享协议
- TEE远程证明

---

## 技术决策

1. **任务队列**: Celery + Redis (已有Redis, 复用)
2. **存储加密**: SM4-GCM + MinIO SSE (双层)
3. **策略编译**: OPA Python SDK (opa-python) → Rego字节码
4. **前端测试**: Vitest + React Testing Library
5. **ClickHouse**: 审计日志异步写入, TTL 6个月
6. **沙箱适配器**: 统一RuntimeAdapter接口, L3 Docker先行

---

## 验收标准

每个 Sprint 完成后:
- 后端测试 ≥ 200 (新增测试覆盖新功能)
- 前端 tsc 0 errors + Vitest 通过
- Vite build ≤ 500KB (主包)
- @测试工程师 回归验证通过
- @架构师 架构审查通过
