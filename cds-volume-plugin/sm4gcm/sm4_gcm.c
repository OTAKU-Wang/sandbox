/* sm4_gcm.c — SM4-GCM AEAD over libtongsuo EVP (SS-10 CB-03, spec §2.9).
 * Wire protocol documented in sm4_gcm.h. Fail-closed: every error path exits
 * non-zero before anything is written to stdout.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <openssl/evp.h>

#include "sm4_gcm.h"

static void die(int code, const char *msg)
{
    fprintf(stderr, "sm4_gcm: %s\n", msg);
    exit(code);
}

/* out must hold in_len bytes (GCM is a stream mode: |ct| == |pt|).
 * tag_out receives the 16-byte GCM tag. Returns 1 on success. */
int sm4_gcm_seal(const unsigned char *key, const unsigned char *nonce,
                 const unsigned char *aad, size_t aad_len,
                 const unsigned char *in, size_t in_len,
                 unsigned char *out, unsigned char *tag_out)
{
    EVP_CIPHER_CTX *ctx;
    int ok = 1, l = 0;

    if (!key || !nonce || !in || !out || !tag_out)
        return 0;
    ctx = EVP_CIPHER_CTX_new();
    if (!ctx)
        return 0;

    ok  = EVP_EncryptInit_ex(ctx, EVP_sm4_gcm(), NULL, NULL, NULL);
    ok &= EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, SM4GCM_NONCE_LEN, NULL);
    ok &= EVP_EncryptInit_ex(ctx, NULL, NULL, key, nonce);
    if (ok && aad && aad_len) {
        int al = 0;
        /* OpenSSL 3.x: AAD is fed via CipherUpdate with a NULL output buffer,
         * after key/IV init (Tongsuo rejects pre-key AAD updates). */
        ok &= EVP_CipherUpdate(ctx, NULL, &al, aad, (int)aad_len);
        ok &= (al == (int)aad_len);
    }
    if (ok) {
        ok  = EVP_CipherUpdate(ctx, out, &l, in, (int)in_len);
        ok &= (l == (int)in_len);
    }
    if (ok) {
        ok  = EVP_EncryptFinal_ex(ctx, out + l, &l);
        ok &= EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, SM4GCM_TAG_LEN, tag_out);
    }
    EVP_CIPHER_CTX_free(ctx);
    return ok;
}

/* out must hold in_len bytes. Returns 1 only if the authentication tag
 * verifies; a tampered tag/ciphertext/aad returns 0 and out is untouched
 * by the caller (fail-closed: never emit unauthenticated plaintext). */
int sm4_gcm_open(const unsigned char *key, const unsigned char *nonce,
                 const unsigned char *aad, size_t aad_len,
                 const unsigned char *in, size_t in_len,
                 const unsigned char *tag, unsigned char *out)
{
    EVP_CIPHER_CTX *ctx;
    int ok = 1, l = 0, l2 = 0;

    if (!key || !nonce || !in || !out || !tag)
        return 0;
    ctx = EVP_CIPHER_CTX_new();
    if (!ctx)
        return 0;

    ok  = EVP_EncryptInit_ex(ctx, EVP_sm4_gcm(), NULL, NULL, NULL);
    ok &= EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, SM4GCM_NONCE_LEN, NULL);
    ok &= EVP_DecryptInit_ex(ctx, NULL, NULL, key, nonce);
    if (ok && aad && aad_len) {
        int al = 0;
        ok &= EVP_CipherUpdate(ctx, NULL, &al, aad, (int)aad_len);
        ok &= (al == (int)aad_len);
    }
    if (ok) {
        ok  = EVP_CipherUpdate(ctx, out, &l, in, (int)in_len);
        ok &= (l == (int)in_len);
    }
    /* GCM: authentication is finalized only after the expected tag is set;
     * EVP_DecryptFinal_ex returns 0 on tag mismatch (tamper detection). */
    if (ok) {
        ok  = EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG, SM4GCM_TAG_LEN, (void *)tag);
        ok &= EVP_DecryptFinal_ex(ctx, out + l, &l2);
    }
    EVP_CIPHER_CTX_free(ctx);
    return ok;
}

static int read_growing(unsigned char **buf, size_t *len)
{
    size_t cap = 1 << 20, n;
    unsigned char chunk[1 << 16];

    *buf = malloc(cap);
    if (!*buf)
        return 0;
    *len = 0;
    while ((n = fread(chunk, 1, sizeof(chunk), stdin)) > 0) {
        if (*len + n > cap) {
            cap *= 2;
            unsigned char *ni = realloc(*buf, cap);
            if (!ni)
                return 0;
            *buf = ni;
        }
        memcpy(*buf + *len, chunk, n);
        *len += n;
    }
    if (ferror(stdin))
        return 0;
    return 1;
}

int main(int argc, char **argv)
{
    unsigned char key[SM4GCM_KEY_LEN];
    unsigned char nonce[SM4GCM_NONCE_LEN];
    unsigned char tag[SM4GCM_TAG_LEN];
    unsigned char aadlen_be[4];
    size_t aad_len, body_len;
    unsigned char *aad, *body, *out;
    int is_enc;

    if (argc != 2 || (strcmp(argv[1], "encrypt") && strcmp(argv[1], "decrypt")))
        die(1, "usage: sm4_gcm <encrypt|decrypt>");
    is_enc = strcmp(argv[1], "encrypt") == 0;

#define RD_FULL(buf) do { \
        if (fread((buf), 1, sizeof(buf), stdin) != sizeof(buf)) \
            die(2, "short read on stdin"); \
    } while (0)

    RD_FULL(key);
    RD_FULL(nonce);
    RD_FULL(aadlen_be);
    aad_len = ((size_t)aadlen_be[0] << 24) | ((size_t)aadlen_be[1] << 16) |
              ((size_t)aadlen_be[2] << 8) | (size_t)aadlen_be[3];
    if (aad_len > SM4GCM_MAX_AAD)
        die(2, "aad_len out of bounds");
    aad = aad_len ? malloc(aad_len) : NULL;
    if (aad && fread(aad, 1, aad_len, stdin) != aad_len)
        die(2, "short read on aad");

    if (!is_enc)
        RD_FULL(tag);

    if (!read_growing(&body, &body_len))
        die(2, "oom or stdin read error");
    out = malloc(body_len ? body_len : 1);
    if (!out)
        die(2, "oom");

    if (is_enc) {
        if (!sm4_gcm_seal(key, nonce, aad, aad_len, body, body_len, out, tag))
            die(3, "sm4-gcm encrypt failed");
        if (fwrite(tag, 1, SM4GCM_TAG_LEN, stdout) != SM4GCM_TAG_LEN ||
            (body_len && fwrite(out, 1, body_len, stdout) != body_len) ||
            fflush(stdout) != 0)
            die(2, "stdout write failure");
    } else {
        if (!sm4_gcm_open(key, nonce, aad, aad_len, body, body_len, tag, out))
            die(3, "sm4-gcm decrypt failed: tag/ciphertext/aad mismatch (fail-closed)");
        if ((body_len && fwrite(out, 1, body_len, stdout) != body_len) ||
            fflush(stdout) != 0)
            die(2, "stdout write failure");
    }

    explicit_bzero(key, sizeof(key));
    free(aad);
    free(body);
    free(out);
    return 0;
}
