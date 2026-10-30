import api from './api';
import type { Contract } from '../types/models';
import type { PaginatedResponse } from '../types/api';
import type { HighRiskOperationPayload } from './identityApi';

export interface CreateContractRequest {
  contract_type: string;
  buyer_id: string;
  product_ids: string[];
  title: string;
  terms?: Record<string, unknown>;
  allowed_sandbox_levels?: string;
  allowed_sandbox_modes?: string[];
  allowed_operations?: string;
  max_duration_hours?: number;
  dp_epsilon_budget?: number;
  max_output_rows?: number;
  allowed_output_formats?: string;
  inspection_rule_set?: Record<string, unknown>;
  // A314 Gap A4 目的限定（purpose limitation）：后端 ContractCreate 就接这两个字段
  // （app/schemas/contract.py:30-31）、创建时直接落库（app/api/contracts.py:85-86）。
  // 不出口它们 = 从 UI 建的合约全部 purpose=null，于是只针对「带 purpose 的合约」生效的
  // 目的门（app/api/sandbox_tasks.py Gap A4）在界面上永远不可达。
  // 留空时这两个键根本不该出现在载荷里（见 ContractCreate 的条件展开）：
  // 与「故意交 null」区分开，也让后端的「不传 = 不做目的限定」口径保持原样。
  purpose?: string;
  purpose_scope?: string[];
}

/**
 * A317：后端 `GET /contracts/{id}/dp-budget` 的 DP 消费流水只回最近 20 条
 * （app/api/contracts.py:302 `for e in ledger[:20]`），而且响应体里**没有** total/has_more。
 * 所以「entries 满 20 条」就是被截断的信号，页面必须显式说明这一上限，
 * 不许把 20 行当全量台账。后端改这个上限时必须连带改这里与页面的截断说明。
 */
export const DP_LEDGER_ENTRY_LIMIT = 20;

export interface DPBudgetStatus {
  contract_id: string;
  total_epsilon: number;
  remaining_epsilon: number;
  consumed_epsilon: number;
  entries: Array<{
    session_id: string;
    epsilon_consumed: number;
    operation: string;
    timestamp: string;
  }>;
}

export const contractApi = {
  list: (params?: { page?: number; page_size?: number; status?: string; contract_type?: string }): Promise<PaginatedResponse<Contract>> =>
    api.get('/contracts', {
      params: {
        skip: ((params?.page ?? 1) - 1) * (params?.page_size ?? 20),
        limit: params?.page_size ?? 20,
        status: params?.status,
        contract_type: params?.contract_type,
      },
    }),

  get: (id: string): Promise<Contract> =>
    api.get(`/contracts/${id}`),

  create: (data: CreateContractRequest): Promise<Contract> =>
    api.post('/contracts', data),

  sign: (id: string, signature: string, timestamp: string): Promise<Contract> =>
    api.post(`/contracts/${id}/sign`, { signature, timestamp }),

  activate: (id: string): Promise<Contract> =>
    api.post(`/contracts/${id}/activate`),

  terminate: (id: string, data: HighRiskOperationPayload): Promise<Contract> =>
    api.post(`/contracts/${id}/terminate`, data),

  getDPBudget: (id: string): Promise<DPBudgetStatus> =>
    api.get(`/contracts/${id}/dp-budget`),
};
