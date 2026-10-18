import api from './api';
import { unwrapList } from '../utils/unwrapList';

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
  /** unknown = 后端没给出可判定的过期时间：界面必须呈现「无法确认」，不得显示绿色「有效」。 */
  status: 'active' | 'revoked' | 'expired' | 'unknown';
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
  /** null = 后端响应无法判定（缺少 valid 字段等）——既不是「通过」也不是「不通过」。 */
  valid: boolean | null;
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

/**
 * 过期时间不可解析/缺失时判为 unknown。
 * 旧实现 `Number.isNaN(...) || expiresAt >= now ? 'active' : 'expired'` 把「无法判定」
 * 直接判成有效（fail-open）；证书是否有效只能由可解析的 not_after 决定。
 */
const certStatus = (notAfter: string | null | undefined, now: Date): Certificate['status'] => {
  if (!notAfter) return 'unknown';
  const expiresAt = new Date(notAfter);
  if (Number.isNaN(expiresAt.getTime())) return 'unknown';
  return expiresAt >= now ? 'active' : 'expired';
};

const normalizeCertificate = (cert: CertificateResponse): CertificateDetail => {
  const status = certStatus(cert.not_after, new Date());
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
  // 规范形态 {items}（app/api/kms.py:104 list_deks）
  listKeys: (params?: { key_type?: string; status?: string }): Promise<KmsKey[]> =>
    api.get('/kms/keys', { params }).then((res) =>
      unwrapList<KmsKeyResponse>(res, '/kms/keys', 'items').map(normalizeKmsKey),
    ),

  createKey: (data: { product_id: string }): Promise<KmsKey> =>
    api.post('/kms/keys', null, { params: { product_id: data.product_id } }).then((res) => normalizeKmsKey(res as unknown as KmsKeyResponse)),

  rotateKey: (keyId: string): Promise<{ old_key_id: string; new_key_id: string; status: string }> =>
    api.post(`/kms/keys/${keyId}/rotate`),

  revokeKey: (keyId: string, data: HighRiskOperationPayload): Promise<{ key_id: string; status: string; terminated_sessions: number }> =>
    api.delete(`/kms/keys/${keyId}`, { data }),

  getKeyAudit: (keyId: string): Promise<KeyAuditEntry[]> =>
    // 规范形态 {items}（app/api/kms.py:238 get_key_audit）；旧实现只认包装形态，
    // 换成裸数组载荷就静默返回空审计轨迹。
    api.get('/kms/audit', { params: { key_id: keyId } }).then((res) =>
      unwrapList<KmsAuditResponse>(res, '/kms/audit', 'items').map((entry) => ({
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
  // 规范形态是裸数组（app/api/certificates.py:78 list_certificates）；旧实现遇到
  // 包装形态直接抛 `.map is not a function`。
  listCertificates: (params?: { status?: string }): Promise<Certificate[]> =>
    api.get('/certificates/list', { params }).then((res) =>
      unwrapList<CertificateResponse>(res, '/certificates/list', 'array').map(normalizeCertificate),
    ),

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
          // 规范形态（app/services/tlcp_service.py:211 verify_certificate）：
          //   {valid, cert_id, subject, expired, not_yet_valid, fingerprint} 或 {valid: false, error}
          const data = (res ?? {}) as CertificateVerifyResponse;
          const errors = data.errors || (data.error ? [data.error] : []);
          // 旧实现 `data.valid !== false` 是 fail-open：任何不含 valid 字段的响应
          // （契约漂移、网关替身）都会被渲染成「证书链验证通过」绿灯。
          const verdict: boolean | null = data.error ? false : typeof data.valid === 'boolean' ? data.valid : null;
          if (verdict === null) {
            const keys = Object.keys(data);
            return {
              valid: null,
              chain: [],
              errors: [...errors, `验证响应缺少 valid 字段${keys.length ? `（收到字段：${keys.slice(0, 6).join(', ')}）` : ''}，无法确认证书链状态`],
            };
          }
          return ({
            valid: verdict,
            chain: [{ subject: data.subject || cert.subject, issuer: data.issuer || cert.issuer, valid: verdict }],
            errors,
          });
        }),
    ),
};
