import api from './api';

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

export const dataResourceApi = {
  list: async (params?: { page?: number; page_size?: number }): Promise<{ items: DataResource[]; total: number }> => {
    const allItems: DataResource[] = await api.get('/data-resources');
    const page = params?.page ?? 1;
    const pageSize = params?.page_size ?? 20;
    const start = (page - 1) * pageSize;
    return { items: allItems.slice(start, start + pageSize), total: allItems.length };
  },

  get: (id: string): Promise<DataResource> =>
    api.get(`/data-resources/${id}`),

  upload: (data: UploadResourceRequest): Promise<DataResource> => {
    const formData = new FormData();
    formData.append('file', data.file);
    formData.append('name', data.name);
    if (data.description) formData.append('description', data.description);
    return api.post('/data-resources/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },

  delete: (id: string): Promise<void> =>
    api.delete(`/data-resources/${id}`),

  sample: (id: string, params: { sample_size?: number; method?: string }): Promise<unknown> =>
    api.post(`/data-resources/${id}/sample`, null, { params }),

  desensitize: (id: string, params?: { sample_size?: number }): Promise<unknown> =>
    api.post(`/data-resources/${id}/desensitize`, null, { params }),
};
