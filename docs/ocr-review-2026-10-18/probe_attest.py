import sys, json, uuid
sys.path.insert(0, 'D:/workspace/sandbox')
from app.services.remote_attestation import (AttestationService, AttestationPolicy, TEEQuote, TEEType,
                                             QuoteType, AttestationStatus, _LOCAL_ATTESTATION_ROOT,
                                             _sign_quote_payload, _canonical_quote_payload)
from datetime import datetime, timezone, timedelta
from app.utils.crypto import sm3_hash

svc = AttestationService(policy=AttestationPolicy())

# C15/C16/C17: non-dict JSON quote -> uncaught exception?
for tt, qt in [(TEEType.SGX, QuoteType.SGX_ECDSA), (TEEType.SEV_SNP, QuoteType.SEV_SNP), (TEEType.FIRECRACKER, QuoteType.SOFTWARE_HASH)]:
    for blob in [b'[]', b'null', b'42', b'"str"']:
        q = TEEQuote(quote_id='x', quote_type=qt, tee_type=tt, raw_bytes=blob, measurement='m', report_data='00')
        try:
            r = svc.verify_quote(q); print('C15-17', tt.value, blob, '->', r.status.value)
        except Exception as e:
            print('C15-17', tt.value, blob, '-> RAISED', type(e).__name__, e)

# C27: get_verification_log limit semantics
n = len(svc._verification_log)
print('C27 log len', n, '| limit=0 ->', len(svc.get_verification_log(limit=0)), '| limit=-5 ->', len(svc.get_verification_log(limit=-5)), '| limit=2 ->', len(svc.get_verification_log(limit=2)))

# C6/C7: forge a quote offline with the public constant
def forge(tee_type, qt, quote_id):
    if tee_type is TEEType.SGX: comp=None; m=sm3_hash(f'sgx-enclave-{quote_id}'.encode()); payload={'quote_id':quote_id,'type':'sgx_ecdsa','measurement':m,'report_data':'aa'*32,'tcb_version':'2.23','firmware':'SGX 2.0'}
    elif tee_type is TEEType.SEV_SNP: m=sm3_hash(f'sev-snp-{quote_id}'.encode()); payload={'quote_id':quote_id,'type':'sev_snp','measurement':m,'report_data':'aa'*32,'tcb_version':'1.55','vmpl':0}
    else:
        comp='kernel:v5.15-hardened|rootfs:gvisor-ext4|config:isolated'; m=sm3_hash(f'{comp}|{quote_id}'.encode()); payload={'quote_id':quote_id,'type':'software_hash','measurement':m,'report_data':'aa'*32,'components':comp}
    payload['signature']=_sign_quote_payload(payload)
    return TEEQuote(quote_id=quote_id, quote_type=qt, tee_type=tee_type, raw_bytes=json.dumps(payload,sort_keys=True).encode(),
                    measurement=m, report_data=payload['report_data'])
for tt, qt in [(TEEType.SGX, QuoteType.SGX_ECDSA), (TEEType.SEV_SNP, QuoteType.SEV_SNP), (TEEType.FIRECRACKER, QuoteType.SOFTWARE_HASH)]:
    r = svc.verify_quote(forge(tt, qt, 'forged-'+uuid.uuid4().hex[:12]))
    print('C6 offline forged', tt.value, '->', r.status.value, 'verified=', r.verified, 'trust_root=', r.trust_root)

# C8: replay with stale raw quote + fresh timestamp; and future timestamp
old = forge(TEEType.SGX, QuoteType.SGX_ECDSA, 'sgx-old0000000')
old.raw_bytes = json.dumps({**json.loads(old.raw_bytes), }, sort_keys=True).encode()
old.timestamp = datetime(2020,1,1,tzinfo=timezone.utc)
print('C8 stale timestamp ->', svc.verify_quote(old).status.value)
ts = json.loads(old.raw_bytes)
print('C8 timestamp inside signed payload?', any('time' in k or 'ts' == k for k in ts.keys()), '| payload keys', sorted(ts.keys()))
replay = TEEQuote(quote_id=old.quote_id, quote_type=QuoteType.SGX_ECDSA, tee_type=TEEType.SGX, raw_bytes=old.raw_bytes,
                  measurement=old.measurement, report_data=old.report_data)  # timestamp defaults to now()
