#!/usr/bin/env python3
"""
Pheromone gossip v0.3 - Dual-path watcher for <200ms MTTD
Watches both Kubernetes Leases (reliability) and UDP fast-path (<200ms)
"""
import os
import sys
import json
import time
import threading
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException

# Add fast-path module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'prototype', 'pheromone'))
try:
    from udp_listener import FastPathListener
except ImportError:
    FastPathListener = None

from .signal_types import LeaseSignal, QuorumMetrics, ElevationEvent

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger('pheromone.gossip')

class DualPathWatcher:
    def __init__(self, namespace="aswarm", window_ms=80, quorum_threshold=3, 
                 node_score_threshold=0.7, fast_path_score=0.90,
                 udp_port=8888, fastpath_enabled=True):
        """Initialize dual-path watcher (Lease + UDP)
        
        Args:
            namespace: Kubernetes namespace to watch
            window_ms: Sliding window size for quorum computation (default 80ms)
            quorum_threshold: Minimum witnesses for elevation
            node_score_threshold: Minimum score for normal elevation
            fast_path_score: Score threshold for single-window fast elevation
            udp_port: UDP port for fast-path listener
            fastpath_enabled: Enable UDP fast-path for <200ms detection
        """
        try:
            config.load_incluster_config()
        except:
            config.load_kube_config()
            
        self.v1 = client.CoreV1Api()
        self.coordination_v1 = client.CoordinationV1Api()
        self.namespace = namespace
        self.window_s = window_ms / 1000.0
        self.quorum_threshold = quorum_threshold
        self.node_score_threshold = node_score_threshold
        self.fast_path_score = fast_path_score
        
        # Sliding window state
        self.signals = []  # List of LeaseSignal objects
        self.last_elevation = 0.0  # Monotonic time of last elevation
        self.elevation_backoff = 2.0  # Min seconds between elevations
        self.consecutive_elevations = 0  # Hysteresis counter
        self.elevated = False  # Track elevation state
        
        # Fast-path state
        self.fastpath_signals = []  # UDP signals for immediate elevation
        self.fastpath_lock = threading.Lock()
        
        # Initialize UDP listener if enabled
        self.udp_listener = None
        self.udp_port = udp_port
        self.fastpath_enabled = fastpath_enabled
        
        if fastpath_enabled and FastPathListener:
            try:
                # Get fast-path key from environment
                fastpath_key = os.environ.get('ASWARM_FASTPATH_KEY')
                if fastpath_key:
                    self.udp_listener = FastPathListener(
                        bind_addr='0.0.0.0',
                        bind_port=udp_port,
                        shared_keys={1: fastpath_key},
                        elevation_callback=self.handle_fastpath_signal,
                        num_workers=2,
                        queue_size=10000
                    )
                    self.udp_listener.start()
                    logger.info(f"UDP fast-path listener started on port {udp_port}")
                else:
                    logger.warning("Fast-path disabled: ASWARM_FASTPATH_KEY not set")
            except Exception as e:
                logger.error(f"Failed to start UDP listener: {e}")
        
        logger.info(f"Pheromone watcher initialized: window={window_ms}ms, quorum={quorum_threshold}, "
                   f"fastpath={bool(self.udp_listener)}")
    
    def parse_lease_signal(self, lease) -> Optional[LeaseSignal]:
        """Parse minimal annotations from Sentinel Lease"""
        if not lease.metadata or not lease.metadata.annotations:
            return None
            
        ann = lease.metadata.annotations
        
        # Extract minimal required fields with sensible defaults
        try:
            node = lease.metadata.name.replace("aswarm-sentinel-", "")
            seq = int(ann.get("aswarm.ai/seq", 0))
            score = float(ann.get("aswarm.ai/score", 0.0))
            
            # Parse timestamps
            client_ts = None
            if "aswarm.ai/ts" in ann:
                client_ts = datetime.fromisoformat(ann["aswarm.ai/ts"].replace('Z', '+00:00'))
            
            server_ts = None    
            if lease.spec and lease.spec.renew_time:
                server_ts = lease.spec.renew_time
            
            # Parse optional elevation fields
            elevate = ann.get("aswarm.ai/elevate") == "true"
            elevate_ts = None
            if elevate and "aswarm.ai/elevate-ts" in ann:
                elevate_ts = datetime.fromisoformat(ann["aswarm.ai/elevate-ts"].replace('Z', '+00:00'))
            
            run_id = ann.get("aswarm.ai/run-id")
            
            return LeaseSignal(
                node=node,
                seq=seq, 
                score=score,
                elevate=elevate,
                client_ts=client_ts,
                server_ts=server_ts,
                elevate_ts=elevate_ts,
                run_id=run_id
            )
            
        except (ValueError, KeyError) as e:
            print(f"Failed to parse lease {lease.metadata.name}: {e}", flush=True)
            return None
    
    def compute_sliding_window_quorum(self, run_id: str = None) -> Optional[QuorumMetrics]:
        """Compute quorum metrics over sliding window using server timestamps"""
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - self.window_s
        
        # Filter signals in window and matching run_id
        window_signals = []
        for signal in self.signals:
            if not signal.server_ts:
                continue
                
            signal_ts = signal.server_ts.timestamp()
            if signal_ts >= cutoff:
                if run_id is None or signal.run_id == run_id:
                    window_signals.append(signal)
        
        if not window_signals:
            return None
            
        # Compute metrics
        scores = [s.score for s in window_signals]
        witnesses = len(set(s.node for s in window_signals))  # Unique nodes
        
        return QuorumMetrics(
            witness_count=witnesses,
            total_samples=len(window_signals),
            mean_score=sum(scores) / len(scores),
            p95_score=sorted(scores)[int(0.95 * len(scores))] if scores else 0.0,
            window_start_ts=datetime.fromtimestamp(cutoff, timezone.utc),
            window_end_ts=now
        )
    
    def should_elevate(self, metrics: QuorumMetrics, run_id: str = None) -> tuple[bool, str]:
        """Fast-path elevation with hysteresis for robustness"""
        if not metrics:
            return False, "no_metrics"
            
        # Check backoff period
        if time.perf_counter() - self.last_elevation < self.elevation_backoff:
            return False, "backoff"
            
        # Skip if already elevated for this run
        if self.elevated:
            return False, "already_elevated"
            
        # Quorum threshold: minimum witnesses  
        if metrics.witness_count < self.quorum_threshold:
            return False, f"quorum({metrics.witness_count}<{self.quorum_threshold})"
        
        # FAST PATH: High confidence single-window elevation
        fast_path = (metrics.witness_count >= self.quorum_threshold and 
                    metrics.p95_score >= self.fast_path_score)
        
        if fast_path:
            self.elevated = True
            return True, f"fast_path(w={metrics.witness_count},p95={metrics.p95_score:.3f})"
        
        # NORMAL PATH: Hysteresis-based elevation
        high_signal = (metrics.witness_count >= self.quorum_threshold and 
                      metrics.mean_score >= self.node_score_threshold)
        
        if high_signal:
            self.consecutive_elevations += 1
            
            # Require 2 consecutive windows for normal elevation
            if self.consecutive_elevations >= 2:
                self.elevated = True
                return True, f"hysteresis(w={metrics.witness_count},s={metrics.mean_score:.3f},consec={self.consecutive_elevations})"
            else:
                return False, f"building({self.consecutive_elevations}/2)"
        else:
            # Reset consecutive counter on low signal
            self.consecutive_elevations = 0
            return False, f"reset_hysteresis"
    
    def create_elevation_artifact(self, event: ElevationEvent) -> str:
        """Create elevation ConfigMap artifact for MTTR measurement with proper hygiene"""
        # Always use run_id to avoid cross-run contamination
        if not event.run_id:
            print("Warning: elevation without run_id, skipping artifact", flush=True)
            return ""
            
        cm_name = f"aswarm-elevated-{event.run_id}"
        
        elevation_data = {
            "run_id": event.run_id,
            "decision_ts_server": event.decision_ts.isoformat(),
            "witness_count": event.metrics.witness_count,
            "mean_score": event.metrics.mean_score,
            "p95_score": event.metrics.p95_score,
            "threshold": self.quorum_threshold,
            "window_ms": int(self.window_s * 1000),
            "reason": event.reason,
            "confidence": event.metrics.confidence
        }
        
        cm = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(
                name=cm_name, 
                namespace=self.namespace,
                labels={
                    "type": "elevation",
                    "aswarm.ai/component": "pheromone", 
                    "aswarm.ai/run-id": event.run_id
                }
            ),
            data={
                "elevation.json": json.dumps(elevation_data, indent=2)
            }
        )
        
        try:
            # Always create (never patch to avoid timing races)
            self.v1.create_namespaced_config_map(self.namespace, cm)
            print(f"Created elevation artifact: {cm_name}", flush=True)
        except ApiException as e:
            if e.status == 409:  # Already exists - this is expected in multi-pheromone setups
                print(f"Elevation artifact {cm_name} already exists (normal in HA)", flush=True)
            else:
                print(f"Failed to create elevation artifact: {e}", flush=True)
                
        return cm_name
    
    def create_elevation_artifact_bg(self, metrics, run_id, decision_ts_server, reason):
        """Background task to create elevation ConfigMap without blocking decision"""
        try:
            cm_name = f"aswarm-elevated-{run_id}" if run_id else "aswarm-elevated"
            
            elevation_data = {
                "run_id": run_id,
                "decision_ts_server": decision_ts_server,
                "witness_count": int(metrics.witness_count),
                "mean_score": float(metrics.mean_score),
                "p95_score": float(metrics.p95_score),
                "threshold": int(self.quorum_threshold),
                "window_ms": int(self.window_s * 1000),
                "reason": reason
            }
            
            cm = client.V1ConfigMap(
                metadata=client.V1ObjectMeta(
                    name=cm_name,
                    namespace=self.namespace,
                    labels={
                        "type": "elevation",
                        "aswarm.ai/component": "pheromone",
                        "aswarm.ai/run-id": run_id
                    }
                ),
                data={
                    "elevation.json": json.dumps(elevation_data, indent=2)
                }
            )
            
            self.v1.create_namespaced_config_map(self.namespace, cm)
            
        except Exception as e:
            if "409" in str(e):  # Already exists
                pass  # Normal in HA setups
            else:
                print(f"Background artifact creation error: {e}", flush=True)
    
    def handle_fastpath_signal(self, elevation_data: Dict[str, Any], source: Tuple[str, int]):
        """Handle UDP fast-path elevation signal"""
        try:
            # Extract anomaly data
            anomaly = elevation_data.get('anomaly', {})
            score = anomaly.get('score', 0)
            
            # Check fast-path threshold
            if score < self.fast_path_score:
                return
            
            # Immediate elevation for high-confidence signals
            decision_ts = datetime.now(timezone.utc).isoformat()
            
            # Create minimal elevation artifact
            elevation_event = {
                "elevation": True,
                "decision_ts_server": decision_ts,
                "source": "fastpath",
                "node": elevation_data.get('node_id', 'unknown'),
                "score": score,
                "witness_count": anomaly.get('witness_count', 1),
                "selector": anomaly.get('selector', ''),
                "event_type": anomaly.get('event_type', 'fastpath'),
                "latency_ms": elevation_data.get('_fastpath', {}).get('approx_age_ms', 0),
                "run_id": elevation_data.get('run_id', '')
            }
            
            # Log elevation
            logger.info(f"Fast-path elevation: {json.dumps(elevation_event)}")
            
            # Create ConfigMap in background
            threading.Thread(
                target=self.create_fastpath_elevation_artifact,
                args=(elevation_event,),
                daemon=True
            ).start()
            
            # Update state
            self.last_elevation = time.perf_counter()
            self.elevated = True
            
        except Exception as e:
            logger.error(f"Fast-path handler error: {e}")
    
    def create_fastpath_elevation_artifact(self, elevation_event: Dict[str, Any]):
        """Create ConfigMap for fast-path elevation"""
        try:
            run_id = elevation_event.get('run_id', '')
            cm_name = f"aswarm-elevated-fastpath-{run_id}" if run_id else "aswarm-elevated-fastpath"
            
            cm = client.V1ConfigMap(
                metadata=client.V1ObjectMeta(
                    name=cm_name,
                    namespace=self.namespace,
                    labels={
                        "type": "elevation",
                        "aswarm.ai/component": "pheromone",
                        "aswarm.ai/source": "fastpath",
                        "aswarm.ai/run-id": run_id
                    }
                ),
                data={
                    "elevation.json": json.dumps(elevation_event, indent=2)
                }
            )
            
            self.v1.create_namespaced_config_map(self.namespace, cm)
            logger.debug(f"Created fast-path elevation artifact: {cm_name}")
            
        except ApiException as e:
            if e.status == 409:  # Already exists
                pass  # Normal in concurrent elevations
            else:
                logger.error(f"Failed to create fast-path artifact: {e}")
    
    def watch_leases(self, run_id: str = None, duration_s: int = None):
        """Main watch loop using Kubernetes list-watch"""
        w = watch.Watch()
        start_time = time.perf_counter()
        
        logger.info(f"Starting dual-path watcher: run_id={run_id}, duration={duration_s}s")
        
        try:
            for event in w.stream(
                self.coordination_v1.list_namespaced_lease,
                namespace=self.namespace,
                label_selector="app.kubernetes.io/component=sentinel",
                timeout_seconds=duration_s
            ):
                event_type = event['type']  # ADDED, MODIFIED, DELETED
                lease = event['object']
                
                if event_type in ('ADDED', 'MODIFIED'):
                    signal = self.parse_lease_signal(lease)
                    if signal:
                        # Add to sliding window (keeping it bounded)
                        self.signals.append(signal)
                        if len(self.signals) > 1000:  # Prevent memory growth
                            self.signals = self.signals[-500:]
                        
                        # Compute quorum metrics
                        metrics = self.compute_sliding_window_quorum(run_id)
                        if metrics:
                            should_elevate, reason = self.should_elevate(metrics, run_id)
                            
                            if should_elevate:
                                self.last_elevation = time.perf_counter()
                                decision_ts = datetime.now(timezone.utc)
                                decision_ts_server = decision_ts.isoformat()
                                
                                # Background evidence write to avoid blocking decision path
                                import threading
                                threading.Thread(
                                    target=self.create_elevation_artifact_bg,
                                    args=(metrics, run_id, decision_ts_server, reason),
                                    daemon=True
                                ).start()
                                
                                print(json.dumps({
                                    "elevation": True,
                                    "decision_ts_server": decision_ts_server,
                                    "witness_count": metrics.witness_count,
                                    "mean_score": metrics.mean_score,
                                    "p95_score": metrics.p95_score,
                                    "reason": reason,
                                    "ts": decision_ts_server
                                }), flush=True)
                
                # Check duration limit
                if duration_s and (time.perf_counter() - start_time) >= duration_s:
                    break
                    
        except Exception as e:
            print(f"Watch error: {e}", flush=True)
        finally:
            w.stop()
            
            # Stop UDP listener if running
            if self.udp_listener:
                self.udp_listener.stop()
                logger.info("UDP listener stopped")
        
        logger.info("Dual-path watcher stopped")

