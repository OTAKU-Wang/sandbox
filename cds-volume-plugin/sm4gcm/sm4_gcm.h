/* sm4_gcm.h — SM4-GCM AEAD helper (SS-10 CB-03, spec §2.9).
 *
 * Links libtongsuo (OpenSSL 3.x-compatible EVP). The `enc`-style CLI is
 * FORBIDDEN for AEAD (Tongsuo `enc` rejects all AEAD ciphers — see spec
 * §2.9 / Tongsuo apps/lib/opt.c:opt_cipher); this helper uses the EVP
 * library path directly.
 *
 * Binary stdin/stdout protocol (strict; any non-zero exit = failure,
 * fail-closed by the caller):
 *
 *   sm4_gcm encrypt
 *     stdin : key(16) || nonce(12) || aad_len(4, BE) || aad || plaintext
 *     stdout: tag(16) || ciphertext
 *     exit  : 0 ok | 1 usage | 2 io | 3 cipher
 *
 *   sm4_gcm decrypt
 *     stdin : key(16) || nonce(12) || aad_len(4, BE) || aad || tag(16) || ciphertext
 *     stdout: plaintext
 *     exit  : 0 ok | 1 usage | 2 io | 3 auth-fail(ciphertext/tag/aad tampered)
 *
 * The key is carried on stdin (never argv/env) to avoid /proc exposure.
 */
#ifndef CDS_SM4_GCM_H
#define CDS_SM4_GCM_H

#include <stddef.h>

#define SM4GCM_KEY_LEN   16  /* SM4 = 128-bit key */
#define SM4GCM_NONCE_LEN 12  /* GCM standard nonce */
#define SM4GCM_TAG_LEN   16  /* GCM tag */
#define SM4GCM_MAX_AAD   (16u * 1024u)

/* out must hold in_len bytes (GCM: |ct| == |pt|); tag_out receives the 16-byte tag. */
int sm4_gcm_seal(const unsigned char *key, const unsigned char *nonce,
                 const unsigned char *aad, size_t aad_len,
                 const unsigned char *in, size_t in_len,
                 unsigned char *out, unsigned char *tag_out);
/* Returns 1 only if the tag verifies; tampered tag/ciphertext/aad -> 0. */
int sm4_gcm_open(const unsigned char *key, const unsigned char *nonce,
                 const unsigned char *aad, size_t aad_len,
                 const unsigned char *in, size_t in_len,
                 const unsigned char *tag, unsigned char *out);

#endif /* CDS_SM4_GCM_H */