r = svc.verify_quote(replay); print('C8 replayed 2020 quote w/ now() timestamp ->', r.status.value, r.verified, r.trust_root)
fut = TEEQuote(quote_id=old.quote_id, quote_type=QuoteType.SGX_ECDSA, tee_type=TEEType.SGX, raw_bytes=old.raw_bytes,
               measurement=old.measurement, report_data=old.report_data, timestamp=datetime.now(timezone.utc)+timedelta(days=365))
print('C8 future timestamp ->', svc.verify_quote(fut).status.value)
try:
    naive = TEEQuote(quote_id=old.quote_id, quote_type=QuoteType.SGX_ECDSA, tee_type=TEEType.SGX, raw_bytes=old.raw_bytes,
                     measurement=old.measurement, report_data=old.report_data, timestamp=datetime.utcnow())
    print('C8 naive timestamp ->', svc.verify_quote(naive).status.value)
except Exception as e: print('C8 naive timestamp RAISED', type(e).__name__, e)
# C8 report_data never compared
print('C8 nonce/report_data compared by service?', 'report_data' in svc.verify_quote.__doc__ or 'expected' in str(svc._policy.__dict__))

# C9/C18: whitelist behaviour
s2 = AttestationService(policy=AttestationPolicy(allowed_measurements={'deadbeef'}))
q = s2.generate_quote(TEEType.SGX, b'session-1')
print('C18 measurement whitelist with realistic policy ->', s2.verify_quote(q).status.value)
q2 = s2.generate_quote(TEEType.SGX, b'session-2')
print('C18 fresh quote measurement differs:', q.measurement[:16], 'vs', q2.measurement[:16])
s3 = AttestationService(policy=AttestationPolicy())
s3._policy.allowed_measurements = set()
print('C9 empty whitelist accepts anything ->', s3.verify_quote(forge(TEEType.SGX, QuoteType.SGX_ECDSA, 'sgx-aaa1112223')).status.value)

# C24: policy_checks on a failed verification
bad = forge(TEEType.SGX, QuoteType.SGX_ECDSA, 'sgx-bad0000000'); bad.raw_bytes = b'{"type":"sgx_ecdsa","quote_id":"nope"}'
r = svc.verify_quote(bad)
print('C24 failed verify status=', r.status.value, 'verified=', r.verified, 'checks=', r.policy_checks)

# C19: policy fields unread -> prove by setting them and seeing no effect
s4 = AttestationService(policy=AttestationPolicy(min_tcb_version='99.99', require_signed=False))
r = s4.verify_quote(forge(TEEType.SGX, QuoteType.SGX_ECDSA, 'sgx-tcb0000001'))
print('C19 min_tcb_version=99.99 / require_signed=False ->', r.status.value, 'tcb_status=', r.tcb_status)

# C20: register_provider overwrite keeps trust_root local-simulated
from app.services.remote_attestation import AttestationProvider
class Ext(AttestationProvider):
    @property
    def tee_type(self): return TEEType.SGX
    def generate_quote(self, report_data=b''): raise NotImplementedError
    def verify_quote(self, quote):
        from app.services.remote_attestation import AttestationResult
        return AttestationResult(quote_id=quote.quote_id, status=AttestationStatus.VERIFIED, tee_type=TEEType.SGX,
                                 measurement=quote.measurement, verified=True)
s5 = AttestationService(policy=AttestationPolicy()); s5.register_provider(Ext())
r = s5.verify_quote(forge(TEEType.SGX, QuoteType.SGX_ECDSA, 'sgx-ext0000001'))
print('C20 external SGX provider result trust_root =', r.trust_root, '(should be external)')
