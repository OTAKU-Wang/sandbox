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
  L1: 'L1',
  L2: 'L2',
  L3: 'L3',
  K8S: 'k8s',
} as const;
export type SandboxLevel = typeof SandboxLevel[keyof typeof SandboxLevel];

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
