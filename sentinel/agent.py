from pydantic import BaseModel
from datetime import datetime, timezone
import time

class ResourceLimits(BaseModel):
    cpu_percent_max: float = 1.0
    rss_mb_max: int = 50
    egress_kbps_avg_max: int = 5

class HealthDelta(BaseModel):
    ts: str
    packet_sketch: dict
    process_graph: dict

def emit_health_delta() -> HealthDelta:
    # Placeholder deterministic emitter
    return HealthDelta(
        ts=datetime.now(timezone.utc).isoformat(),
        packet_sketch={"buckets": [0,1,0,2]},
        process_graph={"nodes": 3, "edges": 2},
    )
