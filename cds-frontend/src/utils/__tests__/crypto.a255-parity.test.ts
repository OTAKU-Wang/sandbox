/**
 * OCR-A255 三语言字节平价 —— 前端这一边（另两边见 tests/test_ocr_a255_xlang_parity.py）。
 *
 * 本文件的 LIMITS / VECTORS 是三边共用的唯一向量表：后端
 * app/services/crypto_service.py::contract_sign_data、SDK
 * sdk/cds_sdk/client.py::CDSClient.sign_data、前端 contractSignPayload
 * 必须在同一组输入下产出完全相同的字节串，并对同一组越界输入各自在本地拒签
 * （不发请求、不签名）。定案是拼接前拒绝而非转义，理由见
 * app/services/crypto_service.py:208-216 的注释。
 *
 * op=payload 的 payload 字段是后端实际产出的字节（生成时由
 * CryptoService.contract_sign_data 取得），pytest 侧逐条复算锁定：后端一改即红。
 * 改这张表要用 pytest（它拒绝与后端不符的向量），不要只改前端。
 */
import { describe, expect, it } from 'vitest';
import {
  CONTRACT_PAYLOAD_MAX_LEN,
  CONTRACT_PAYLOAD_ROLE_MAX_LEN,
  CONTRACT_PAYLOAD_SCOPE_MAX_ITEMS,
  ContractPayloadError,
  contractSignPayload,
  generateSM2KeyPair,
  sm2Sign,
  sm2Verify,
} from '../crypto';

interface Vector {
  name: string;
  op: 'payload' | 'reject';
  contract_id: string;
  contract_no: string;
  party_role: string;
  timestamp: string;
  purpose: string | null;
  purpose_scope: string[] | null;
  payload?: string;
}

/* LIMITS-BEGIN 与后端 CryptoService._PAYLOAD_* 同名常量一一对应；pytest 侧同时比对 SDK 常量 */
const LIMITS: { max_len: number; role_max_len: number; scope_max_items: number } = {"max_len": 64, "role_max_len": 16, "scope_max_items": 64};
/* LIMITS-END */

