# CDS v1.0 — Phase 1 完成度迭代计划

> 版本：v1.1
> 日期：2026-06-04
> 作者：@架构师

---

## 完成状态

| 项目 | 状态 | 详情 |
|------|------|------|
| P0-1 数据库索引 | ✅ 完成 | 7 个复合索引 (4 models) |
| P0-2 前端代码分割 | ✅ 完成 | React.lazy + Suspense, 15 页面按需加载, 主包 422KB |
| P1-1 SM2 真实签名 | ✅ 完成 | crypto_service.py (gmssl) + utils/crypto.ts (sm-crypto) |
| P2-1 区块链审计锚定 | ✅ 完成 | audit.py Merkle 树 + output_control.py DLP+DP API |
| P2-2 差分隐私输出 | ✅ 完成 | output_control API (Laplace/Gaussian 噪声) + @Andrew 前端对接中 |

## 新增文件清单

### 后端
- `app/services/crypto_service.py` — SM2/SM3 加密服务 (gmssl)
- `app/api/output_control.py` — DLP 审查 + DP 噪声注入 API
- `app/api/audit.py` — 重写: 真实数据库查询 + Merkle 锚定
- `app/api/monitoring.py` — 增强: 真实统计 + 安全告警

### 前端
- `src/utils/crypto.ts` — 浏览器端 SM2 签名 (sm-crypto)
- `src/services/outputControlApi.ts` — DLP/DP API 服务
- `src/services/auditApi.ts` — 更新: 匹配新后端格式

### 模型变更
- `app/models/data_product.py` — +2 复合索引
- `app/models/contract.py` — +3 复合索引
- `app/models/sandbox_session.py` — +3 复合索引
- `app/models/audit_log.py` — +3 复合索引

## API 端点汇总 (新增/增强)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/auth/generate-sm2-keys` | POST | 生成 SM2 密钥对 |
| `/api/v1/auth/sign-data` | POST | SM2 数据签名 |
| `/api/v1/output-control/inspect` | POST | DLP 审查管线 |
| `/api/v1/output-control/dp/noise` | POST | DP 噪声注入 |
| `/api/v1/output-control/dp/budget/init` | POST | DP 预算初始化 |
| `/api/v1/output-control/dp/budget/{id}` | GET | DP 预算查询 |
| `/api/v1/audit/anchor` | POST | Merkle 树区块链锚定 |
| `/api/v1/audit/verify/{id}` | GET | 区块链验证 |
| `/api/v1/audit/merkle-proof/{id}` | GET | Merkle 证明 |

## 构建状态

- TypeScript: 0 errors
- Vite build: 297ms, 3279 modules
- 代码分割: 15 个页面 chunk + 1 个主包 (422KB / 135KB gzip)
