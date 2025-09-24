#!/usr/bin/env python3
"""
Sentinel telemetry v0.3 - Dual-path signaling for <200ms MTTD
Emits signals via both Kubernetes Leases (reliability) and UDP fast-path (<200ms)
"""
import os
import sys
import time
import json
import random
import hashlib
import logging
from datetime import datetime, timezone
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Add fast-path module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'prototype', 'sentinel'))
try:
    from fast_path import FastPathSender
except ImportError:
    FastPathSender = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger('sentinel.telemetry')

class DualPathTelemetry:
    def __init__(self, node_name=None, namespace="aswarm", cadence_ms=150,
                 fastpath_host=None, fastpath_port=8888, fastpath_enabled=True):
        """Initialize dual-path telemetry (Lease + UDP)
        
        Args:
            node_name: Kubernetes node name (auto-detected if None)
            namespace: Kubernetes namespace for Leases
            cadence_ms: Signal emission interval (50-150ms recommended)
            fastpath_host: UDP fast-path target (Pheromone service)
            fastpath_port: UDP port for fast-path
            fastpath_enabled: Enable UDP fast-path for <200ms detection
        """
        try:
            config.load_incluster_config()
        except:
            config.load_kube_config()
        
        self.v1 = client.CoreV1Api()
        self.coordination_v1 = client.CoordinationV1Api()
        self.namespace = namespace
        self.cadence_s = cadence_ms / 1000.0
        
        # Auto-detect node name from Downward API or hostname (cross-platform)
        if node_name:
            self.node_name = node_name
        else:
            self.node_name = os.environ.get('NODE_NAME')
            if not self.node_name:
                # Cross-platform hostname detection
                import platform
                self.node_name = platform.node().lower()
        
        # Sanitize node name for Kubernetes RFC 1123 compliance
        import re
        sanitized_node = re.sub(r'[^a-z0-9-]', '-', self.node_name.lower())
        sanitized_node = re.sub(r'-+', '-', sanitized_node)  # Collapse multiple dashes
        sanitized_node = sanitized_node.strip('-')  # Remove leading/trailing dashes
        
        self.lease_name = f"aswarm-sentinel-{sanitized_node}"
        
        # Signal state
        self.sequence = 0
        self.anomaly_signals = 0
        self.last_reset = time.time()
        
        # Initialize UDP fast-path if enabled
        self.fastpath_sender = None
        self.fastpath_enabled = fastpath_enabled
        
        if fastpath_enabled and FastPathSender and fastpath_host:
            try:
                # Get fast-path key from environment
                fastpath_key = os.environ.get('ASWARM_FASTPATH_KEY')
                if fastpath_key:
                    self.fastpath_sender = FastPathSender(
                        host=fastpath_host,
                        port=fastpath_port,
                        shared_key=fastpath_key,
                        dupes=2,  # Send duplicates for reliability
                        gap_ms=5
                    )
                    logger.info(f"Fast-path enabled: {fastpath_host}:{fastpath_port}")
                else:
                    logger.warning("Fast-path disabled: ASWARM_FASTPATH_KEY not set")
            except Exception as e:
                logger.error(f"Failed to initialize fast-path: {e}")
        
        logger.info(f"Sentinel telemetry initialized: lease={self.lease_name}, "
                   f"cadence={cadence_ms}ms, fastpath={bool(self.fastpath_sender)}")
    
    def generate_packet_sketch(self):
        """Generate packet sketch (eBPF/conntrack simulation)
        Returns dict with port/protocol buckets and anomaly indicators
        """
        # In production: read from eBPF maps or conntrack
        # For demo: simulate realistic patterns with occasional anomalies
        
        normal_sketch = {
            "tcp_22": random.randint(0, 5),      # SSH
            "tcp_80": random.randint(5, 20),     # HTTP
            "tcp_443": random.randint(10, 30),   # HTTPS
            "tcp_6443": random.randint(0, 8),    # K8s API
            "udp_53": random.randint(2, 10),     # DNS
            "tcp_other": random.randint(0, 5)    # Other protocols
        }
        
        # Simulate port scanning anomaly
        if self.anomaly_signals > 0:
            # High connection attempts to unusual ports
            normal_sketch.update({
                "tcp_3306": random.randint(5, 15),   # MySQL
                "tcp_5432": random.randint(3, 12),   # PostgreSQL
                "tcp_6379": random.randint(2, 8),    # Redis
                "tcp_8080": random.randint(4, 16),   # Alt HTTP
                "tcp_9200": random.randint(1, 6),    # Elasticsearch
                "scan_ports": random.randint(8, 25)  # Anomaly indicator
            })
            self.anomaly_signals -= 1
        
        return normal_sketch
    
    def generate_process_graph(self):
        """Generate process graph delta (PID lineage changes)
        Returns dict with node/edge counts and anomaly flags
        """
        base_graph = {
            "nodes": random.randint(15, 25),
            "edges": random.randint(12, 22),
            "new_procs": random.randint(0, 3),
            "term_procs": random.randint(0, 2)
        }
        
        # Add anomaly indicators during scan simulation
        if self.anomaly_signals > 0:
            base_graph.update({
                "new_procs": random.randint(3, 8),   # Rapid process spawning
                "network_procs": random.randint(2, 5), # Network-heavy processes
                "anomaly_score": random.uniform(0.7, 0.9)
            })
        
        return base_graph
    
    def trigger_anomaly_simulation(self, duration_signals=10):
        """Trigger anomaly simulation for next N signals"""
        self.anomaly_signals = duration_signals
        print(f"Anomaly simulation triggered for {duration_signals} signals", flush=True)
    
    def score_signal(self, sketch: dict, graph: dict) -> float:
        """Simple bounded score with EWMA on unusual port and proc churn"""
        ports = sketch.get("scan_ports", 0)
        churn = graph.get("new_procs", 0) + graph.get("network_procs", 0)
        raw = 0.7*min(ports/10.0, 1.0) + 0.3*min(churn/8.0, 1.0)
        # EWMA to smooth
        alpha = 0.4
        prev = getattr(self, "_ewma", 0.0)
        ewma = alpha*raw + (1-alpha)*prev
        self._ewma = ewma
        return ewma
    
    def update_lease(self, score: float, elevate: bool, run_id: str = None) -> tuple[str, bool]:
        """Minimal merge-patch to the Lease for low latency and low churn."""
        now = datetime.now(timezone.utc).isoformat()
        self.sequence += 1

        # Keep annotations tiny
        ann = {
            "aswarm.ai/seq": str(self.sequence),
            "aswarm.ai/score": f"{score:.3f}",
            "aswarm.ai/ts": now,
        }
        if run_id:
            ann["aswarm.ai/run-id"] = run_id
        if elevate:
            ann["aswarm.ai/elevate"] = "true"
            ann["aswarm.ai/elevate-ts"] = now

        # Add labels for run scoping and cleanup
        labels = {
            "app.kubernetes.io/component": "sentinel",
            "aswarm.ai/node": self.node_name
        }
        if run_id:
            labels["aswarm.ai/run-id"] = run_id
            
        patch = {
            "metadata": {
                "annotations": ann,
                "labels": labels
            },
            "spec": {
                "holderIdentity": f"sentinel-{self.node_name}",
                "renewTime": now,
                "leaseDurationSeconds": 5
            }
        }

        # Try patch, create on 404, retry on conflict
        for attempt in range(3):
            try:
                self.coordination_v1.patch_namespaced_lease(
                    name=self.lease_name,
                    namespace=self.namespace,
                    body=patch
                )
                return now, elevate
            except ApiException as e:
                if e.status == 404:
                    body = client.V1Lease(
                        metadata=client.V1ObjectMeta(
                            name=self.lease_name, 
                            namespace=self.namespace,
                            labels=labels
                        ),
                        spec=client.V1LeaseSpec(
                            holder_identity=f"sentinel-{self.node_name}",
                            lease_duration_seconds=5,
                        ),
                    )
                    try:
                        self.coordination_v1.create_namespaced_lease(self.namespace, body)
                        # loop will patch next iteration
                    except ApiException as ce:
                        if ce.status not in (409,):
                            print(f"Create lease error: {ce}", flush=True)
                            break
                elif e.status in (409, 429, 500):
                    time.sleep(0.01 * (attempt + 1))  # tiny backoff
                    continue
                else:
                    print(f"Lease patch error: {e}", flush=True)
                    break
        return now, elevate
    
    def run_telemetry_loop(self, duration_s=None, run_id=None):
        """Run main telemetry loop with jittered cadence and hysteresis
        
        Args:
            duration_s: Run for specified seconds (None = infinite)
            run_id: Optional run identifier for scoping
        """
        start = time.perf_counter()
        print(f"Starting telemetry loop: {self.cadence_s*1000:.0f}ms cadence, run_id={run_id}", flush=True)
        
        while True:
            loop_start = time.perf_counter()
            sketch = self.generate_packet_sketch()
            graph = self.generate_process_graph()
            score = self.score_signal(sketch, graph)

            # Hysteresis: require 2 consecutive windows > 0.7 for elevation
            high = score > 0.7
            prev_high = getattr(self, "_prev_high", False)
            elevate = high and prev_high
            self._prev_high = high

            ts, elevated = self.update_lease(score, elevate, run_id)
            
            # Send via UDP fast-path for high-confidence signals
            fastpath_sent = False
            if self.fastpath_sender and score >= 0.90:  # High confidence threshold
                try:
                    anomaly_data = {
                        'score': score,
                        'witness_count': 1,  # Single node witness
                        'selector': f'node={self.node_name}',
                        'event_type': 'port_scan' if sketch.get('scan_ports', 0) > 5 else 'process_anomaly',
                        'detection_window_ms': int(self.cadence_s * 1000),
                        'sketch': sketch,
                        'graph': graph
                    }
                    
                    stats = self.fastpath_sender.send_elevation(anomaly_data, run_id=run_id)
                    fastpath_sent = True
                    logger.debug(f"Fast-path sent: seq={self.sequence}, latency={stats.send_ms_first:.1f}ms")
                except Exception as e:
                    logger.error(f"Fast-path send failed: {e}")

            # structured log for local debug
            log_entry = {
                "ts": ts,
                "node": self.node_name,
                "seq": self.sequence,
                "score": round(score, 3),
                "elevate": elevated,
                "fastpath": fastpath_sent,
                "run_id": run_id
            }
            print(json.dumps(log_entry), flush=True)

            if duration_s and (time.perf_counter() - start) >= duration_s:
                break

            # jittered cadence to avoid lease write herds
            elapsed = time.perf_counter() - loop_start
            jitter = random.uniform(-0.01, 0.02)  # -10ms to +20ms jitter
            sleep_time = max(0, (self.cadence_s + jitter) - elapsed)
            time.sleep(sleep_time)
        
        logger.info(f"Telemetry loop completed: {self.sequence} signals")
        
        # Cleanup fast-path
        if self.fastpath_sender:
            self.fastpath_sender.close()

