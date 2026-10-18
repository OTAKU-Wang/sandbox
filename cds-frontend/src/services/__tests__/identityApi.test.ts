import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import api from '../api';
import { kmsApi, certificateApi } from '../identityApi';
import { ListShapeError } from '../../utils/unwrapList';

// 拦截器已把 response.data 解出来，所以这里 mock 的就是后端响应体本身。
const get = api.get as unknown as ReturnType<typeof vi.fn>;
const post = api.post as unknown as ReturnType<typeof vi.fn>;

const KMS_AUDIT_ROW = {
  id: 'a1',
  key_id: 'dek-1',
  operation: 'rotate',
  user_id: 'u1',
  detail: null,
  created_at: '2026-10-01T00:00:00',
};

const CERT_ROW = {
  cert_id: 'c1',
  subject: 'CN=cds,O=CDS,C=CN',
  issuer: 'CN=cds-ca',
  serial_number: '0A',
  cert_type: 'signing',
  not_before: '2026-01-01T00:00:00',
  not_after: '2099-01-01T00:00:00',
  fingerprint: 'fp',
  cert_pem: '-----BEGIN CERTIFICATE-----',
};

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});

describe('kmsApi.listKeys — 规范形态 {items}（app/api/kms.py:104 list_deks）', () => {
  it('解出 {items:[...]}', async () => {
    get.mockResolvedValue({ items: [{ key_id: 'dek-1', product_id: 'p1', status: 'active' }] });
    const res = await kmsApi.listKeys();
    expect(res).toHaveLength(1);
    expect(res[0].key_id).toBe('dek-1');
  });

  it('兼容裸数组', async () => {
    get.mockResolvedValue([{ key_id: 'dek-2', product_id: 'p1', status: 'active' }]);
    const res = await kmsApi.listKeys();
    expect(res.map((k) => k.key_id)).toEqual(['dek-2']);
  });

  it('无法识别的响应结构抛错，不静默当成「没有密钥」', async () => {
    get.mockResolvedValue({ keys: [] });
    await expect(kmsApi.listKeys()).rejects.toBeInstanceOf(ListShapeError);
  });
});

describe('kmsApi.getKeyAudit — 与 listKeys 同一约定（app/api/kms.py:238 get_key_audit 返回 {items}）', () => {
  it('规范形态 {items:[...]} 解出条目', async () => {
    get.mockResolvedValue({ items: [KMS_AUDIT_ROW] });
    const res = await kmsApi.getKeyAudit('dek-1');
    expect(res).toHaveLength(1);
    expect(res[0]).toMatchObject({ id: 'a1', action: 'rotate', operator: 'u1' });
  });

  it('裸数组载荷同样解出条目（旧实现静默返回空审计轨迹）', async () => {
    get.mockResolvedValue([KMS_AUDIT_ROW]);
    const res = await kmsApi.getKeyAudit('dek-1');
    expect(res).toHaveLength(1);
    expect(res[0].key_id).toBe('dek-1');
  });

  it('未识别载荷抛 ListShapeError，而不是空的 KMS 审计轨迹', async () => {
    get.mockResolvedValue({ entries: [KMS_AUDIT_ROW] });
    await expect(kmsApi.getKeyAudit('dek-1')).rejects.toBeInstanceOf(ListShapeError);
    get.mockResolvedValue(null);
    await expect(kmsApi.getKeyAudit('dek-1')).rejects.toBeInstanceOf(ListShapeError);
  });

  it('请求失败 ≠ 审计轨迹为空', async () => {
    get.mockRejectedValue({ code: 'INTERNAL_ERROR', message: 'boom' });
    await expect(kmsApi.getKeyAudit('dek-1')).rejects.toBeDefined();
  });
});

