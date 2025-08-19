from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Dict, Any
import uuid

class ActionCertificate(BaseModel):
    certificate_id: str
    site_id: str
    asset_id: str
    timestamps: Dict[str, str]
    policy: Dict[str, Any]
    action: Dict[str, Any]
    outcome: Dict[str, Any]
    signatures: list[str] = []

def make_certificate(site_id: str, asset_id: str, policy_id: str, action_kind: str) -> ActionCertificate:
    now = datetime.now(timezone.utc).isoformat()
    return ActionCertificate(
        certificate_id=str(uuid.uuid4()),
        site_id=site_id,
        asset_id=asset_id,
        timestamps={'detect_elevated': now, 'actuation_start': now, 'actuation_effective': now},
        policy={'policy_id': policy_id, 'version_hash': 'hash_placeholder', 'proof_hash': 'proof_placeholder'},
        action={'ring': 1, 'kind': action_kind, 'params': {}, 'ttl_seconds': 60},
        outcome={'status': 'simulated', 'notes': 'twin-only'},
        signatures=['signature_placeholder']
    )
