import sys, json
sys.path.insert(0,'D:/workspace/sandbox')
from app.services.remote_attestation import *
from app.services.remote_attestation import AttestationService, AttestationPolicy, TEEQuote, TEEType, QuoteType, _sign_quote_payload
from app.utils.crypto import sm3_hash
svc = AttestationService(policy=AttestationPolicy())
for i in range(6):
    q = svc.generate_quote(TEEType.SGX, b'r'); svc.verify_quote(q)
print('log len', len(svc._verification_log))
print('C27 limit=0 ->', len(svc.get_verification_log(limit=0)), '| limit=-4 ->', len(svc.get_verification_log(limit=-4)), '| limit=2 ->', len(svc.get_verification_log(limit=2)))
print('C21 no maxlen:', getattr(svc._verification_log,'maxlen','NONE'), '| get_stats', svc.get_stats()['total_verifications'])
# C9b: the LIVE KMS path passes an explicit policy -> whitelist never loaded from settings
import inspect, re
src = inspect.getsource(AttestationService.__init__)
print('C9b settings-load guarded by "if policy is None":', 'if policy is None' in src)
s = AttestationService(policy=AttestationPolicy(require_fresh_quote=True, max_quote_age_seconds=300))
print('C9b allowed_measurements with explicit policy =', s._policy.allowed_measurements)
# audit gap: exception path leaves no log entry
before = len(svc._verification_log)
try:
    svc.verify_quote(TEEQuote(quote_id='z', quote_type=QuoteType.SGX_ECDSA, tee_type=TEEType.SGX, raw_bytes=b'[]', measurement='m', report_data='0'))
except Exception as e:
    print('crash path logged?', len(svc._verification_log) != before, '| raised', type(e).__name__)
# C7: signature comparison uses ==, non-constant-time; and is a plain hash (forgeable)
import inspect as I
print('C7 == compare:', 'signature == _sign_quote_payload' in I.getsource(_signature_valid))
print('C7 hmac imported?', 'hmac' in sys.modules or 'import hmac' in I.getsource(sys.modules['app.services.remote_attestation']))
# C18 f-string with no placeholder
lines = I.getsource(sys.modules['app.services.remote_attestation']).split(chr(10))
print('C18 L405:', lines[404].strip())
