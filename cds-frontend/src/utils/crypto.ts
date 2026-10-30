/**
 * SM2/SM3 Cryptographic Utilities — Chinese national standard signing.
 *
 * Uses sm-crypto library for browser-compatible SM2 operations.
 * Key format: hex strings (private: 64 chars, public: 128 chars uncompressed)
 */

// @ts-expect-error sm-crypto has no type declarations
import * as smCrypto from 'sm-crypto';
const { sm2, sm3 } = smCrypto;

export interface SM2KeyPair {
  privateKey: string; // 64 hex chars
  publicKey: string;  // 128 hex chars (uncompressed, no 04 prefix)
}

/**
 * Generate an SM2 key pair in the browser.
 */
export function generateSM2KeyPair(): SM2KeyPair {
  const keypair = sm2.generateKeyPairHex();
  return {
    privateKey: keypair.privateKey,
    publicKey: keypair.publicKey,
  };
}

/**
 * Sign data with SM2 private key.
 * @param data - UTF-8 string to sign
 * @param privateKey - 64-char hex private key
 * @returns 128-char hex signature (r||s)
 */
export function sm2Sign(data: string, privateKey: string): string {
  // Mode C1C3C2 (standard GM/T 0009)
  const sigValue = sm2.doSignature(data, privateKey, {
    hash: true,
    der: false,
  });
  return sigValue;
}

/**
 * Verify SM2 signature.
 * @param data - Original UTF-8 string
 * @param signature - 128-char hex signature
 * @param publicKey - 128-char hex public key
 */
export function sm2Verify(data: string, signature: string, publicKey: string): boolean {
  return sm2.doVerifySignature(data, signature, publicKey, {
    hash: true,
    der: false,
  });
}

/**
 * Compute SM3 hash.
 */
export function sm3Hash(data: string): string {
  return sm3(data);
}

/**
 * 合约签名原文的字节锁（OCR-A110 / OCR-A255）——与后端
 * `app/services/crypto_service.py:217-219` 的同名类常量一一对应（上限取 DB 列宽，
 * 见 `app/models/contract.py`：contract_no / purpose 均为 String(64)）。
 * 三边常量与共享向量表由 `tests/test_ocr_a255_xlang_parity.py` 与本目录的
 * `crypto.a255-parity.test.ts` 锁死：改一边不改另一边即红。
 */
export const CONTRACT_PAYLOAD_MAX_LEN = 64;
export const CONTRACT_PAYLOAD_ROLE_MAX_LEN = 16;
export const CONTRACT_PAYLOAD_SCOPE_MAX_ITEMS = 64;

const PAYLOAD_FORBIDDEN = ['|'];
const PAYLOAD_SCOPE_FORBIDDEN = ['|', ','];

export interface ContractPayloadIssue {
  field: string;
  reason: string;
  index?: number;
}

/**
 * 原文不合法（含分隔符 / 超列宽）——与后端 `VALIDATION_ERROR` 同口径。
 *
 * 拒签而非转义：后端定案见 `crypto_service.py:208-216`——原文字节被本文件逐字
 * 镜像，各自转义会两边拼出不同的串 → 验签静默失败。`issues` 的 field/reason
 * 与后端 `detail.errors` 逐字一致，跳语言可对齐。
 */
export class ContractPayloadError extends Error {
  readonly issues: ContractPayloadIssue[];

  constructor(issues: ContractPayloadIssue[]) {
    const detail = issues.map((i) => `${i.field}${i.index === undefined ? '' : `[${i.index}]`}: ${i.reason}`).join('; ');
    super(`合约签名原文不得含 | / , 分隔符，且不得超过落库列宽，已本地拒签：${detail}`);
    this.name = 'ContractPayloadError';
    this.issues = issues;
  }
}

function payloadFieldIssues(
  field: string,
  value: string | null | undefined,
  maxLen: number,
  forbidden: string[] = PAYLOAD_FORBIDDEN,
): ContractPayloadIssue[] {
  const text = value ?? '';
  const issues: ContractPayloadIssue[] = forbidden
    .filter((ch) => text.includes(ch))
    .map((ch) => ({ field, reason: `contains forbidden separator '${ch}'` }));
  if (text.length > maxLen) {
    issues.push({ field, reason: `exceeds ${maxLen} characters` });
  }
  return issues;
}