def main():
    """CLI entry point for Sentinel telemetry"""
    import argparse
    
    parser = argparse.ArgumentParser(description="A-SWARM Sentinel Telemetry v0.3")
    parser.add_argument("--namespace", default="aswarm", help="Kubernetes namespace")
    parser.add_argument("--cadence-ms", type=int, default=50, help="Signal cadence (ms)")
    parser.add_argument("--duration", type=int, help="Run duration (seconds)")
    parser.add_argument("--run-id", help="Run identifier for scoping")
    parser.add_argument("--trigger-anomaly", type=int, help="Trigger anomaly for N signals")
    parser.add_argument("--fastpath-host", help="UDP fast-path target host")
    parser.add_argument("--fastpath-port", type=int, default=8888, help="UDP fast-path port")
    parser.add_argument("--no-fastpath", action="store_true", help="Disable UDP fast-path")
    
    args = parser.parse_args()
    
    # Auto-detect Pheromone service if not specified
    fastpath_host = args.fastpath_host
    if not fastpath_host and not args.no_fastpath:
        try:
            # Try to resolve Pheromone service
            import socket
            fastpath_host = f"aswarm-pheromone.{args.namespace}.svc.cluster.local"
            socket.gethostbyname(fastpath_host)  # Test resolution
            logger.info(f"Auto-detected fast-path target: {fastpath_host}")
        except:
            logger.warning("Could not auto-detect Pheromone service")
    
    sentinel = DualPathTelemetry(
        namespace=args.namespace,
        cadence_ms=args.cadence_ms,
        fastpath_host=fastpath_host,
        fastpath_port=args.fastpath_port,
        fastpath_enabled=not args.no_fastpath
    )
    
    if args.trigger_anomaly:
        sentinel.trigger_anomaly_simulation(args.trigger_anomaly)
    
    try:
        sentinel.run_telemetry_loop(
            duration_s=args.duration,
            run_id=args.run_id
        )
    except KeyboardInterrupt:
        print("\nTelemetry stopped by user", flush=True)

if __name__ == "__main__":
    main()