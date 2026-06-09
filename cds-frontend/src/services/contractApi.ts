import api from './api';
import type { Contract } from '../types/models';
import type { PaginatedResponse } from '../types/api';

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
}

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

  sign: (id: string, signature: string): Promise<Contract> =>
    api.post(`/contracts/${id}/sign`, { signature }),

  activate: (id: string): Promise<Contract> =>
    api.post(`/contracts/${id}/activate`),

  terminate: (id: string): Promise<Contract> =>
    api.post(`/contracts/${id}/terminate`),

  getDPBudget: (id: string): Promise<DPBudgetStatus> =>
    api.get(`/contracts/${id}/dp-budget`),
};
