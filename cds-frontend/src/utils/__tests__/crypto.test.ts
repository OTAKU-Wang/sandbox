import { describe, it, expect } from 'vitest';
import { contractSignPayload, sm2Sign, sm2Verify, generateSM2KeyPair, sm3Hash } from '../crypto';

// 跨语言机器契约：后端 crypto_service.contract_sign_data 重算同一原文后验签，多一段少一段均验签失败。
describe('contractSignPayload', () => {
  it('无 purpose 时保持四段旧版原文', () => {
    expect(contractSignPayload('c1', 'CDS-1', 'buyer', 'T')).toBe('CDS-SIGN|c1|CDS-1|buyer|T');
  });

  it('purpose 与 purpose_scope 按后端顺序追加', () => {
    expect(contractSignPayload('c1', 'CDS-1', 'provider', 'T', '风控建模', ['read', 'aggregate']))
      .toBe('CDS-SIGN|c1|CDS-1|provider|T|purpose:风控建模|purpose_scope:read,aggregate');
  });

  it('空 scope 不产生尾随分隔符', () => {
    expect(contractSignPayload('c1', 'CDS-1', 'buyer', 'T', 'x', [])).toBe('CDS-SIGN|c1|CDS-1|buyer|T|purpose:x');
  });

  it('签名原文变化会改变签名结果', () => {
    const kp = generateSM2KeyPair();
    const a = sm2Sign(contractSignPayload('c1', 'CDS-1', 'buyer', 'T', 'a'), kp.privateKey);
    const b = sm2Sign(contractSignPayload('c1', 'CDS-1', 'buyer', 'T', 'b'), kp.privateKey);
    expect(a).not.toBe(b);
    expect(sm2Verify(contractSignPayload('c1', 'CDS-1', 'buyer', 'T', 'a'), a, kp.publicKey)).toBe(true);
    expect(sm2Verify(contractSignPayload('c1', 'CDS-1', 'buyer', 'T', 'b'), a, kp.publicKey)).toBe(false);
  });

  it('sm3Hash 输出 64 位十六进制', () => {
    expect(sm3Hash('CDS')).toMatch(/^[0-9a-f]{64}$/);
  });
});
