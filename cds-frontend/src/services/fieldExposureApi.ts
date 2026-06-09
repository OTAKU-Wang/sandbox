import api from './api';

export type FieldSensitivity = 'public' | 'internal' | 'sensitive' | 'pii' | 'restricted';

export interface FieldRule {
  sensitivity: FieldSensitivity;
  mask_pattern?: string | null;
  auto_approve?: boolean;
  description?: string | null;
}

export interface FieldVisibilityConfig {
  id: string;
  product_id: string;
  field_rules: Record<string, FieldRule>;
  default_sensitivity: FieldSensitivity;
  created_at: string;
  updated_at: string;
}

export interface ExposureRequest {
  id: string;
  product_id: string;
  buyer_id: string;
  contract_id: string | null;
  requested_fields: string[];
  justification: string | null;
  approved_fields: string[] | null;
  rejection_reason: string | null;
  status: 'pending' | 'approved' | 'rejected' | 'revoked';
  reviewed_by: string | null;
  reviewed_at: string | null;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export const fieldExposureApi = {
  getVisibility: (productId: string): Promise<FieldVisibilityConfig> =>
    api.get(`/field-exposure/products/${productId}/field-visibility`),

  setVisibility: (
    productId: string,
    data: { default_sensitivity: FieldSensitivity; field_rules: Record<string, FieldRule> },
  ): Promise<FieldVisibilityConfig> =>
    api.put(`/field-exposure/products/${productId}/field-visibility`, data),

  createRequest: (data: { product_id: string; requested_fields: string[]; justification?: string }): Promise<ExposureRequest> =>
    api.post('/field-exposure/exposure-requests', data),

  listRequests: (params?: { product_id?: string; status?: string; as_provider?: boolean }): Promise<ExposureRequest[]> =>
    api.get('/field-exposure/exposure-requests', { params }),

  reviewRequest: (requestId: string, data: { approved_fields?: string[]; rejection_reason?: string }): Promise<ExposureRequest> =>
    api.post(`/field-exposure/exposure-requests/${requestId}/review`, data),

  revokeRequest: (requestId: string): Promise<ExposureRequest> =>
    api.post(`/field-exposure/exposure-requests/${requestId}/revoke`),

  getApprovedFields: (productId: string): Promise<{ product_id: string; buyer_id: string; approved_fields: string[]; total_approved: number }> =>
    api.get(`/field-exposure/products/${productId}/approved-fields`),
};
