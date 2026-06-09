import api from './api';

// ─── KMS Key Types ───

export interface KmsKey {
  key_id: string;
  key_type: 'dek' | 'session' | 'kek';
  algorithm: string;
  status: 'active' | 'rotated' | 'revoked' | 'destroyed';
  created_at: string;
  expires_at: string | null;
}

export interface KeyAuditEntry {
  id: string;
  key_id: string;
  action: string;
  operator: string;
  detail: Record<string, unknown> | null;
  created_at: string;
}

// ─── Certificate Types ───

export interface Certificate {
  id: string;
  subject: string;
  issuer: string;
  serial_number: string;
  status: 'active' | 'revoked' | 'expired';
  valid_from: string;
  valid_to: string;
  algorithm: string;
}

export interface CertificateDetail extends Certificate {
  fingerprint: string;
  public_key: string;
  extensions: Record<string, unknown> | null;
  created_at: string;
}

export interface ChainVerifyResult {
  valid: boolean;
  chain: { subject: string; issuer: string; valid: boolean }[];
  errors: string[];
}

// ─── KMS Key API ───

export const kmsApi = {
  listKeys: (params?: { key_type?: string; status?: string }): Promise<KmsKey[]> =>
    api.get('/kms/keys', { params }),

  createKey: (data: { key_type: 'dek' | 'session' | 'kek'; algorithm?: string }): Promise<KmsKey> =>
    api.post('/kms/keys', data),

  rotateKey: (keyId: string): Promise<KmsKey> =>
    api.post(`/kms/keys/${keyId}/rotate`),

  revokeKey: (keyId: string): Promise<KmsKey> =>
    api.post(`/kms/keys/${keyId}/revoke`),

  getKeyAudit: (keyId: string): Promise<KeyAuditEntry[]> =>
    api.get(`/kms/keys/${keyId}/audit`),
};

// ─── Certificate API ───

export const certificateApi = {
  listCertificates: (params?: { status?: string }): Promise<Certificate[]> =>
    api.get('/certificates', { params }),

  getCertificate: (id: string): Promise<CertificateDetail> =>
    api.get(`/certificates/${id}`),

  generateCertificate: (data: { subject: string; algorithm?: string; valid_days?: number }): Promise<Certificate> =>
    api.post('/certificates/generate', data),

  revokeCertificate: (id: string): Promise<Certificate> =>
    api.post(`/certificates/${id}/revoke`),

  verifyChain: (id: string): Promise<ChainVerifyResult> =>
    api.post(`/certificates/${id}/verify`),
};
