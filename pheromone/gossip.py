from dataclasses import dataclass
from typing import List

@dataclass
class GossipConfig:
    fanout: int = 3
    rate_limit_eps: int = 50  # events per second

def rate_limited(events: int, cfg: GossipConfig) -> bool:
    return events <= cfg.rate_limit_eps
