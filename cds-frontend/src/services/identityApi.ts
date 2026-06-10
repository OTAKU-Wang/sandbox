import api from './api';

// ─── KMS Key Types ───

export interface KmsKey {
  key_id: string;
  key_type: 'dek' | 'session' | 'kek';
  product_id?: string;
  algorithm?: string;
  status: 'active' | 'rotated' | 'revoked' | 'destroyed';
  key_version?: number;
  usage_count?: number;
  max_usage?: number;
  created_at?: string;
  expires_at?: string | null;
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
  cert_type?: string;
  valid_from: string;
  valid_to: string;
  algorithm: string;
}

export interface CertificateDetail extends Certificate {
  fingerprint: string;
  public_key: string;
  cert_pem?: string;
  key_pem?: string;
  extensions: Record<string, unknown> | null;
  created_at: string;
}

export interface ChainVerifyResult {
  valid: boolean;
  chain: { subject: string; issuer: string; valid: boolean }[];
  errors: string[];
}

interface KmsKeyResponse {
  key_id: string;
  product_id?: string;
  status: KmsKey['status'];
  key_version?: number;
  algorithm?: string;
  usage_count?: number;
  max_usage?: number;
  created_at?: string;
  expires_at?: string | null;
}

interface KmsAuditResponse {
  id: string;
  key_id: string;
  operation: string;
  user_id: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}

interface CertificateResponse {
  cert_id: string;
  subject: string;
  issuer: string;
  serial_number: string;
  cert_type?: string;
  not_before: string;
  not_after: string;
  fingerprint: string;
  cert_pem?: string;
  key_pem?: string;
}

interface CertificateVerifyResponse {
  valid?: boolean;
  error?: string;
  errors?: string[];
  subject?: string;
  issuer?: string;
}

const normalizeKmsKey = (key: KmsKeyResponse): KmsKey => ({
  key_id: key.key_id,
  key_type: 'dek',
  product_id: key.product_id,
  algorithm: key.algorithm || 'SM4',
  status: key.status,
  key_version: key.key_version,
  usage_count: key.usage_count,
  max_usage: key.max_usage,
  created_at: key.created_at,
  expires_at: key.expires_at ?? null,
});

const normalizeCertificate = (cert: CertificateResponse): CertificateDetail => {
  const expiresAt = new Date(cert.not_after);
  const status: Certificate['status'] = Number.isNaN(expiresAt.getTime()) || expiresAt >= new Date() ? 'active' : 'expired';
  return {
    id: cert.cert_id,
    subject: cert.subject,
    issuer: cert.issuer,
    serial_number: cert.serial_number,
    status,
    cert_type: cert.cert_type,
    valid_from: cert.not_before,
    valid_to: cert.not_after,
    algorithm: cert.cert_type === 'encryption' ? 'SM2 encryption' : 'SM2 signing',
    fingerprint: cert.fingerprint,
    public_key: cert.cert_pem || '',
    cert_pem: cert.cert_pem,
    key_pem: cert.key_pem,
    extensions: null,
    created_at: cert.not_before,
  };
};

// ─── KMS Key API ───

export interface HighRiskOperationPayload {
  reason: string;
  ticket_id?: string | null;
}

export const kmsApi = {
  listKeys: (params?: { key_type?: string; status?: string }): Promise<KmsKey[]> =>
    api.get('/kms/keys', { params }).then((res) => {
      const data = res as unknown as { items?: KmsKeyResponse[] } | KmsKeyResponse[];
      return (Array.isArray(data) ? data : data.items || []).map(normalizeKmsKey);
    }),

  createKey: (data: { product_id: string }): Promise<KmsKey> =>
    api.post('/kms/keys', null, { params: { product_id: data.product_id } }).then((res) => normalizeKmsKey(res as unknown as KmsKeyResponse)),

  rotateKey: (keyId: string): Promise<{ old_key_id: string; new_key_id: string; status: string }> =>
    api.post(`/kms/keys/${keyId}/rotate`),

  revokeKey: (keyId: string, data: HighRiskOperationPayload): Promise<{ key_id: string; status: string; terminated_sessions: number }> =>
    api.delete(`/kms/keys/${keyId}`, { data }),

  getKeyAudit: (keyId: string): Promise<KeyAuditEntry[]> =>
    api.get('/kms/audit', { params: { key_id: keyId } }).then((res) =>
      ((res as unknown as { items?: KmsAuditResponse[] }).items || []).map((entry) => ({
        id: entry.id,
        key_id: entry.key_id,
        action: entry.operation,
        operator: entry.user_id || '-',
        detail: entry.detail,
        created_at: entry.created_at,
      })),
    ),
};

// ─── Certificate API ───

export const certificateApi = {
  listCertificates: (params?: { status?: string }): Promise<Certificate[]> =>
    api.get('/certificates/list', { params }).then((res) => (res as unknown as CertificateResponse[]).map(normalizeCertificate)),

  getCertificate: (id: string): Promise<CertificateDetail> =>
    api.get(`/certificates/${id}`).then((res) => normalizeCertificate(res as unknown as CertificateResponse)),

  generateCertificate: (data: {
    subject: string;
    cert_type?: 'signing' | 'encryption';
    validity_days?: number;
    organization?: string;
  }): Promise<Certificate> =>
    api.post('/certificates/generate', data).then((res) => normalizeCertificate(res as unknown as CertificateResponse)),

  revokeCertificate: (id: string, data: HighRiskOperationPayload): Promise<{ revoked: boolean; cert_id: string }> =>
    api.delete(`/certificates/${id}`, { data }),

  verifyChain: (id: string): Promise<ChainVerifyResult> =>
    certificateApi.getCertificate(id).then((cert) =>
      api.post('/certificates/verify', null, { params: { cert_pem: cert.cert_pem || cert.public_key } })
        .then((res) => {
          const data = res as unknown as CertificateVerifyResponse;
          return ({
            valid: data.valid !== false && !data.error,
            chain: [{ subject: data.subject || cert.subject, issuer: data.issuer || cert.issuer, valid: data.valid !== false && !data.error }],
            errors: data.errors || (data.error ? [data.error] : []),
          });
        }),
    ),
};
