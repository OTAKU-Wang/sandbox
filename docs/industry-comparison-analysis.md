# CDS 密态沙箱 — 业界对标 + 前后端对接分析

> 生成日期：2026-06-06
> 分析人：@Cindy + @Andrew + @测试工程师

---

## 一、业界对标矩阵

| 能力 | CDS | Snowflake | Databricks | AWS | Google | Huawei |
|------|-----|-----------|------------|-----|--------|--------|
| TEE 硬件隔离 | L1/L2/L3 三级 | ❌ 软件 | ❌ 软件 | ❌ 软件 | CVM | TrustZone |
| 差分隐私 | DP 预算账本 | 内建 | 有限 | 内建噪声 | ❌ | ❌ |
| PII 检测 | 正则+NER 双层 | ❌ | ❌ | ❌ | ❌ | ❌ |
| 国密全栈 | SM2/SM3/SM4/TLCP | ❌ | ❌ | ❌ | ❌ | 部分 |
| 区块链审计 | FISCO BCOS Merkle | ❌ | ❌ | ❌ | ❌ | ❌ |
| 数字合约 | 全生命周期 | ❌ | ❌ | ❌ | ❌ | ❌ |
| GPU-TEE 训练 | LLM SFT/PT/RLHF | ❌ | ❌ | Clean Rooms ML | Confidential GPU | ❌ |
| 输出水印 | 零宽+LSB+模型权重 | ❌ | ❌ | ❌ | ❌ | ❌ |
| MIA 探针 | 逐输出评分 | ❌ | ❌ | ❌ | ❌ | ❌ |
| 字段级 ACL | 列级 read/aggregate/mask | 列掩码 | 列掩码 | 列规则 | ❌ | ❌ |

### CDS 独有优势
1. 全栈国密 — 国际产品均不支持
2. 三级自适应隔离 — 竞品仅单级
3. 6 阶段输出安全管线
4. 区块链不可变审计 + Merkle 证明
5. GPU-TEE AI 训练管线

### CDS 不足
1. 生态成熟度 — 竞品有成千客户
2. SQL 接口简洁性 — 竞品提供纯 SQL
3. ID 映射/数据匹配 — AWS 有内建身份解析
4. 跨云数据织网 — Snowflake 原生跨云
5. 托管服务弹性 — 云厂商自动扩缩容

---

## 二、前后端对接问题

### 2.1 前端 API 调用会 404（11 个）

| 模块 | 前端调用 | 后端实际 | 问题 |
|------|---------|---------|------|
| 联邦 | GET /federation/trusts/{id} | 无此路由 | 缺接口 |
| 联邦 | POST /federation/trusts | POST /federation/trust | 单复数不一致 |
| 联邦 | GET /federation/catalog/sync-status | 无 | 缺接口 |
| 联邦 | POST /federation/catalog/sync/{id} | 无 | 缺接口 |
| 联邦 | GET /federation/catalog/entries | 无 | 缺接口 |
| 联邦 | GET /federation/trusts/{id}/score | 无 | 缺接口 |
| 训练 | POST /training/validate-config | 无 | 缺接口 |
| 训练 | GET /training/jobs/{id}/audit | 无 | 缺接口 |
| 训练 | GET /training/jobs/{id}/checkpoints | 无 | 缺接口 |
| 连接器 | GET /connectors/{id} | 仅列表 | 缺单个查询 |
| 证书 | POST /certificates/{id}/verify | POST /certificates/verify | 路径不匹配 |

### 2.2 后端模块无前端 UI（13 个）

| 模块 | 路由前缀 | 说明 |
|------|---------|------|
| MPC 密钥分片 | /api/v1/mpc | 多方计算 |
| 沙箱数据库 | /api/v1/sandbox-db | DuckDB 管理 |
| 沙箱任务 | /api/v1/sandbox-tasks | 任务队列 |
| 网络策略 | /api/v1/network-policies | iptables 规则 |
| 合规 | /api/v1/compliance | 合规报告 |
| 网关 | /api/v1/gateway | 执行网关 |
| 数据管线 | /api/v1/data-pipeline | ETL 管线 |
| 字段暴露 | /api/v1/field-exposure | 字段级暴露统计 |
| 训练水印 | /api/v1/training/.../watermark | 模型水印 |

### 2.3 正常对接的核心模块

认证、数据产品、合约、沙箱会话、审计、监控、输出控制、目录、数据资源 — **全部正常**

---

## 三、改进优先级

| 优先级 | 改进项 | 工作量 | 负责人 |
|--------|--------|--------|--------|
| P0 | 修复 11 个断裂 API 调用 | 2 天 | @Andrew |
| P1 | 为 9 个后端模块添加前端 UI | 5 天 | @Andrew |
| P2 | SQL 接口简化 | 3 天 | @Cindy 设计 |
| P2 | 跨空间 ID 映射 | 3 天 | @Andrew |
| P3 | 托管服务弹性 | 架构级 | 远期 |
