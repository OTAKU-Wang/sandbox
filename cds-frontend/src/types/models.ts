import type {
  UserRole,
  ContractStatus,
  ContractType,
  SandboxLevel,
  SandboxSessionStatus,
  ProductType,
  ProductStatus,
} from './enums';

export interface User {
  id: string;
  username: string;
  email: string;
  role: UserRole;
  organization: string | null;
}

export interface DataProduct {
  id: string;
  name: string;
  description: string | null;
  product_type: ProductType;
  industry: string | null;
  status: ProductStatus;
  provider_id: string;
  resource_id: string | null;
  row_count: number | null;
  data_schema: Record<string, unknown> | null;
  security_level: string | null;
  allowed_operations: string[] | null;
  output_constraints: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface Contract {
  id: string;
  contract_no: string;
  contract_type: ContractType;
  status: ContractStatus;
  buyer_id: string;
  provider_id: string;
  product_ids: string[];
  title: string;
  terms: Record<string, unknown> | null;
  allowed_sandbox_levels: string | null;
  allowed_sandbox_modes: string[] | null;
  allowed_operations: string | null;
  max_duration_hours: number;
  dp_epsilon_budget: number | null;
  max_output_rows: number;
  allowed_output_formats: string | null;
  inspection_rule_set: Record<string, unknown> | null;
  provider_signed_at: string | null;
  buyer_signed_at: string | null;
  provider_signature: string | null;
  buyer_signature: string | null;
  platform_signature: string | null;
  platform_signed_at: string | null;
  blockchain_tx_hash: string | null;
  created_at: string;
  updated_at: string;
}

export interface SandboxSession {
  id: string;
  user_id: string;
  data_product_id: string;
  contract_id: string | null;
  sandbox_level: SandboxLevel;
  sandbox_mode: string;
  status: SandboxSessionStatus;
  container_id: string | null;
  session_key_id: string | null;
  timeout_seconds: number;
  resource_limits: Record<string, unknown> | null;
  error_message: string | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
  updated_at: string;
}
