import api from './api';
// A311：上传上限是后端的 500MB（app/api/data_resources.py:MAX_UPLOAD_SIZE），全局 30s
// （services/api.ts）在大文件上必先把请求掐了。超时口径唯一来源见 services/timeouts.ts。
import { UPLOAD_TIMEOUT_MS } from './timeouts';
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

/** Task 7 自动探查的单列结果。 */
export interface AutoProfileColumn {
  field: string;
  pii_type: string | null;
  severity: string | null;
  /** 命中来自哪一层：regex 是确定性证据、ner/lac/transformers 不是。 */
  layer: string | null;
  confidence: number | null;
  /** 1=公开 … 4=机密；null = 本列无敏感证据。 */
  classification_level: number | null;
  /** 该列分级是否进入出域管控（level ≥ 3）。 */
  enforced: boolean;
  evidence_rows: number;
  samples_checked: number;
  note: string | null;
  pii_types: Array<{
    pii_type: string; severity: string; layer: string; level: number; count: number; confidence: number;
  }>;
}

export interface AutoProfileResult {
  resource_id: string;
  format: string;
  /** pii_ner 实际跑的引擎（环境缺 LAC 时会如实降到 rule）。 */
  engine: string;
  scanned_rows: number;
  scanned_bytes: number;
  truncated: boolean;
  columns_sampled_per_field: number;
  sensitivity_level: 'high' | 'medium' | 'low';
  classification_level: number | null;
  columns: AutoProfileColumn[];
  field_classifications: Record<string, number>;
  enforcement_preview: { mask_fields: string[]; deny_out_fields: string[] };
  persisted: boolean;
}

/**
 * Task 7：一键自动分类分级探查。识别全部复用后端既有的 app/services/pii_ner（前 500 行的
 * 取样值逐列送检），前端不参与任何识别判定。
 *
 * 结论会写回资源实体的 field_classifications —— 那同时是出域管控的输入（level≥3 强制
 * 掩码、≥4 禁止出域），所以响应带了 enforcement_preview，UI 必须把它说出来。
 */
export function autoProfileResource(id: string): Promise<AutoProfileResult> {
  return api.post(`/data-resources/${id}/auto-profile`);
}

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
    return api.post('/data-resources/upload', formData, { timeout: UPLOAD_TIMEOUT_MS });
  },

  delete: (id: string): Promise<DeleteResourceResult> =>
    api.delete(`/data-resources/${id}`),

  sample: (id: string, params: { sample_size?: number; method?: string }): Promise<unknown> =>
    api.post(`/data-resources/${id}/sample`, null, { params }),

  desensitize: (id: string): Promise<DesensitizeResult> =>
    api.post(`/data-resources/${id}/desensitize`),

  autoProfile: autoProfileResource,
};
