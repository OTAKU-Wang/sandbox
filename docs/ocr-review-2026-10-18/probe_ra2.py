from app.services.remote_attestation import (
    AttestationService, FirecrackerProvider, SGXProvider, TEEQuote, TEEType, QuoteType,
)

fc = FirecrackerProvider()
sgx = SGXProvider()
fq = fc.generate_quote()
sq = sgx.generate_quote()


def q(t, raw, qid, meas):
    return TEEQuote(quote_id=qid, quote_type=QuoteType.SOFTWARE_HASH, tee_type=t,
                    raw_bytes=raw, measurement=meas, report_data="")


print("FC provider + SGX raw ->", fc.verify_quote(q(TEEType.FIRECRACKER, sq.raw_bytes, sq.quote_id, sq.measurement)).status.value)
print("service: FC-shaped raw labelled SGX ->", AttestationService().verify_quote(q(TEEType.SGX, fq.raw_bytes, fq.quote_id, fq.measurement)).status.value)
print("service: SGX-shaped raw labelled FC ->", AttestationService().verify_quote(q(TEEType.FIRECRACKER, sq.raw_bytes, sq.quote_id, sq.measurement)).status.value)
s = AttestationService()
print("local_root_types:", sorted(t.value for t in s._local_root_types))
print("registered providers:", sorted(t.value for t in s._providers))
# hardware-shaped quote: real measurement value can never satisfy the derived formula
import json
payload = {"quote_id": "sgx-real-0001", "type": "sgx_ecdsa", "measurement": "e" * 64,
           "report_data": "11" * 32, "tcb_version": "2.23", "firmware": "SGX 2.0"}
payload["signature"] = "0" * 64
hw = TEEQuote(quote_id="sgx-real-0001", quote_type=QuoteType.SGX_ECDSA, tee_type=TEEType.SGX,
              raw_bytes=json.dumps(payload, sort_keys=True).encode(), measurement="e" * 64,
              report_data="11" * 32)
r = AttestationService().verify_quote(hw)
print("external/hardware-shaped sgx quote ->", r.status.value, "(measurement bound to sm3('sgx-enclave-'+quote_id))")
