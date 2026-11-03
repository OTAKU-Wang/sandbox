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

// 版本管理（SS-realignment Task 8）：版本号是整数（app/models/data_product.py 的
// version 列是 Integer，行级版本模型 parent_id + is_latest）。
export interface CreateProductVersionRequest {
  version?: number;
  schema_definition?: Record<string, unknown>;
  changelog?: string;
}

export interface CreatedProductVersion {
  id: string;
  version: number;
  parent_id: string;
  status: string;
  change_summary: string | null;
  data_schema: Record<string, unknown> | null;
}

export interface ProductVersionFieldChange {
  name: string;
  from_type: string | null;
  to_type: string | null;
  from_field: Record<string, unknown>;
  to_field: Record<string, unknown>;
}

export interface ProductVersionDiff {
  product_id: string;
  from_version: number;
  to_version: number;
  added_fields: string[];
  removed_fields: string[];
  modified_fields: ProductVersionFieldChange[];
  unchanged_fields: string[];
  breaking_change: boolean;
}

export interface ProductVersionRollbackResult {
  product_id: string;
  current_version: number;
  restored_product_id: string;
  rolled_back_from: number | null;
  already_latest: boolean;
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

  // 版本管理（Task 8）：追加版本草稿 / 两版本 Schema 比对 / 把当前版本回退到历史版本
  createVersion: (id: string, data: CreateProductVersionRequest): Promise<CreatedProductVersion> =>
    api.post(`/data-products/${id}/versions`, data),

  diffVersions: (id: string, fromVersion: number, toVersion: number): Promise<ProductVersionDiff> =>
    api.get(`/data-products/${id}/versions/diff`, { params: { from_version: fromVersion, to_version: toVersion } }),

  rollbackVersion: (id: string, targetVersion: number): Promise<ProductVersionRollbackResult> =>
    api.post(`/data-products/${id}/versions/rollback`, { target_version: targetVersion }),
};
