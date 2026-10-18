import api from './api';
import type { PaginatedResponse } from '../types/api';

export interface DataResource {
  id: string;
  provider_id: string;
  name: string;
  description: string | null;
  resource_type: string;
  format: string;
  status: string;
  schema_fields: Array<Record<string, unknown>> | null;
  row_count: number | null;
  file_size_bytes: number | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface UploadResourceRequest {
  name: string;
  description?: string;
  file: File;
}

export interface DesensitizeResult {
  resource_id: string;
  format: string;
  data: string;
  row_count: number;
  masking_applied: boolean;
  /** A309：后端算出来的“本次真的掩了哪几列”；空数组 = 一个字段都没掩。 */
  masked_columns?: string[];
}

/**
 * A312：删除接口的响应体是有内容的，不是 void——UI 拿它分辨“记录删了”与
 * “密文对象/DEK 也真的销了”，不能一律当成功。
 */
export interface DeleteResourceResult {
  deleted: boolean;
  resource_id: string;
  storage_deleted: boolean;
  key_destroyed: boolean;
}

/**
 * A311：上传上限是后端的 500MB（app/api/data_resources.py:MAX_UPLOAD_SIZE），
 * 全局 30s（services/api.ts）在大文件上必先把请求掐了。逐调用放宽，口径同
 * flow.ts 的 TIMEOUT_*（网关 /api/ 的 proxy_read_timeout 就是 300s）。
 */
const TIMEOUT_UPLOAD_MS = 300_000;

export const dataResourceApi = {
  list: (params?: { page?: number; page_size?: number }): Promise<PaginatedResponse<DataResource>> =>
    // A310：分页交给后端做。`paginated=true` 才回 {items,total,page,page_size} 信封，
    // 不带该参数的调用方（含 cds_sdk.list_data_resources）拿到的仍是裸数组；
    // 旧实现是这里全量下载再 slice，为了一个 count 也要把 schema_fields 全拉回来。
    api.get('/data-resources/', {
      params: {
        paginated: true,
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 20,
      },
    }),

  get: (id: string): Promise<DataResource> =>
    api.get(`/data-resources/${id}`),

  upload: (data: UploadResourceRequest): Promise<DataResource> => {
    const formData = new FormData();
    formData.append('file', data.file);
    formData.append('name', data.name);
    if (data.description) formData.append('description', data.description);
    // Content-Type 必须留给浏览器：手写 'multipart/form-data' 会顶掉浏览器自动生成的
    // boundary，后端拿不到 file 部件（实测 422）。
    return api.post('/data-resources/upload', formData, { timeout: TIMEOUT_UPLOAD_MS });
  },

  delete: (id: string): Promise<DeleteResourceResult> =>
    api.delete(`/data-resources/${id}`),

  sample: (id: string, params: { sample_size?: number; method?: string }): Promise<unknown> =>
    api.post(`/data-resources/${id}/sample`, null, { params }),

  desensitize: (id: string): Promise<DesensitizeResult> =>
    api.post(`/data-resources/${id}/desensitize`),
};
