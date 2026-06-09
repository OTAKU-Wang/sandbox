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
 * Build canonical contract signing payload.
 */
export function contractSignPayload(
  contractId: string,
  contractNo: string,
  partyRole: 'provider' | 'buyer',
  timestamp: string,
): string {
  return `CDS-SIGN|${contractId}|${contractNo}|${partyRole}|${timestamp}`;
}

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