/* VECTORS-BEGIN 唯一向量表：JSON 字面量，pytest 按文本解析——别加注释或尾逗号 */
const VECTORS: readonly Vector[] = [
  {"name": "legacy-no-purpose", "op": "payload", "contract_id": "c-1", "contract_no": "C-1", "party_role": "buyer", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": null, "purpose_scope": null, "payload": "CDS-SIGN|c-1|C-1|buyer|2026-10-20T00:00:00.000Z"},
  {"name": "purpose-and-scope-cjk", "op": "payload", "contract_id": "c-1", "contract_no": "C-1", "party_role": "provider", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": "风控建模", "purpose_scope": ["read", "aggregate"], "payload": "CDS-SIGN|c-1|C-1|provider|2026-10-20T00:00:00.000Z|purpose:风控建模|purpose_scope:read,aggregate"},
  {"name": "empty-purpose-and-empty-scope-are-omitted", "op": "payload", "contract_id": "c-1", "contract_no": "C-1", "party_role": "buyer", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": "", "purpose_scope": [], "payload": "CDS-SIGN|c-1|C-1|buyer|2026-10-20T00:00:00.000Z"},
  {"name": "purpose-only", "op": "payload", "contract_id": "c-1", "contract_no": "C-1", "party_role": "buyer", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": "statistical_analysis", "purpose_scope": null, "payload": "CDS-SIGN|c-1|C-1|buyer|2026-10-20T00:00:00.000Z|purpose:statistical_analysis"},
  {"name": "boundary-max-lengths", "op": "payload", "contract_id": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", "contract_no": "NNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNN", "party_role": "provider", "timestamp": "2026-10-20T00:00:00.000ZTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT", "purpose": "用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用", "purpose_scope": ["s", "t", "u"], "payload": "CDS-SIGN|cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc|NNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNN|provider|2026-10-20T00:00:00.000ZTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT|purpose:用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用|purpose_scope:s,t,u"},
  {"name": "scope-64-items-at-cap", "op": "payload", "contract_id": "c-1", "contract_no": "C-1", "party_role": "buyer", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": "p", "purpose_scope": ["s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "s12", "s13", "s14", "s15", "s16", "s17", "s18", "s19", "s20", "s21", "s22", "s23", "s24", "s25", "s26", "s27", "s28", "s29", "s30", "s31", "s32", "s33", "s34", "s35", "s36", "s37", "s38", "s39", "s40", "s41", "s42", "s43", "s44", "s45", "s46", "s47", "s48", "s49", "s50", "s51", "s52", "s53", "s54", "s55", "s56", "s57", "s58", "s59", "s60", "s61", "s62", "s63"], "payload": "CDS-SIGN|c-1|C-1|buyer|2026-10-20T00:00:00.000Z|purpose:p|purpose_scope:s0,s1,s2,s3,s4,s5,s6,s7,s8,s9,s10,s11,s12,s13,s14,s15,s16,s17,s18,s19,s20,s21,s22,s23,s24,s25,s26,s27,s28,s29,s30,s31,s32,s33,s34,s35,s36,s37,s38,s39,s40,s41,s42,s43,s44,s45,s46,s47,s48,s49,s50,s51,s52,s53,s54,s55,s56,s57,s58,s59,s60,s61,s62,s63"},
  {"name": "pipe-in-contract-no", "op": "reject", "contract_id": "c-1", "contract_no": "X|purpose:Y", "party_role": "buyer", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": null, "purpose_scope": null},
  {"name": "pipe-shift-collision-pair", "op": "reject", "contract_id": "c-1|A", "contract_no": "B", "party_role": "buyer", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": null, "purpose_scope": null},
  {"name": "comma-in-scope-element", "op": "reject", "contract_id": "c-1", "contract_no": "N-1", "party_role": "buyer", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": "p", "purpose_scope": ["read,aggregate"]},
  {"name": "pipe-in-timestamp", "op": "reject", "contract_id": "c-1", "contract_no": "N-1", "party_role": "buyer", "timestamp": "2026-10-20T00:00:00.000Z|x", "purpose": null, "purpose_scope": null},
  {"name": "pipe-in-purpose", "op": "reject", "contract_id": "c-1", "contract_no": "N-1", "party_role": "buyer", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": "风控|建模", "purpose_scope": null},
  {"name": "contract-no-over-column-width", "op": "reject", "contract_id": "c-1", "contract_no": "NNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNN", "party_role": "buyer", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": null, "purpose_scope": null},
  {"name": "party-role-over-cap", "op": "reject", "contract_id": "c-1", "contract_no": "N-1", "party_role": "buyerxxxxxxxxxxxx", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": null, "purpose_scope": null},
  {"name": "purpose-over-column-width", "op": "reject", "contract_id": "c-1", "contract_no": "N-1", "party_role": "buyer", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": "用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用用", "purpose_scope": null},
  {"name": "scope-too-many-items", "op": "reject", "contract_id": "c-1", "contract_no": "N-1", "party_role": "buyer", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": "p", "purpose_scope": ["s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "s12", "s13", "s14", "s15", "s16", "s17", "s18", "s19", "s20", "s21", "s22", "s23", "s24", "s25", "s26", "s27", "s28", "s29", "s30", "s31", "s32", "s33", "s34", "s35", "s36", "s37", "s38", "s39", "s40", "s41", "s42", "s43", "s44", "s45", "s46", "s47", "s48", "s49", "s50", "s51", "s52", "s53", "s54", "s55", "s56", "s57", "s58", "s59", "s60", "s61", "s62", "s63", "s64"]},
  {"name": "scope-element-over-cap", "op": "reject", "contract_id": "c-1", "contract_no": "N-1", "party_role": "buyer", "timestamp": "2026-10-20T00:00:00.000Z", "purpose": "p", "purpose_scope": ["sssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssss"]}
];
/* VECTORS-END */

const call = (v: Vector) =>
  contractSignPayload(
    v.contract_id,
    v.contract_no,
    v.party_role as 'provider' | 'buyer',
    v.timestamp,
    v.purpose,
    v.purpose_scope,
  );

const payloadVectors = VECTORS.filter((v) => v.op === 'payload');
const rejectVectors = VECTORS.filter((v) => v.op === 'reject');

describe('A255 三语言字节平价 — 前端 contractSignPayload', () => {
  it('向量表非空且两类都在（防止掏空向量表冒充绿）', () => {
    expect(payloadVectors.length).toBeGreaterThanOrEqual(5);
    expect(rejectVectors.length).toBeGreaterThanOrEqual(8);
    expect(new Set(rejectVectors.map((v) => v.name)).size).toBe(rejectVectors.length);
  });

  it('长度上限常量与后端/SDK 同源', () => {
    expect({
      max_len: CONTRACT_PAYLOAD_MAX_LEN,
      role_max_len: CONTRACT_PAYLOAD_ROLE_MAX_LEN,
      scope_max_items: CONTRACT_PAYLOAD_SCOPE_MAX_ITEMS,
    }).toEqual(LIMITS);
  });

  // JS 里两个字符串相等即 UTF-8 字节序列相等（两者都是合法 USV 序列），
  // 所以 toBe 就是字节平价断言。
  it.each(payloadVectors)('逐字节等于后端产出的原文: $name', (v) => {
    expect(call(v)).toBe(v.payload);
  });

  it.each(rejectVectors)('越界输入本地拒签，不产出可签原文: $name', (v) => {
    expect(() => call(v)).toThrow(ContractPayloadError);
  });

  it('拒签原因口径与后端逐字一致（field/reason 跨语言同名）', () => {
    const bad = rejectVectors.find((v) => v.name === 'comma-in-scope-element');
    if (!bad) throw new Error('向量表缺少 comma-in-scope-element');
    let issues: unknown = null;
    try {
      call(bad);
    } catch (err) {
      issues = (err as ContractPayloadError).issues;
    }
    expect(issues).toEqual([
      { field: 'purpose_scope', reason: "contains forbidden separator ','", index: 0 },
    ]);
  });

  it('合法路径未被拒签打死（SM2 签署/验签仍然成立）', () => {
    const v = payloadVectors.find((x) => x.name === 'purpose-and-scope-cjk');
    if (!v?.payload) throw new Error('向量表缺少 purpose-and-scope-cjk');
    const kp = generateSM2KeyPair();
    const sig = sm2Sign(call(v), kp.privateKey);
    expect(sm2Verify(call(v), sig, kp.publicKey)).toBe(true);
  });
});
