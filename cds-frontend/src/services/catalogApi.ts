import api from './api';
import type { PaginatedResponse } from '../types/api';

export interface CatalogProduct {
  id: string;
  name: string;
  description: string | null;
  product_type: string;
  industry: string | null;
  security_level: string | null;
  allowed_operations: string[] | null;
  output_constraints: Record<string, unknown> | null;
  row_count: number | null;
  data_schema: Record<string, unknown> | null;
  provider_id: string;
  created_at: string;
  updated_at?: string;
}

export interface CatalogSearchParams {
  q?: string;
  product_type?: string;
  industry?: string;
  security_level?: string;
  page?: number;
  page_size?: number;
}

export const catalogApi = {
  search: (params?: CatalogSearchParams): Promise<PaginatedResponse<CatalogProduct>> => {
    const { page, page_size, ...rest } = params || {};
    return api.get('/catalog', { params: { ...rest, skip: ((page || 1) - 1) * (page_size || 20), limit: page_size || 20 } });
  },

  get: (id: string): Promise<CatalogProduct> =>
    api.get(`/catalog/${id}`),
};
