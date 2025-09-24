#!/usr/bin/env python3
"""
Signal type definitions for A-SWARM Lease-based telemetry
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class LeaseSignal:
    """Parsed signal from Sentinel Lease annotations - minimal schema"""
    node: str
    seq: int
    score: float  # Anomaly score [0,1]
    elevate: bool = False
    client_ts: Optional[datetime] = None
    server_ts: Optional[datetime] = None  # From lease.spec.renew_time
    elevate_ts: Optional[datetime] = None
    run_id: Optional[str] = None
    
    def __post_init__(self):
        """Validate signal after creation"""
        if self.score < 0 or self.score > 1:
            raise ValueError(f"Anomaly score must be [0,1], got {self.score}")
        
        if self.seq < 0:
            raise ValueError(f"Sequence must be non-negative, got {self.seq}")

@dataclass 
class QuorumMetrics:
    """Quorum computation results"""
    witness_count: int
    total_samples: int
    mean_score: float
    p95_score: float
    window_start_ts: datetime
    window_end_ts: datetime
    confidence: float = 0.0
    
    def __post_init__(self):
        """Compute confidence score"""
        # Confidence based on witness diversity and score strength
        if self.witness_count > 0 and self.mean_score > 0:
            witness_factor = min(1.0, self.witness_count / 3.0)  # Assume 3 is good diversity
            score_factor = min(1.0, self.mean_score / 0.8)  # 0.8 is strong signal
            self.confidence = witness_factor * score_factor
        else:
            self.confidence = 0.0

@dataclass
class ElevationEvent:
    """Elevation decision event"""
    elevated: bool
    run_id: Optional[str]
    metrics: QuorumMetrics
    decision_ts: datetime
    reason: str  # Why elevation was triggered/denied