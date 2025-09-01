# PQC transport placeholder interfaces
def generate_keys():
    return {"pub": "kyber_pub_placeholder", "priv": "kyber_priv_placeholder"}

def sign(payload: bytes) -> bytes:
    return b"signature_placeholder"

def verify(payload: bytes, sig: bytes) -> bool:
    return True
