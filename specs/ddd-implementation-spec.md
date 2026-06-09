# 密态沙箱系统（CDS）· DDD 领域驱动实现设计文档

> 版本：v1.0
> 作者：@架构师
> 日期：2026-06-03
> 方法论：Domain-Driven Design (DDD) + 敏捷开发
> 对齐文档：product-design.md v1.0

---

## 目录

1. [领域划分与限界上下文](#1-领域划分与限界上下文)
2. [合约域（Contract Domain）](#2-合约域contract-domain)
3. [沙箱域（Sandbox Domain）](#3-沙箱域sandbox-domain)
4. [身份域（Identity Domain）](#4-身份域identity-domain)
5. [输出管控域（Output Control Domain）](#5-输出管控域output-control-domain)
6. [审计域（Audit Domain）](#6-审计域audit-domain)
7. [数据产品域（Data Product Domain）](#7-数据产品域data-product-domain)
8. [跨域集成与领域事件](#8-跨域集成与领域事件)
9. [前端架构设计](#9-前端架构设计)
10. [API 设计规范](#10-api-设计规范)
11. [数据库设计](#11-数据库设计)
12. [实现优先级与迭代计划](#12-实现优先级与迭代计划)

---

## 1. 领域划分与限界上下文

### 1.1 上下文映射图（Context Map）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          CDS 限界上下文映射                                  │
│                                                                             │
│  ┌──────────────┐    遵从者     ┌──────────────┐    遵从者    ┌────────────┐ │
│  │  合约域       │◄────────────│  沙箱域       │───────────►│ 输出管控域  │ │
│  │  Contract     │             │  Sandbox      │            │ Output     │ │
│  │              │             │              │            │ Control    │ │
│  └──────┬───────┘             └──────┬───────┘            └─────┬──────┘ │
│         │                            │                          │        │
│         │ 共享内核                    │ 发布语言                  │        │
│         │                            │                          │        │
│  ┌──────▼───────┐             ┌──────▼───────┐            ┌─────▼──────┐ │
│  │  身份域       │◄────────────│  审计域       │───────────►│ 数据产品域  │ │
│  │  Identity     │   事件订阅   │  Audit       │  事件发布   │ DataProduct│ │
│  │              │             │              │            │            │ │
│  └──────────────┘             └──────────────┘            └────────────┘ │
│                                                                             │
│  关系类型：                                                                   │
│  ├─ 共享内核 (Shared Kernel): 身份域 ↔ 合约域（用户/组织模型）                 │
│  ├─ 遵从者 (Conformist): 沙箱域 → 合约域（合约策略约束沙箱行为）               │
│  ├─ 发布语言 (Published Language): 审计域 ← 各域（统一审计事件格式）           │
│  ├─ 事件订阅 (Event Subscription): 输出管控域 ← 沙箱域（执行结果事件）         │
│  └─ 开放主机服务 (OHS): 数据产品域 → 外部系统（数据交易所对接）                │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 限界上下文定义

| 限界上下文 | 核心能力 | 聚合根 | 主要参与者 |
|-----------|---------|--------|-----------|
| **合约域** | 数字合约生命周期管理 | `Contract` | ContractService, PolicyEngine |
| **沙箱域** | 安全隔离执行环境管理 | `SandboxSession` | SandboxManager, RuntimeAdapter |
| **身份域** | 身份认证与权限管理 | `Principal` (User/Org) | IdentityProvider, CertificateService |
| **输出管控域** | 计算结果审查与脱敏 | `OutputResult` | OutputInspector, DPBudgetManager |
| **审计域** | 全链路审计与存证 | `AuditRecord` | AuditService, BlockchainAnchor |
| **数据产品域** | 数据产品注册与管理 | `DataProduct` | DataProductService, CatalogService |

---

## 2. 合约域（Contract Domain）

### 2.1 领域模型

```typescript
// ===== 聚合根：Contract =====
export class Contract {
  private readonly id: ContractId;
  private dataProductId: DataProductId;
  private providerId: PrincipalId;      // 数商
  private consumerId: PrincipalId;      // 买方
  private status: ContractStatus;
  private terms: ContractTerms;         // 值对象
  private signatures: Signature[];      // 值对象集合
  private createdAt: Date;
  private expiresAt: Date;

  // 领域方法
  sign(principal: Principal, sm2Signature: SM2Signature): void;
  activate(): void;
  suspend(reason: string): void;
  terminate(): void;
  renew(newExpiresAt: Date): void;

  // 查询
  isExpired(): boolean;
  isValid(): boolean;
  getRemainingQuota(): UsageQuota;
}

// ===== 值对象：ContractTerms =====
export interface ContractTerms {
  allowedOperations: OperationType[];    // ['aggregate', 'filter', 'model_train', 'inference']
  forbiddenOperations: OperationType[];  // ['row_export', 'network_access', 'process_create']
  outputConstraints: OutputConstraints;  // 值对象
  sandboxConfig: SandboxConfig;          // 值对象
  dpBudget: DPBudget;                    // 值对象
  usageQuota: UsageQuota;                // 值对象
}

export interface OutputConstraints {
  maxRows: number;
  dpEpsilon: number;                     // 差分隐私预算
  allowedFormats: OutputFormat[];        // ['json', 'csv', 'parquet']
  watermarkEnabled: boolean;
}

export interface SandboxConfig {
  maxDurationSeconds: number;
  maxMemoryMb: number;
  allowedLanguages: RuntimeLanguage[];   // ['python', 'sql', 'r']
  requiresTee: boolean;
  networkAccess: boolean;
}

export interface DPBudget {
  totalEpsilon: number;                  // 总隐私预算
  usedEpsilon: number;                   // 已消耗
  delta: number;                         // δ 参数
  mechanism: 'laplace' | 'gaussian';
}

// ===== 值对象：Signature =====
export interface Signature {
  principalId: PrincipalId;
  algorithm: 'SM2';
  value: string;                         // Base64 编码的签名
  timestamp: Date;
  certificateId: string;                 // SM2 证书 ID
}

// ===== 枚举 =====
export enum ContractStatus {
  DRAFT = 'draft',
  PENDING_PROVIDER = 'pending_provider',  // 等待数商签名
  PENDING_CONSUMER = 'pending_consumer',  // 等待买方签名
  ACTIVE = 'active',
  SUSPENDED = 'suspended',
  TERMINATED = 'terminated',
  EXPIRED = 'expired',
}

// ===== 领域事件 =====
export interface ContractSigned {
  type: 'ContractSigned';
  contractId: ContractId;
  signedBy: PrincipalId;
  timestamp: Date;
}

export interface ContractActivated {
  type: 'ContractActivated';
  contractId: ContractId;
  dataProductId: DataProductId;
  providerId: PrincipalId;
  consumerId: PrincipalId;
  terms: ContractTerms;
  timestamp: Date;
}

export interface ContractTerminated {
  type: ContractTerminated;
  contractId: ContractId;
  reason: string;
  timestamp: Date;
}
```

### 2.2 应用服务

```typescript
// ===== ContractService =====
export class ContractService {
  constructor(
    private contractRepo: ContractRepository,
    private policyEngine: PolicyEngine,
    private eventBus: EventBus,
    private sm2Service: SM2Service,
  ) {}

  // 创建合约
  async createContract(dto: CreateContractDTO): Promise<ContractId> {
    // 1. 验证数据产品存在且可用
    // 2. 验证双方资质
    // 3. 创建合约草稿
    // 4. 返回合约 ID
  }

  // 签名合约
  async signContract(contractId: ContractId, principalId: PrincipalId, signature: SM2Signature): Promise<void> {
    // 1. 验证签名有效性（SM2 验签）
    // 2. 验证签名者是合约参与方
    // 3. 调用合约聚合根的 sign 方法
    // 4. 如果双方都已签名，激活合约
    // 5. 发布 ContractActivated 事件
  }

  // 检查操作是否符合合约条款
  async checkOperation(contractId: ContractId, operation: Operation): Promise<PolicyDecision> {
    // 1. 获取合约
    // 2. 调用 PolicyEngine 评估
    // 3. 返回允许/拒绝 + 原因
  }

  // 消耗隐私预算
  async consumeDPBudget(contractId: ContractId, epsilon: number): Promise<boolean> {
    // 1. 检查剩余预算
    // 2. 原子性扣减
    // 3. 如果预算耗尽，暂停合约
  }
}

// ===== PolicyEngine =====
export class PolicyEngine {
  // 基于 OPA Rego 的策略评估
  async evaluate(context: PolicyContext): Promise<PolicyDecision> {
    // 将合约条款转换为 OPA 策略包
    // 实时评估操作是否合规
  }
}
```

### 2.3 仓储接口

```typescript
export interface ContractRepository {
  save(contract: Contract): Promise<void>;
  findById(id: ContractId): Promise<Contract | null>;
  findByProvider(providerId: PrincipalId, pagination: Pagination): Promise<Contract[]>;
  findByConsumer(consumerId: PrincipalId, pagination: Pagination): Promise<Contract[]>;
  findByDataProduct(dataProductId: DataProductId): Promise<Contract[]>;
  findActive(): Promise<Contract[]>;
}
```

---

## 3. 沙箱域（Sandbox Domain）

### 3.1 领域模型

```typescript
// ===== 聚合根：SandboxSession =====
export class SandboxSession {
  private readonly id: SessionId;
  private contractId: ContractId;
  private dataProductId: DataProductId;
  private consumerId: PrincipalId;
  private status: SessionStatus;
  private sandboxType: SandboxType;      // L1 | L2 | L3
  private runtime: RuntimeInfo;           // 值对象
  private resourceUsage: ResourceUsage;   // 值对象
  private createdAt: Date;
  private startedAt: Date | null;
  private terminatedAt: Date | null;

  // 领域方法
  start(): void;
  pause(): void;
  resume(): void;
  terminate(reason: string): void;
  updateResourceUsage(usage: ResourceUsage): void;

  // 查询
  isRunning(): boolean;
  getElapsedSeconds(): number;
  getRemainingTime(): number;
}

// ===== 值对象 =====
export interface RuntimeInfo {
  language: RuntimeLanguage;
  version: string;
  executor: string;                      // 执行器路径
  sandboxImage: string;                  // 沙箱镜像/配置
  teeType: 'sgx' | 'tdx' | 'sev' | null;
}

export interface ResourceUsage {
  cpuMs: number;
  memoryMb: number;
  diskMb: number;
  networkBytesIn: number;
  networkBytesOut: number;
  pidCount: number;
}

// ===== 实体：Task =====
export class Task {
  private readonly id: TaskId;
  private sessionId: SessionId;
  private type: TaskType;                // 'sql' | 'script' | 'api_call'
  private code: string;
  private status: TaskStatus;
  private result: TaskResult | null;
  private startedAt: Date;
  private completedAt: Date | null;

  execute(): Promise<void>;
  abort(): void;
}

// ===== 值对象：TaskResult =====
export interface TaskResult {
  exitCode: number;
  stdout: string;
  stderr: string;
  outputFiles: OutputFile[];
  duration: number;
}

export interface OutputFile {
  path: string;
  size: number;
  mimeType: string;
  hash: string;                          // SM3 哈希
}

// ===== 枚举 =====
export enum SandboxType {
  L1_TEE = 'l1_tee',           // SGX/TDX/SEV 硬件隔离
  L2_MICROVM = 'l2_microvm',   // Firecracker + gVisor
  L3_CONTAINER = 'l3_container', // Docker + Seccomp-BPF
}

export enum SessionStatus {
  PROVISIONING = 'provisioning',
  READY = 'ready',
  RUNNING = 'running',
  PAUSED = 'paused',
  TERMINATED = 'terminated',
  FAILED = 'failed',
}

// ===== 领域事件 =====
export interface SandboxCreated {
  type: 'SandboxCreated';
  sessionId: SessionId;
  contractId: ContractId;
  sandboxType: SandboxType;
  timestamp: Date;
}

export interface SandboxTerminated {
  type: 'SandboxTerminated';
  sessionId: SessionId;
  reason: string;
  resourceUsage: ResourceUsage;
  timestamp: Date;
}

export interface TaskCompleted {
  type: 'TaskCompleted';
  sessionId: SessionId;
  taskId: TaskId;
  result: TaskResult;
  timestamp: Date;
}
```

### 3.2 应用服务

```typescript
// ===== SandboxManager =====
export class SandboxManager {
  constructor(
    private sessionRepo: SandboxSessionRepository,
    private contractService: ContractService,
    private runtimeAdapter: RuntimeAdapter,
    private cgroupManager: CgroupManager,
    private eventBus: EventBus,
  ) {}

  // 创建沙箱会话
  async createSession(dto: CreateSessionDTO): Promise<SessionId> {
    // 1. 验证合约有效且未过期
    // 2. 根据数据敏感级别选择沙箱类型 (L1/L2/L3)
    // 3. 验证 TEE 远程证明（L1 场景）
    // 4. 分配资源（cgroup）
    // 5. 创建 SandboxSession 聚合根
    // 6. 发布 SandboxCreated 事件
  }

  // 执行任务
  async executeTask(sessionId: SessionId, taskDto: TaskDTO): Promise<TaskResult> {
    // 1. 验证会话状态为 RUNNING
    // 2. 验证操作符合合约条款
    // 3. 创建 Task 实体
    // 4. 通过 RuntimeAdapter 执行
    // 5. 监控资源使用
    // 6. 返回结果
  }

  // 终止会话
  async terminateSession(sessionId: SessionId, reason: string): Promise<void> {
    // 1. 终止所有运行中的任务
    // 2. 释放资源（cgroup 清理）
    // 3. 收集最终资源使用统计
    // 4. 发布 SandboxTerminated 事件
  }
}

// ===== RuntimeAdapter（适配不同沙箱运行时） =====
export interface RuntimeAdapter {
  provision(type: SandboxType, config: SandboxConfig): Promise<RuntimeHandle>;
  execute(handle: RuntimeHandle, task: Task): Promise<TaskResult>;
  terminate(handle: RuntimeHandle): Promise<void>;
  getResourceUsage(handle: RuntimeHandle): Promise<ResourceUsage>;
}

// ===== BwrapRuntimeAdapter（L3 容器级沙箱） =====
export class BwrapRuntimeAdapter implements RuntimeAdapter {
  // 基于现有 bwrap 沙箱实现
  // 复用 SandboxExecutor 的逻辑
}

// ===== FirecrackerRuntimeAdapter（L2 微虚拟机） =====
export class FirecrackerRuntimeAdapter implements RuntimeAdapter {
  // 基于 Firecracker microVM
  // gVisor 内核级隔离
}

// ===== OcclumRuntimeAdapter（L1 TEE） =====
export class OcclumRuntimeAdapter implements RuntimeAdapter {
  // 基于 Occlum LibOS
  // SGX/TDX 硬件隔离
}
```

### 3.3 仓储接口

```typescript
export interface SandboxSessionRepository {
  save(session: SandboxSession): Promise<void>;
  findById(id: SessionId): Promise<SandboxSession | null>;
  findByContract(contractId: ContractId): Promise<SandboxSession[]>;
  findByConsumer(consumerId: PrincipalId, pagination: Pagination): Promise<SandboxSession[]>;
  findRunning(): Promise<SandboxSession[]>;
}
```

---

## 4. 身份域（Identity Domain）

### 4.1 领域模型

```typescript
// ===== 聚合根：Principal =====
export abstract class Principal {
  protected readonly id: PrincipalId;
  protected name: string;
  protected status: PrincipalStatus;
  protected certificates: Certificate[];
  protected createdAt: Date;

  abstract getRole(): PrincipalRole;
  abstract getPermissions(): Permission[];
}

// ===== 实体：Organization =====
export class Organization extends Principal {
  private creditCode: string;            // 统一社会信用代码
  private industry: string;
  private contactInfo: ContactInfo;
  private members: OrganizationMember[];

  getRole(): PrincipalRole { return 'organization'; }
  getPermissions(): Permission[] { /* ... */ }
}

// ===== 实体：User =====
export class User extends Principal {
  private organizationId: PrincipalId | null;
  private role: UserRole;
  private mfaEnabled: boolean;
  private lastLoginAt: Date;

  getRole(): PrincipalRole { return this.role; }
  getPermissions(): Permission[] { /* ... */ }
}

// ===== 值对象：Certificate =====
export interface Certificate {
  id: string;
  type: 'SM2';
  subject: string;
  issuer: string;
  serialNumber: string;
  validFrom: Date;
  validTo: Date;
  pem: string;
  status: 'active' | 'revoked' | 'expired';
}

// ===== 值对象：ContactInfo =====
export interface ContactInfo {
  email: string;
  phone: string;
  address: string;
}

// ===== 枚举 =====
export enum UserRole {
  PROVIDER = 'provider',          // 数据提供商
  CONSUMER = 'consumer',          // 数据使用方
  OPERATOR = 'operator',          // 运营方
  SECURITY_ADMIN = 'security_admin', // 安全运维
  PLATFORM_ADMIN = 'platform_admin', // 平台管理员
}

export enum PrincipalStatus {
  PENDING_VERIFICATION = 'pending_verification',
  ACTIVE = 'active',
  SUSPENDED = 'suspended',
  REVOKED = 'revoked',
}

// ===== 领域事件 =====
export interface OrganizationRegistered {
  type: 'OrganizationRegistered';
  organizationId: PrincipalId;
  creditCode: string;
  timestamp: Date;
}

export interface CertificateIssued {
  type: 'CertificateIssued';
  principalId: PrincipalId;
  certificateId: string;
  timestamp: Date;
}

export interface CertificateRevoked {
  type: 'CertificateRevoked';
  principalId: PrincipalId;
  certificateId: string;
  reason: string;
  timestamp: Date;
}
```

### 4.2 应用服务

```typescript
// ===== IdentityService =====
export class IdentityService {
  constructor(
    private principalRepo: PrincipalRepository,
    private caService: CAService,           // 证书颁发服务
    private sm2Service: SM2Service,
    private eventBus: EventBus,
  ) {}

  // 注册组织
  async registerOrganization(dto: RegisterOrgDTO): Promise<PrincipalId> {
    // 1. 验证统一社会信用代码
    // 2. 创建组织实体
    // 3. 颁发 SM2 证书
    // 4. 发布事件
  }

  // 身份认证
  async authenticate(credentials: Credentials): Promise<AuthToken> {
    // 1. 验证 SM2 签名
    // 2. 验证证书有效性
    // 3. 签发 JWT Token
  }

  // 权限检查
  async authorize(principalId: PrincipalId, resource: string, action: string): Promise<boolean> {
    // RBAC + ABAC 混合授权
  }
}
```

---

## 5. 输出管控域（Output Control Domain）

### 5.1 领域模型

```typescript
// ===== 聚合根：OutputResult =====
export class OutputResult {
  private readonly id: OutputId;
  private sessionId: SessionId;
  private taskId: TaskId;
  private contractId: ContractId;
  private status: OutputStatus;
  private inspectionStages: InspectionStage[];  // 6阶段审查管线
  private finalContent: Buffer | null;
  private metadata: OutputMetadata;

  // 领域方法
  addInspectionStage(stage: InspectionStage): void;
  approve(): void;
  reject(reason: string): void;
  getInspectionSummary(): InspectionSummary;
}

// ===== 值对象：InspectionStage =====
export interface InspectionStage {
  stage: InspectionStageType;
  status: 'passed' | 'failed' | 'skipped';
  details: string;
  duration: number;
  timestamp: Date;
}

export enum InspectionStageType {
  FORMAT_VALIDATION = 'format_validation',
  DLP_SCAN = 'dlp_scan',
  DIFFERENTIAL_PRIVACY = 'differential_privacy',
  VOLUME_LIMIT = 'volume_limit',
  WATERMARK_INJECTION = 'watermark_injection',
  SM2_SIGNATURE = 'sm2_signature',
}

// ===== 实体：DPBudgetTracker =====
export class DPBudgetTracker {
  private contractId: ContractId;
  private totalEpsilon: number;
  private usedEpsilon: number;
  private delta: number;
  private queries: DPQueryRecord[];

  // 消耗预算
  consume(epsilon: number): boolean;
  // 检查剩余
  getRemaining(): number;
  // 重置（合约续期时）
  reset(): void;
}

// ===== 值对象：WatermarkInfo =====
export interface WatermarkInfo {
  type: 'visible' | 'invisible';
  algorithm: 'lsb' | 'dct' | 'text';
  content: string;                       // 水印内容（含会话ID、时间戳）
  injectedAt: Date;
}

// ===== 领域事件 =====
export interface OutputApproved {
  type: 'OutputApproved';
  outputId: OutputId;
  sessionId: SessionId;
  inspectionSummary: InspectionSummary;
  timestamp: Date;
}

export interface OutputRejected {
  type: 'OutputRejected';
  outputId: OutputId;
  sessionId: SessionId;
  reason: string;
  failedStage: InspectionStageType;
  timestamp: Date;
}

export interface DPBudgetExhausted {
  type: 'DPBudgetExhausted';
  contractId: ContractId;
  totalEpsilon: number;
  usedEpsilon: number;
  timestamp: Date;
}
```

### 5.2 应用服务

```typescript
// ===== OutputInspector =====
export class OutputInspector {
  constructor(
    private outputRepo: OutputResultRepository,
    private dlpScanner: DLPScanner,
    private dpEngine: DPEngine,
    private watermarkService: WatermarkService,
    private sm2Service: SM2Service,
    private eventBus: EventBus,
  ) {}

  // 审查输出（6阶段管线）
  async inspect(output: RawOutput, contract: Contract): Promise<OutputResult> {
    const result = OutputResult.create(output, contract);

    // Stage 1: 格式验证
    result.addInspectionStage(await this.validateFormat(output));

    // Stage 2: DLP 扫描（敏感数据检测）
    result.addInspectionStage(await this.dlpScan(output, contract));

    // Stage 3: 差分隐私注入
    result.addInspectionStage(await this.injectDP(output, contract));

    // Stage 4: 数据量限制检查
    result.addInspectionStage(await this.checkVolumeLimit(output, contract));

    // Stage 5: 水印注入
    result.addInspectionStage(await this.injectWatermark(output));

    // Stage 6: SM2 签名
    result.addInspectionStage(await this.signOutput(output));

    // 根据所有阶段结果决定通过/拒绝
    if (result.allStagesPassed()) {
      result.approve();
      this.eventBus.publish(new OutputApproved(result.id, ...));
    } else {
      result.reject(result.getFirstFailureReason());
      this.eventBus.publish(new OutputRejected(result.id, ...));
    }

    return result;
  }
}

// ===== DPEngine =====
export class DPEngine {
  // Laplace 机制
  injectLaplace(data: number[], epsilon: number, sensitivity: number): number[];

  // Gaussian 机制
  injectGaussian(data: number[], epsilon: number, delta: number, sensitivity: number): number[];

  // 预算检查
  checkBudget(contractId: ContractId, requiredEpsilon: number): Promise<boolean>;
}
```

---

## 6. 审计域（Audit Domain）

### 6.1 领域模型

```typescript
// ===== 聚合根：AuditRecord =====
export class AuditRecord {
  private readonly id: AuditId;
  private eventType: AuditEventType;
  private actorId: PrincipalId;
  private resourceType: string;
  private resourceId: string;
  private action: string;
  private details: Record<string, any>;
  private ipAddress: string;
  private userAgent: string;
  private timestamp: Date;
  private merkleRoot: string | null;     // 区块链锚定后的 Merkle 根
  private blockNumber: number | null;

  // 验证存证完整性
  verifyIntegrity(): boolean;
}

// ===== 值对象：MerkleProof =====
export interface MerkleProof {
  leaf: string;                          // SM3(审计记录)
  path: string[];                        // Merkle 路径
  root: string;                          // Merkle 根
  blockNumber: number;                   // 区块号
  txHash: string;                        // 交易哈希
}

// ===== 枚举 =====
export enum AuditEventType {
  // 身份事件
  ORG_REGISTERED = 'org.registered',
  USER_LOGIN = 'user.login',
  USER_LOGOUT = 'user.logout',
  CERT_ISSUED = 'cert.issued',
  CERT_REVOKED = 'cert.revoked',

  // 合约事件
  CONTRACT_CREATED = 'contract.created',
  CONTRACT_SIGNED = 'contract.signed',
  CONTRACT_ACTIVATED = 'contract.activated',
  CONTRACT_SUSPENDED = 'contract.suspended',
  CONTRACT_TERMINATED = 'contract.terminated',

  // 沙箱事件
  SANDBOX_CREATED = 'sandbox.created',
  SANDBOX_STARTED = 'sandbox.started',
  SANDBOX_TERMINATED = 'sandbox.terminated',
  TASK_SUBMITTED = 'task.submitted',
  TASK_COMPLETED = 'task.completed',
  TASK_FAILED = 'task.failed',

  // 输出事件
  OUTPUT_INSPECTING = 'output.inspecting',
  OUTPUT_APPROVED = 'output.approved',
  OUTPUT_REJECTED = 'output.rejected',
  DP_BUDGET_CONSUMED = 'dp.budget.consumed',

  // 安全事件
  POLICY_VIOLATION = 'security.policy_violation',
  ANOMALY_DETECTED = 'security.anomaly_detected',
  KEY_REVOKED = 'security.key_revoked',
}

// ===== 领域事件 =====
export interface AuditRecorded {
  type: 'AuditRecorded';
  auditId: AuditId;
  eventType: AuditEventType;
  timestamp: Date;
}

export interface MerkleAnchored {
  type: 'MerkleAnchored';
  merkleRoot: string;
  blockNumber: number;
  recordCount: number;
  timestamp: Date;
}
```

### 6.2 应用服务

```typescript
// ===== AuditService =====
export class AuditService {
  constructor(
    private auditRepo: AuditRepository,
    private blockchainClient: BlockchainClient,
    private merkleTree: MerkleTreeService,
    private eventBus: EventBus,
  ) {}

  // 记录审计事件
  async record(event: AuditEvent): Promise<AuditId> {
    // 1. 创建审计记录
    // 2. 计算 SM3 哈希
    // 3. 添加到 Merkle 树
    // 4. 持久化到 ClickHouse
    // 5. 发布事件
  }

  // 批量锚定到区块链
  async anchorToBlockchain(): Promise<MerkleAnchored> {
    // 1. 获取未锚定的记录批次
    // 2. 计算 Merkle 根
    // 3. 调用 FISCO BCOS 智能合约存储
    // 4. 更新记录的 merkleRoot 和 blockNumber
  }

  // 验证审计记录完整性
  async verify(auditId: AuditId): Promise<VerificationResult> {
    // 1. 获取审计记录
    // 2. 重新计算 SM3 哈希
    // 3. 验证 Merkle 路径
    // 4. 验证区块链存证
  }

  // 查询审计日志
  async query(filter: AuditFilter, pagination: Pagination): Promise<AuditRecord[]> {
    // 支持按事件类型、时间范围、参与者等过滤
  }
}
```

---

## 7. 数据产品域（Data Product Domain）

### 7.1 领域模型

```typescript
// ===== 聚合根：DataProduct =====
export class DataProduct {
  private readonly id: DataProductId;
  private providerId: PrincipalId;
  private name: string;
  private description: string;
  private dataType: DataType;            // structured | semi_structured | unstructured
  private category: DataCategory;        // 行业分类
  private sensitivity: SensitivityLevel; // public | restricted | sensitive | core
  private status: DataProductStatus;
  private schema: DataSchema | null;     // 结构化数据的 Schema
  private files: DataFile[];             // 非结构化数据的文件列表
  private storageInfo: StorageInfo;      // 值对象
  private policy: UsagePolicy;           // 值对象
  private pricing: PricingModel;         // 值对象
  private metadata: ProductMetadata;     // 值对象

  // 领域方法
  publish(): void;
  unpublish(): void;
  updatePolicy(policy: UsagePolicy): void;
  addFile(file: DataFile): void;
  removeFile(fileId: string): void;
}

// ===== 值对象 =====
export enum DataType {
  STRUCTURED = 'structured',           // 数据库表、CSV、Parquet
  SEMI_STRUCTURED = 'semi_structured', // JSON、XML、日志
  UNSTRUCTURED = 'unstructured',       // 图像、文档、音视频
}

export enum SensitivityLevel {
  PUBLIC = 'public',
  RESTRICTED = 'restricted',
  SENSITIVE = 'sensitive',
  CORE = 'core',
}

export interface DataSchema {
  fields: SchemaField[];
  primaryKey: string[];
  indexes: string[];
  constraints: SchemaConstraint[];
}

export interface SchemaField {
  name: string;
  type: string;
  nullable: boolean;
  sensitivity: 'low' | 'medium' | 'high' | 'pii';
  description: string;
}

export interface StorageInfo {
  storageType: 'minio' | 'postgresql' | 'elasticsearch';
  objectName: string;                  // MinIO 对象名
  sizeBytes: number;
  checksum: string;                    // SM3 哈希
  encrypted: boolean;
  encryptionAlgorithm: 'SM4-GCM';
}

export interface PricingModel {
  type: 'per_query' | 'per_row' | 'subscription' | 'custom';
  price: number;
  currency: 'CNY';
  unit: string;
}

export interface DataFile {
  id: string;
  name: string;
  mimeType: string;
  sizeBytes: number;
  checksum: string;
  storageInfo: StorageInfo;
}

export interface UsagePolicy {
  allowedOperations: OperationType[];
  forbiddenOperations: OperationType[];
  outputConstraints: OutputConstraints;
  sandboxConfig: SandboxConfig;
  dpBudget: DPBudget;
}

// ===== 领域事件 =====
export interface DataProductPublished {
  type: 'DataProductPublished';
  productId: DataProductId;
  providerId: PrincipalId;
  dataType: DataType;
  timestamp: Date;
}

export interface DataProductUpdated {
  type: 'DataProductUpdated';
  productId: DataProductId;
  changes: string[];
  timestamp: Date;
}
```

### 7.2 应用服务

```typescript
// ===== DataProductService =====
export class DataProductService {
  constructor(
    private productRepo: DataProductRepository,
    private storageService: StorageService,
    private sm2Service: SM2Service,
    private eventBus: EventBus,
  ) {}

  // 注册数据产品
  async register(dto: RegisterProductDTO): Promise<DataProductId> {
    // 1. 验证提供商资质
    // 2. 上传数据文件（SM4-GCM 加密）
    // 3. 计算 SM3 校验和
    // 4. 提取元数据
    // 5. 创建数据产品实体
    // 6. 发布事件
  }

  // 查询数据目录
  async search(query: CatalogQuery, pagination: Pagination): Promise<DataProduct[]> {
    // 支持按数据类型、行业、敏感级别等过滤
  }

  // 获取数据产品详情
  async getDetail(productId: DataProductId): Promise<DataProductDetail> {
    // 包含 Schema、文件列表、定价、使用策略等
  }
}
```

---

## 8. 跨域集成与领域事件

### 8.1 事件总线架构

```typescript
// ===== EventBus 接口 =====
export interface EventBus {
  publish(event: DomainEvent): Promise<void>;
  subscribe(eventType: string, handler: EventHandler): void;
  unsubscribe(eventType: string, handler: EventHandler): void;
}

// ===== 事件处理器示例 =====
// 合约激活 → 初始化沙箱资源预留
export class ContractActivatedHandler implements EventHandler {
  async handle(event: ContractActivated): Promise<void> {
    // 预分配沙箱资源
    // 初始化 DP 预算跟踪器
  }
}

// 沙箱终止 → 触发最终输出审查
export class SandboxTerminatedHandler implements EventHandler {
  async handle(event: SandboxTerminated): Promise<void> {
    // 收集所有未审查的输出
    // 触发批量审查
  }
}

// 输出审查通过 → 记录审计日志
export class OutputApprovedHandler implements EventHandler {
  async handle(event: OutputApproved): Promise<void> {
    // 记录审计事件
    // 更新合约使用统计
  }
}

// DP 预算耗尽 → 暂停合约
export class DPBudgetExhaustedHandler implements EventHandler {
  async handle(event: DPBudgetExhausted): Promise<void> {
    // 暂停合约
    // 通知数商和买方
    // 记录安全审计
  }
}
```

### 8.2 事件流转图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          领域事件流转                                     │
│                                                                         │
│  合约域                    沙箱域                   输出管控域            │
│  ─────                    ─────                   ────────            │
│  ContractActivated ──────► SandboxCreated                               │
│                            │                                            │
│                            │ TaskCompleted ──────► OutputInspecting     │
│                            │                         │                  │
│                            │                         ▼                  │
│                            │                    OutputApproved          │
│                            │                         │                  │
│                            │                         ▼                  │
│  ContractTerminated ◄──────┤                    DPBudgetConsumed        │
│                            │                                            │
│                            ▼                                            │
│                       SandboxTerminated                                 │
│                                                                         │
│  审计域 ← 所有域事件（统一审计记录）                                       │
│                                                                         │
│  身份域 ← 组织注册/证书签发/吊销事件                                      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 9. 前端架构设计

### 9.1 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 框架 | React 18 + TypeScript | 与现有 SecurityManagementAgent 一致 |
| 状态管理 | Zustand | 轻量级，适合中型应用 |
| 路由 | React Router v6 | SPA 路由 |
| UI 组件 | Ant Design 5 | 企业级 UI 组件库 |
| 表格 | Ant Design Table + ProTable | 数据列表展示 |
| 表单 | Ant Design Form + ProForm | 复杂表单 |
| 图表 | ECharts / AntV | 监控仪表盘 |
| 请求 | Axios + React Query | API 请求 + 缓存 |
| 加密 | WebCrypto API + SM2 JS 库 | 客户端签名 |

### 9.2 页面结构

```
src/
├── pages/
│   ├── Dashboard/                    # 运营仪表盘
│   │   ├── index.tsx                 # 概览页面
│   │   ├── components/
│   │   │   ├── StatsCards.tsx        # 统计卡片
│   │   │   ├── ActivityChart.tsx     # 活动趋势图
│   │   │   └── AlertList.tsx         # 安全告警列表
│   │   └── hooks/
│   │       └── useDashboard.ts
│   │
│   ├── DataProducts/                 # 数据产品管理
│   │   ├── ProductList.tsx           # 产品列表
│   │   ├── ProductDetail.tsx         # 产品详情
│   │   ├── ProductCreate.tsx         # 创建产品
│   │   ├── ProductEdit.tsx           # 编辑产品
│   │   └── components/
│   │       ├── DataTypeSelector.tsx  # 数据类型选择器
│   │       ├── SchemaEditor.tsx      # Schema 编辑器
│   │       ├── PolicyConfig.tsx      # 使用策略配置
│   │       ├── FileUploader.tsx      # 文件上传
│   │       └── PricingConfig.tsx     # 定价配置
│   │
│   ├── Contracts/                    # 合约管理
│   │   ├── ContractList.tsx          # 合约列表
│   │   ├── ContractDetail.tsx        # 合约详情
│   │   ├── ContractCreate.tsx        # 创建合约
│   │   └── components/
│   │       ├── ContractTerms.tsx     # 条款展示
│   │       ├── SignaturePanel.tsx    # 签名面板
│   │       └── DPBudgetChart.tsx     # DP 预算图表
│   │
│   ├── Sandbox/                      # 沙箱管理
│   │   ├── SessionList.tsx           # 会话列表
│   │   ├── SessionDetail.tsx         # 会话详情
│   │   ├── CodeEditor.tsx            # 代码编辑器
│   │   ├── OutputViewer.tsx          # 输出查看器
│   │   └── components/
│   │       ├── ResourceMonitor.tsx   # 资源监控
│   │       ├── TaskTimeline.tsx      # 任务时间线
│   │       └── Terminal.tsx          # 终端模拟
│   │
│   ├── Audit/                        # 审计中心
│   │   ├── AuditLog.tsx              # 审计日志
│   │   ├── AuditDetail.tsx           # 日志详情
│   │   ├── Verification.tsx          # 存证验证
│   │   └── components/
│   │       ├── AuditFilter.tsx       # 过滤器
│   │       ├── MerkleTree.tsx        # Merkle 树可视化
│   │       └── BlockchainProof.tsx   # 区块链存证展示
│   │
│   ├── Identity/                     # 身份管理
│   │   ├── OrgList.tsx               # 组织列表
│   │   ├── OrgDetail.tsx             # 组织详情
│   │   ├── UserList.tsx              # 用户列表
│   │   └── components/
│   │       ├── CertificateCard.tsx   # 证书卡片
│   │       └── RoleSelector.tsx      # 角色选择器
│   │
│   └── OutputControl/                # 输出管控
│       ├── OutputList.tsx            # 输出记录列表
│       ├── OutputDetail.tsx          # 输出详情
│       └── components/
│           ├── InspectionPipeline.tsx # 审查管线可视化
│           ├── DLPReport.tsx         # DLP 扫描报告
│           └── WatermarkPreview.tsx  # 水印预览
│
├── stores/                           # Zustand 状态管理
│   ├── authStore.ts                  # 认证状态
│   ├── contractStore.ts              # 合约状态
│   ├── sandboxStore.ts               # 沙箱状态
│   └── auditStore.ts                 # 审计状态
│
├── services/                         # API 服务层
│   ├── api.ts                        # Axios 实例
│   ├── contractApi.ts                # 合约 API
│   ├── sandboxApi.ts                 # 沙箱 API
│   ├── dataProductApi.ts             # 数据产品 API
│   ├── auditApi.ts                   # 审计 API
│   └── identityApi.ts                # 身份 API
│
├── hooks/                            # 自定义 Hooks
│   ├── useAuth.ts                    # 认证 Hook
│   ├── useContract.ts                # 合约 Hook
│   ├── useSandbox.ts                 # 沙箱 Hook
│   └── useAudit.ts                   # 审计 Hook
│
└── utils/                            # 工具函数
    ├── crypto.ts                     # SM2/SM3/SM4 客户端工具
    ├── format.ts                     # 格式化工具
    └── validation.ts                 # 表单验证
```

### 9.3 核心组件设计

```typescript
// ===== CodeEditor 组件 =====
// 基于 Monaco Editor，支持 Python/SQL/R 语法高亮
interface CodeEditorProps {
  language: 'python' | 'sql' | 'r';
  value: string;
  onChange: (value: string) => void;
  onSubmit: (code: string) => void;
  readOnly?: boolean;
  sandboxType?: SandboxType;
}

// ===== InspectionPipeline 组件 =====
// 6阶段审查管线可视化
interface InspectionPipelineProps {
  stages: InspectionStage[];
  currentStage: InspectionStageType;
}

// ===== ResourceMonitor 组件 =====
// 实时资源使用监控（CPU/内存/磁盘/网络）
interface ResourceMonitorProps {
  sessionId: SessionId;
  refreshInterval?: number;
}

// ===== DPBudgetChart 组件 =====
// 差分隐私预算消耗可视化
interface DPBudgetChartProps {
  contractId: ContractId;
  totalEpsilon: number;
  usedEpsilon: number;
}
```

---

## 10. API 设计规范

### 10.1 RESTful API 设计

```yaml
# ===== 合约 API =====
POST   /api/v1/contracts                    # 创建合约
GET    /api/v1/contracts                    # 查询合约列表
GET    /api/v1/contracts/:id                # 获取合约详情
POST   /api/v1/contracts/:id/sign           # 签名合约
POST   /api/v1/contracts/:id/activate       # 激活合约
POST   /api/v1/contracts/:id/suspend        # 暂停合约
POST   /api/v1/contracts/:id/terminate      # 终止合约

# ===== 数据产品 API =====
POST   /api/v1/data-products                # 注册数据产品
GET    /api/v1/data-products                # 查询数据目录
GET    /api/v1/data-products/:id            # 获取产品详情
PUT    /api/v1/data-products/:id            # 更新产品
DELETE /api/v1/data-products/:id            # 删除产品
POST   /api/v1/data-products/:id/files      # 上传数据文件

# ===== 沙箱 API =====
POST   /api/v1/sandbox-sessions             # 创建沙箱会话
GET    /api/v1/sandbox-sessions             # 查询会话列表
GET    /api/v1/sandbox-sessions/:id         # 获取会话详情
POST   /api/v1/sandbox-sessions/:id/start   # 启动会话
POST   /api/v1/sandbox-sessions/:id/stop    # 停止会话
POST   /api/v1/sandbox-sessions/:id/tasks   # 提交任务
GET    /api/v1/sandbox-sessions/:id/tasks   # 查询任务列表
GET    /api/v1/sandbox-sessions/:id/tasks/:taskId  # 获取任务详情
GET    /api/v1/sandbox-sessions/:id/output  # 获取输出结果

# ===== 审计 API =====
GET    /api/v1/audit-logs                   # 查询审计日志
GET    /api/v1/audit-logs/:id               # 获取日志详情
POST   /api/v1/audit-logs/:id/verify        # 验证存证完整性

# ===== 身份 API =====
POST   /api/v1/organizations                # 注册组织
GET    /api/v1/organizations                # 查询组织列表
GET    /api/v1/organizations/:id            # 获取组织详情
POST   /api/v1/auth/login                   # 登录
POST   /api/v1/auth/logout                  # 登出
GET    /api/v1/auth/me                      # 获取当前用户
```

### 10.2 SSE 事件流

```typescript
// 沙箱会话实时事件流
GET /api/v1/sandbox-sessions/:id/events

// 事件类型：
interface SandboxEvent {
  type: 'status_change' | 'task_progress' | 'resource_update' | 'output_ready' | 'error';
  data: any;
  timestamp: string;
}

// 示例：
// data: {"type":"task_progress","data":{"taskId":"xxx","progress":0.75,"message":"执行中..."}}
// data: {"type":"output_ready","data":{"outputId":"xxx","status":"approved"}}
```

---

## 11. 数据库设计

### 11.1 PostgreSQL Schema

```sql
-- ===== 合约表 =====
CREATE TABLE contracts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id UUID NOT NULL REFERENCES data_products(id),
  provider_id UUID NOT NULL REFERENCES principals(id),
  consumer_id UUID NOT NULL REFERENCES principals(id),
  status VARCHAR(20) NOT NULL DEFAULT 'draft',
  terms JSONB NOT NULL,
  signatures JSONB DEFAULT '[]',
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_contracts_status ON contracts(status);
CREATE INDEX idx_contracts_provider ON contracts(provider_id);
CREATE INDEX idx_contracts_consumer ON contracts(consumer_id);
CREATE INDEX idx_contracts_expires ON contracts(expires_at);

-- ===== 数据产品表 =====
CREATE TABLE data_products (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_id UUID NOT NULL REFERENCES principals(id),
  name VARCHAR(255) NOT NULL,
  description TEXT,
  data_type VARCHAR(20) NOT NULL,
  category VARCHAR(50),
  sensitivity VARCHAR(20) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'draft',
  schema JSONB,
  storage_info JSONB NOT NULL,
  policy JSONB NOT NULL,
  pricing JSONB,
  metadata JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_data_products_provider ON data_products(provider_id);
CREATE INDEX idx_data_products_type ON data_products(data_type);
CREATE INDEX idx_data_products_sensitivity ON data_products(sensitivity);

-- ===== 沙箱会话表 =====
CREATE TABLE sandbox_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contract_id UUID NOT NULL REFERENCES contracts(id),
  data_product_id UUID NOT NULL REFERENCES data_products(id),
  consumer_id UUID NOT NULL REFERENCES principals(id),
  sandbox_type VARCHAR(20) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'provisioning',
  runtime_info JSONB,
  resource_usage JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  started_at TIMESTAMP WITH TIME ZONE,
  terminated_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_sessions_contract ON sandbox_sessions(contract_id);
CREATE INDEX idx_sessions_consumer ON sandbox_sessions(consumer_id);
CREATE INDEX idx_sessions_status ON sandbox_sessions(status);

-- ===== 任务表 =====
CREATE TABLE tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES sandbox_sessions(id),
  type VARCHAR(20) NOT NULL,
  code TEXT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  result JSONB,
  started_at TIMESTAMP WITH TIME ZONE,
  completed_at TIMESTAMP WITH TIME ZONE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_tasks_session ON tasks(session_id);
CREATE INDEX idx_tasks_status ON tasks(status);

-- ===== 输出结果表 =====
CREATE TABLE output_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES sandbox_sessions(id),
  task_id UUID NOT NULL REFERENCES tasks(id),
  contract_id UUID NOT NULL REFERENCES contracts(id),
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  inspection_stages JSONB DEFAULT '[]',
  final_content_hash VARCHAR(64),
  metadata JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_outputs_session ON output_results(session_id);
CREATE INDEX idx_outputs_contract ON output_results(contract_id);

-- ===== DP 预算跟踪表 =====
CREATE TABLE dp_budgets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contract_id UUID NOT NULL REFERENCES contracts(id) UNIQUE,
  total_epsilon DECIMAL(10, 4) NOT NULL,
  used_epsilon DECIMAL(10, 4) NOT NULL DEFAULT 0,
  delta DECIMAL(20, 16) NOT NULL,
  mechanism VARCHAR(20) NOT NULL,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ===== 身份表 =====
CREATE TABLE principals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  type VARCHAR(20) NOT NULL,  -- 'organization' | 'user'
  name VARCHAR(255) NOT NULL,
  credit_code VARCHAR(50),    -- 统一社会信用代码（组织）
  organization_id UUID REFERENCES principals(id),  -- 所属组织（用户）
  role VARCHAR(30) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending_verification',
  certificates JSONB DEFAULT '[]',
  contact_info JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_principals_type ON principals(type);
CREATE INDEX idx_principals_org ON principals(organization_id);
CREATE INDEX idx_principals_credit_code ON principals(credit_code);
```

### 11.2 ClickHouse 审计表

```sql
-- ===== 审计日志表（ClickHouse） =====
CREATE TABLE audit_logs (
  id UUID,
  event_type String,
  actor_id UUID,
  resource_type String,
  resource_id UUID,
  action String,
  details String,  -- JSON
  ip_address String,
  user_agent String,
  timestamp DateTime64(3),
  merkle_root Nullable(String),
  block_number Nullable(UInt64)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, event_type, actor_id)
TTL timestamp + INTERVAL 365 DAY;
```

---

## 12. 实现优先级与迭代计划

### Sprint 1：基础框架（2周）

| 任务 | 描述 | 优先级 |
|------|------|--------|
| T1-01 | 项目脚手架（FastAPI + React + PostgreSQL） | P0 |
| T1-02 | 身份域基础（组织注册 + 用户管理 + SM2 证书） | P0 |
| T1-03 | 认证授权（JWT + RBAC + SM2 签名验证） | P0 |
| T1-04 | 数据产品 CRUD（结构化数据基础） | P0 |
| T1-05 | 前端基础框架（路由 + 布局 + 认证） | P0 |

### Sprint 2：核心引擎（2周）

| 任务 | 描述 | 优先级 |
|------|------|--------|
| T2-01 | 合约域（合约创建 + 签名 + 策略引擎） | P0 |
| T2-02 | 沙箱域（L3 bwrap 沙箱 + 任务执行） | P0 |
| T2-03 | 输出管控域（格式验证 + DLP 扫描 + 水印） | P0 |
| T2-04 | 审计域（审计记录 + ClickHouse 存储） | P0 |
| T2-05 | 前端（合约管理 + 沙箱操作 + 输出查看） | P0 |

### Sprint 3：高级功能（2周）

| 任务 | 描述 | 优先级 |
|------|------|--------|
| T3-01 | 差分隐私引擎（Laplace/Gaussian + 预算管理） | P1 |
| T3-02 | 区块链存证（FISCO BCOS + Merkle 树） | P1 |
| T3-03 | L2 沙箱（Firecracker + gVisor） | P1 |
| T3-04 | 非结构化数据处理（图像/文档/音视频） | P1 |
| T3-05 | 前端（审计中心 + 输出管控 + 监控仪表盘） | P1 |

### Sprint 4：完善与优化（2周）

| 任务 | 描述 | 优先级 |
|------|------|--------|
| T4-01 | L1 TEE 沙箱（Occlum + SGX 远程证明） | P2 |
| T4-02 | 半结构化数据支持（JSON/XML/日志处理） | P2 |
| T4-03 | 数据产品开发沙箱（结构化/非结构化/半结构化） | P2 |
| T4-04 | E2E 测试（全流程场景验证） | P1 |
| T4-05 | 性能优化 + 安全加固 | P1 |

---

## 附录 A：与现有 SecurityManagementAgent 的集成点

| CDS 模块 | SMA 对应模块 | 集成方式 |
|----------|-------------|---------|
| 沙箱执行 | SandboxExecutor + CodeExecutionService | 复用 bwrap 沙箱，扩展 L2/L3 |
| 身份认证 | auth.ts + authenticateAPI.ts | 扩展 SM2 证书认证 |
| 审计日志 | Prisma AuditLog 模型 | 扩展 ClickHouse 存储 |
| 输出审查 | 无（新建） | 全新实现 |
| 差分隐私 | 无（新建） | 全新实现 |
| 区块链存证 | 无（新建） | 全新实现 |
| 前端框架 | React + TypeScript + Tailwind | 复用现有技术栈 |

## 附录 B：关键设计决策记录

| 决策 | 理由 | 备选方案 |
|------|------|---------|
| 选择 OPA 而非自研策略引擎 | 成熟开源、Rego 语言表达力强、CNCF 毕业项目 | Casbin（更轻量但功能有限） |
| ClickHouse 存储审计日志 | 列式存储、高写入吞吐、适合时序分析 | Elasticsearch（更通用但资源消耗大） |
| FISCO BCOS 而非以太坊 | 国密支持、联盟链更适合企业场景、国内合规 | Hyperledger Fabric（更通用但国密支持弱） |
| Zustand 而非 Redux | 轻量级、TypeScript 友好、适合中型应用 | Redux Toolkit（更重但生态更大） |
| Ant Design 而非自研 | 企业级组件完善、中文支持好、与现有系统一致 | shadcn/ui（更灵活但需要更多自定义） |
