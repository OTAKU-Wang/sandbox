export const UserRole = {
  DATA_PROVIDER: 'data_provider',
  BUYER: 'buyer',
  OPERATOR: 'operator',
  REGULATOR: 'regulator',
  ADMIN: 'admin',
} as const;
export type UserRole = typeof UserRole[keyof typeof UserRole];

export const ContractStatus = {
  DRAFT: 'draft',
  NEGOTIATING: 'negotiating',
  SIGNED: 'signed',
  ACTIVE: 'active',
  SUSPENDED: 'suspended',
  COMPLETED: 'completed',
  VIOLATED: 'violated',
  ARCHIVED: 'archived',
} as const;
export type ContractStatus = typeof ContractStatus[keyof typeof ContractStatus];

export const ContractType = {
  DATA_QUERY: 'data_query',
  MODEL_TRAINING: 'model_training',
  DATA_APPLICATION: 'data_application',
  API_SERVICE: 'api_service',
  JOINT_COMPUTE: 'joint_compute',
  PRODUCT_DEV: 'product_dev',
  DATA_MODELING: 'data_modeling',
} as const;
export type ContractType = typeof ContractType[keyof typeof ContractType];

export const SandboxLevel = {
  L2C: 'l2c',
} as const;

// SS-13 收敛后唯一可选的沙箱级别 = CubeSandbox 微虚拟机（l2c）。
// 旧级别不再出现在任何创建表单里，但它们仍存在于存量会话/合约中，
// 所以取值域保留历史值（否则旧数据在列表/详情里会变成不可比较、不可渲染的类型错误）。
export const LEGACY_SANDBOX_LEVELS = ['L0', 'L1', 'L2', 'L3', 'k8s'] as const;
export type LegacySandboxLevel = typeof LEGACY_SANDBOX_LEVELS[number];

// `string & {}` 的作用：保留 l2c/历史值的字面量补全，同时容忍后端新回传的未知级别。
export type SandboxLevel =
  | typeof SandboxLevel[keyof typeof SandboxLevel]
  | LegacySandboxLevel
  | (string & {});

export const SANDBOX_LEVEL_LABELS: Record<string, string> = {
  l2c: "L2' MicroVM (CubeSandbox)",
  L0: 'L0 进程隔离（历史）',
  L1: 'L1 TEE（历史）',
  L2: 'L2 Firecracker（历史）',
  L3: 'L3 bwrap 隔离（历史）',
  k8s: 'K8s Pod（历史）',
};

export function sandboxLevelLabel(level?: string | null): string {
  if (!level) return '未知级别';
  return SANDBOX_LEVEL_LABELS[level] ?? `${level}（历史）`;
}

export function isLegacySandboxLevel(level?: string | null): boolean {
  return (LEGACY_SANDBOX_LEVELS as readonly string[]).includes(level ?? '');
}

export const SandboxSessionStatus = {
  PENDING: 'pending',
  KEY_DISTRIBUTING: 'key_distributing',
  READY: 'ready',
  PROVISIONING: 'provisioning',
  RUNNING: 'running',
  SUSPENDED: 'suspended',
  COMPLETED: 'completed',
  FAILED: 'failed',
  TERMINATED: 'terminated',
  REVOKED: 'revoked',
} as const;
export type SandboxSessionStatus = typeof SandboxSessionStatus[keyof typeof SandboxSessionStatus];

export const TaskType = {
  QUERY: 'query',
  TRAIN: 'train',
  ANALYZE: 'analyze',
  EXPORT: 'export',
  CUSTOM: 'custom',
  RAG_QUERY: 'rag_query',
} as const;
export type TaskType = typeof TaskType[keyof typeof TaskType];

export const ProductType = {
  STRUCTURED: 'structured',
  UNSTRUCTURED: 'unstructured',
  SEMI_STRUCTURED: 'semi-structured',
  API: 'api',
} as const;
export type ProductType = typeof ProductType[keyof typeof ProductType];

export const SensitivityLevel = {
  CORE: 'core',
  SENSITIVE: 'sensitive',
  RESTRICTED: 'restricted',
  PUBLIC: 'public',
} as const;
export type SensitivityLevel = typeof SensitivityLevel[keyof typeof SensitivityLevel];

export const ProductStatus = {
  DRAFT: 'draft',
  REVIEWING: 'reviewing',
  APPROVED: 'approved',
  PUBLISHED: 'published',
  SUSPENDED: 'suspended',
  ARCHIVED: 'archived',
} as const;
export type ProductStatus = typeof ProductStatus[keyof typeof ProductStatus];
