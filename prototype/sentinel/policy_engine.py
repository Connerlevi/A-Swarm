from pydantic import BaseModel
from typing import Dict, Any

class PolicyDecision(BaseModel):
    ring: int
    action: str
    params: Dict[str, Any]
    ttl_seconds: int = 60

def evaluate_local_rules(health_delta) -> PolicyDecision | None:
    # Deterministic stub: never auto-escalate
    return None
