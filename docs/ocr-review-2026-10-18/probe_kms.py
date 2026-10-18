import sys, json, os
os.environ['CDS_ALLOW_SIMULATION']='false'
os.environ['CDS_DATABASE_URL']='sqlite+aiosqlite:///./ci_probe.db'
os.environ['CDS_TESTING']='1'
os.environ['CDS_ATTESTATION_ALLOWED_MEASUREMENTS']='aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899'
sys.path.insert(0,'D:/workspace/sandbox')
from app.core.config import get_settings
print('ALLOW_SIMULATION =', get_settings().ALLOW_SIMULATION)
print('ATTESTATION_ALLOWED_MEASUREMENTS =', repr(get_settings().ATTESTATION_ALLOWED_MEASUREMENTS))
from app.services.kms_service import KMSService
from app.services.remote_attestation import _sign_quote_payload
from app.utils.crypto import sm3_hash
import uuid
def forge(t):
    qid='sgx-'+uuid.uuid4().hex[:12]
    p={'quote_id':qid,'type':t,'measurement':sm3_hash(f'sgx-enclave-{qid}'.encode()),'report_data':'aa'*32,'tcb_version':'2.23','firmware':'SGX 2.0'}
    p['signature']=_sign_quote_payload(p)
    return json.dumps(p,sort_keys=True).encode()
k = KMSService()
print('forged type=software_hash, ALLOW_SIMULATION=false ->', k._verify_attestation(forge('software_hash'), None, 'key-1', 'sess-1'))
print('forged type=sgx_ecdsa,     ALLOW_SIMULATION=false ->', k._verify_attestation(forge('sgx_ecdsa'), None, 'key-1', 'sess-1'))
print('garbage bytes            ->', k._verify_attestation(b'[]', None, 'key-1', 'sess-1'))
print('None attestation         ->', k._verify_attestation(None, None, 'key-1', 'sess-1'))
