# 星信可信数据空间 · 数据沙箱开发平台规格说明

> 本文档为 `specs/data-sandbox-developer-portal-prd.md` 在 `docs/` 目录下的对齐镜像与快速指引索引。

## 1. 核心定位与原则
1. **双模式工作台**：
   - 默认模式：面向算法工程师/数据使用方的 **开发者工作台 (Developer Portal)**，采用 4+2 极简架构（首页、我的开发、我的结果集、安全管理 + 开发指引、环境配额）。
   - 管理态：面向平台管理员的 **空间管理后台 (Space Admin Console)**，承载 CubeSandbox 物理节点纳管、CA 证书、KMS/MPC 分布式密钥、跨空间联邦等底层平台能力。
2. **唯 CubeSandbox 架构**：
   - 彻底废除并下线历史遗留的 Bubblewrap (bwrap) 进程沙箱与 Kubernetes Pod 容器沙箱。
   - 所有调试与生产执行 100% 运行于独立的 KVM MicroVM。
3. **交付三通道闭环**：
   - 离线数据集导出包下载（隐形数字水印 + SM2 签名）。
   - 在线 OpenAPI 导出与监控。
   - Webhook 结果推送。

## 2. 权威文档与原型路径
- **高保真可交互原型 (HTML)**：[specs/prototype-v3.html](../specs/prototype-v3.html)
- **产品功能与需求规格说明书 (PRD)**：[specs/data-sandbox-developer-portal-prd.md](../specs/data-sandbox-developer-portal-prd.md)
- **AI Agent 开发实施计划与 Spec**：[specs/developer-portal-implementation-plan.md](../specs/developer-portal-implementation-plan.md)
- **数据流通闭环底层技术规范**：[specs/ss-15-flow-orchestration.md](../specs/ss-15-flow-orchestration.md)
- **系统前端规格说明书**：[specs/frontend-spec.md](../specs/frontend-spec.md)
- **系统总产品规格说明书**：[specs/product-spec.md](../specs/product-spec.md)
