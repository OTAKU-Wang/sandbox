import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach } from 'vitest';
import {
  SM2_KEY_STORAGE_DISCLOSURE,
  contractSignPayload,
  loadSM2PrivateKey,
  removeSM2PrivateKey,
  saveSM2PrivateKey,
  sm2Sign,
  sm2Verify,
  generateSM2KeyPair,
  sm3Hash,
} from '../crypto';

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

// A258 的「只披露」这一半：私钥托管模型要重得改（服务端派生+下发本身就是风险面），
// 未拍板前能确定的是：界面与常量不得把「明文躺在 localStorage」说成安全的。
// 这三条钉的是诚实性，不是文案字面量。
describe('SM2 私钥托管的披露口径（A258）', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('私钥就是明文写在 cds_sm2_privkey_<用户ID>：键名与「未加密」两件事钉住，不让它看起来像保管', () => {
    const key = 'ab'.repeat(32);
    saveSM2PrivateKey('u-1', key);
    expect(localStorage.getItem('cds_sm2_privkey_u-1')).toBe(key);
    expect(loadSM2PrivateKey('u-1')).toBe(key);
    removeSM2PrivateKey('u-1');
    expect(loadSM2PrivateKey('u-1')).toBeNull();
    expect(loadSM2PrivateKey('u-2')).toBeNull();
  });

  it('披露常量说清「服务端生成下发 + 明文 localStorage」这两件事实', () => {
    expect(SM2_KEY_STORAGE_DISCLOSURE).toContain('服务端生成');
    expect(SM2_KEY_STORAGE_DISCLOSURE).toContain('localStorage');
    expect(SM2_KEY_STORAGE_DISCLOSURE).toContain('明文');
  });

  it('签署页不再声称「私钥安全存储在本地」，而是引用上述披露常量', async () => {
    const page = await readFile(
      fileURLToPath(new URL('../../pages/Contracts/ContractDetail.tsx', import.meta.url)),
      'utf8',
    );
    expect(page).toContain('SM2_KEY_STORAGE_DISCLOSURE');
    expect(page).not.toMatch(/私钥安全存储|安全存储在本地|私钥不会落盘|不落盘/);
  });
});