describe('certificateApi.listCertificates — 规范形态是裸数组（app/api/certificates.py:78）', () => {
  it('裸数组解出条目', async () => {
    get.mockResolvedValue([CERT_ROW]);
    const res = await certificateApi.listCertificates();
    expect(res).toHaveLength(1);
    expect(res[0].id).toBe('c1');
  });

  it('{items:[...]} 包装同样解出条目（旧实现抛 .map is not a function）', async () => {
    get.mockResolvedValue({ items: [CERT_ROW] });
    const res = await certificateApi.listCertificates();
    expect(res.map((c) => c.id)).toEqual(['c1']);
  });

  it('未识别载荷抛 ListShapeError', async () => {
    get.mockResolvedValue({ certificates: [CERT_ROW] });
    await expect(certificateApi.listCertificates()).rejects.toBeInstanceOf(ListShapeError);
  });

  it('请求失败 ≠ 空列表（页面据此显示错误而不是「暂无证书」）', async () => {
    get.mockRejectedValue({ code: 'SERVICE_UNAVAILABLE', message: 'down' });
    await expect(certificateApi.listCertificates()).rejects.toBeDefined();
  });
});

describe('证书状态：过期时间不可判定 ≠ 有效（identityApi.ts:113 宽松默认值）', () => {
  const withNotAfter = (notAfter: unknown) => {
    get.mockResolvedValue({ ...CERT_ROW, not_after: notAfter });
    return certificateApi.getCertificate('c1');
  };

  it('not_after 在未来 → active', async () => {
    expect((await withNotAfter('2099-01-01T00:00:00')).status).toBe('active');
  });

  it('not_after 已过 → expired', async () => {
    expect((await withNotAfter('2000-01-01T00:00:00')).status).toBe('expired');
  });

  it('not_after 缺失/不可解析 → unknown（旧实现报绿色「有效」）', async () => {
    expect((await withNotAfter('')).status).toBe('unknown');
    expect((await withNotAfter(undefined)).status).toBe('unknown');
    expect((await withNotAfter('not-a-date')).status).toBe('unknown');
  });
});

describe('certificateApi.verifyChain — 只认后端肯定结论（identityApi.ts:195 宽松默认值）', () => {
  beforeEach(() => {
    get.mockResolvedValue(CERT_ROW); // getCertificate
  });

  it('后端 valid:true → 通过', async () => {
    post.mockResolvedValue({ valid: true, subject: 'CN=cds', cert_id: 'c1', expired: false, not_yet_valid: false });
    const res = await certificateApi.verifyChain('c1');
    expect(res.valid).toBe(true);
    expect(res.errors).toEqual([]);
  });

  it('后端 valid:false + error → 不通过并带原因', async () => {
    post.mockResolvedValue({ valid: false, error: 'Certificate parse failed: x' });
    const res = await certificateApi.verifyChain('c1');
    expect(res.valid).toBe(false);
    expect(res.errors).toEqual(['Certificate parse failed: x']);
  });

  it('只带 error 无 valid 字段 → 不通过', async () => {
    post.mockResolvedValue({ error: 'Certificate parse failed: y' });
    const res = await certificateApi.verifyChain('c1');
    expect(res.valid).toBe(false);
    expect(res.errors[0]).toContain('parse failed');
  });

  it('缺少 valid 字段的未知响应 → null（无法确认），旧实现返回 true 绿灯', async () => {
    post.mockResolvedValue({ is_valid: true });
    const res = await certificateApi.verifyChain('c1');
    expect(res.valid).toBeNull();
    expect(res.errors.join('')).toContain('无法确认');
  });

  it('响应为 null/非对象 → null（无法确认）', async () => {
    post.mockResolvedValue(null);
    expect((await certificateApi.verifyChain('c1')).valid).toBeNull();
  });

  it('验证请求失败 → 拒绝，绝不呈现「验证通过」', async () => {
    post.mockRejectedValue({ code: 'INTERNAL_ERROR', message: 'verify 500' });
    await expect(certificateApi.verifyChain('c1')).rejects.toBeDefined();
  });
});