/**
 * Build canonical contract signing payload.
 *
 * 必须与后端 crypto_service.contract_sign_data 逐字一致（Gap A4：使用目的属于被签原文），
 * 否则带 purpose 的合约在服务端验签必定失败。
 *
 * @throws ContractPayloadError 字段含 `|`（scope 元素另禁 `,`）或超列宽时在本地拒签，
 *   不产出可签原文、不发请求（A255：跨方场景下原文出自对方提交的资料，
 *   一条可重新解释的原文就是一条可被对方重新解释的不可否认证据）。
 */
export function contractSignPayload(
  contractId: string,
  contractNo: string,
  partyRole: 'provider' | 'buyer',
  timestamp: string,
  purpose?: string | null,
  purposeScope?: string[] | null,
): string {
  const errors: ContractPayloadIssue[] = [
    ...payloadFieldIssues('contract_id', contractId, CONTRACT_PAYLOAD_MAX_LEN),
    ...payloadFieldIssues('contract_no', contractNo, CONTRACT_PAYLOAD_MAX_LEN),
    ...payloadFieldIssues('party_role', partyRole, CONTRACT_PAYLOAD_ROLE_MAX_LEN),
    ...payloadFieldIssues('timestamp', timestamp, CONTRACT_PAYLOAD_MAX_LEN),
    ...payloadFieldIssues('purpose', purpose, CONTRACT_PAYLOAD_MAX_LEN),
  ];
  if (purposeScope) {
    if (purposeScope.length > CONTRACT_PAYLOAD_SCOPE_MAX_ITEMS) {
      errors.push({
        field: 'purpose_scope',
        reason: `more than ${CONTRACT_PAYLOAD_SCOPE_MAX_ITEMS} elements`,
      });
    }
    purposeScope.forEach((item, index) => {
      payloadFieldIssues('purpose_scope', item, CONTRACT_PAYLOAD_MAX_LEN, PAYLOAD_SCOPE_FORBIDDEN)
        .forEach((issue) => errors.push({ ...issue, index }));
    });
  }
  if (errors.length > 0) throw new ContractPayloadError(errors);

  let payload = `CDS-SIGN|${contractId}|${contractNo}|${partyRole}|${timestamp}`;
  if (purpose) payload += `|purpose:${purpose}`;
  if (purposeScope && purposeScope.length > 0) {
    payload += `|purpose_scope:${purposeScope.join(',')}`;
  }
  return payload;
}

/**
 * A258 披露口径（诚实降级铁律）：SM2 私钥的真实保管状态。
 *
 * 界面文案只能引用这个常量，不得自行写成「安全存储」「不落盘」这类比代码更强的说法。
 * 事实（可直接读码复核，代码变了要同步改这里与引用它的界面）：
 * 1) 密钥对**由服务端生成**并下发：ContractDetail 首次签署调 `POST /auth/generate-sm2-keys`，
 *    私钥是该端点的下行响应体字段（services/authApi.ts 的 SM2KeyPairResponse），明文回到浏览器；
 *    （本模块的 generateSM2KeyPair 是本地生成路径，当前产品代码没有调用方。）
 * 2) 浏览器随后把它以**明文**写入 localStorage（键名 `cds_sm2_privkey_<用户ID>`，见下方 save 函数）：
 *    没有口令派生、没有 WebCrypto 包裹、没有过期时间，拿到这台机器/这个浏览器的人就能取走。
 *
 * 私钥托管到服务端 KMS/HSM（私钥不出服务端）是 A258 尚未拍板的改造；没上线之前，上面这句就是真实姿态。
 */
export const SM2_KEY_STORAGE_DISCLOSURE =
  '本平台的 SM2 私钥由服务端生成后下发，并以明文保存在本浏览器的 localStorage（键名 cds_sm2_privkey_<用户ID>）里，' +
  '没有口令派生，也没有硬件加密保护；这是私钥托管方案（A258）落地前的过渡姿态，请勿在共用或不受控的机器上签署合约。';

/**
 * Load SM2 private key from localStorage.
 */
export function loadSM2PrivateKey(userId: string): string | null {
  return localStorage.getItem(`cds_sm2_privkey_${userId}`);
}

/**
 * Save SM2 private key to localStorage.
 */
export function saveSM2PrivateKey(userId: string, privateKey: string): void {
  localStorage.setItem(`cds_sm2_privkey_${userId}`, privateKey);
}

/**
 * Remove SM2 private key from localStorage.
 */
export function removeSM2PrivateKey(userId: string): void {
  localStorage.removeItem(`cds_sm2_privkey_${userId}`);
}
