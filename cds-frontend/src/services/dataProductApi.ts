import api from './api';
import type { DataProduct } from '../types/models';
import type { PaginatedResponse } from '../types/api';

export interface CreateProductRequest {
  name: string;
  description?: string;
  product_type: string;
  resource_id?: string;
  industry?: string;
  row_count?: number;
  data_schema?: Record<string, unknown>;
  security_level?: string;
  allowed_operations?: string[];
  output_constraints?: Record<string, unknown>;
}

export interface UpdateProductRequest {
  name?: string;
  description?: string;
  industry?: string;
  data_schema?: Record<string, unknown>;
  security_level?: string;
  allowed_operations?: string[];
  output_constraints?: Record<string, unknown>;
}

export interface ProductVersionSummary {
  id: string;
  version: number;
  status: string;
  is_latest: boolean;
  change_summary: string | null;
  created_at: string;
}

export const dataProductApi = {
  list: (params?: { page?: number; page_size?: number; q?: string; product_type?: string; status?: string }): Promise<PaginatedResponse<DataProduct>> =>
    api.get('/data-products', {
      params: {
        skip: ((params?.page ?? 1) - 1) * (params?.page_size ?? 20),
        limit: params?.page_size ?? 20,
        q: params?.q,
        product_type: params?.product_type,
        status: params?.status,
      },
    }),

  get: (id: string): Promise<DataProduct> =>
    api.get(`/data-products/${id}`),

  create: (data: CreateProductRequest): Promise<DataProduct> =>
    api.post('/data-products', data),

  update: (id: string, data: UpdateProductRequest): Promise<DataProduct> =>
    api.put(`/data-products/${id}`, data),

  delete: (id: string): Promise<void> =>
    api.delete(`/data-products/${id}`),

  archive: (id: string): Promise<DataProduct> =>
    api.post(`/data-products/${id}/archive`),

  submit: (id: string): Promise<DataProduct> =>
    api.post(`/data-products/${id}/submit`),

  approve: (id: string): Promise<DataProduct> =>
    api.post(`/data-products/${id}/approve`),

  reject: (id: string): Promise<DataProduct> =>
    api.post(`/data-products/${id}/reject`),

  publish: (id: string): Promise<DataProduct> =>
    api.post(`/data-products/${id}/publish`),

  createNewVersion: (id: string, change_summary = ''): Promise<{ id: string; version: number; parent_id: string; status: string; change_summary: string }> =>
    api.post(`/data-products/${id}/new-version`, null, { params: { change_summary } }),

  listVersions: (id: string): Promise<{ product_id: string; versions: ProductVersionSummary[] }> =>
    api.get(`/data-products/${id}/versions`),
};