def main():
    """CLI entry point for Pheromone watcher"""
    import argparse
    
    parser = argparse.ArgumentParser(description="A-SWARM Pheromone Dual-Path Watcher v0.3")
    parser.add_argument("--namespace", default="aswarm", help="Kubernetes namespace")
    parser.add_argument("--window-ms", type=int, default=80, help="Sliding window (ms)")
    parser.add_argument("--quorum", type=int, default=3, help="Minimum witnesses for elevation")
    parser.add_argument("--node-score-threshold", type=float, default=0.7, help="Score threshold for normal elevation")
    parser.add_argument("--fast-path-score", type=float, default=0.90, help="P95 score threshold for single-window elevation")
    parser.add_argument("--duration", type=int, help="Watch duration (seconds)")
    parser.add_argument("--run-id", help="Run identifier for scoping")
    parser.add_argument("--udp-port", type=int, default=8888, help="UDP fast-path port")
    parser.add_argument("--no-fastpath", action="store_true", help="Disable UDP fast-path")
    
    args = parser.parse_args()
    
    watcher = DualPathWatcher(
        namespace=args.namespace,
        window_ms=args.window_ms,
        quorum_threshold=args.quorum,
        node_score_threshold=args.node_score_threshold,
        fast_path_score=args.fast_path_score,
        udp_port=args.udp_port,
        fastpath_enabled=not args.no_fastpath
    )
    
    try:
        watcher.watch_leases(
            run_id=args.run_id,
            duration_s=args.duration
        )
    except KeyboardInterrupt:
        print("\nWatcher stopped by user", flush=True)

if __name__ == "__main__":
    main()