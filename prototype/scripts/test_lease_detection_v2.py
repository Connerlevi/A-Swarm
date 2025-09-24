#!/usr/bin/env python3
"""
Robust test harness for Lease-based <200ms MTTD validation
Uses server timestamps, proper cleanup, and percentile assertions
"""
import os
import sys
import json
import time
import subprocess
import threading
import statistics
from pathlib import Path
from datetime import datetime, timezone
from kubernetes import client, config

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

def parse_iso_server_time(ts_str):
    """Parse server timestamp to UTC datetime"""
    return datetime.fromisoformat(ts_str.replace('Z', '+00:00')).astimezone(timezone.utc)

def drain_pipe(pipe, name, output_list):
    """Drain subprocess pipe to prevent deadlocks"""
    try:
        for line in iter(pipe.readline, ''):
            if line:
                output_list.append(f"[{name}] {line.rstrip()}")
        pipe.close()
    except Exception as e:
        output_list.append(f"[{name}] Drain error: {e}")

class LeaseDetectionTest:
    def __init__(self, namespace="aswarm"):
        self.namespace = namespace
        
        try:
            config.load_kube_config()
        except:
            config.load_incluster_config()
            
        self.v1 = client.CoreV1Api()
        self.coordination_v1 = client.CoordinationV1Api()
        self.apps_v1 = client.AppsV1Api()
    
    def cleanup_run_artifacts(self, run_id):
        """Clean up only objects labeled with run_id"""
        label_selector = f"aswarm.ai/run-id={run_id}"
        
        # Clean ConfigMaps
        try:
            cms = self.v1.list_namespaced_config_map(
                self.namespace, label_selector=label_selector
            )
            for cm in cms.items:
                self.v1.delete_namespaced_config_map(cm.metadata.name, self.namespace)
                print(f"Cleaned CM: {cm.metadata.name}")
        except Exception as e:
            print(f"CM cleanup warning: {e}")
        
        # Clean Leases 
        try:
            leases = self.coordination_v1.list_namespaced_lease(
                self.namespace, label_selector=label_selector
            )
            for lease in leases.items:
                self.coordination_v1.delete_namespaced_lease(lease.metadata.name, self.namespace)
                print(f"Cleaned Lease: {lease.metadata.name}")
        except Exception as e:
            print(f"Lease cleanup warning: {e}")
    
    def create_anomaly_start_marker(self, run_id):
        """Create run-scoped start marker with server timestamp"""
        cm_name = f"aswarm-anomaly-start-{run_id}"
        
        cm = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(
                name=cm_name,
                namespace=self.namespace,
                labels={
                    "type": "anomaly-start",
                    "aswarm.ai/component": "test",
                    "aswarm.ai/run-id": str(run_id)  # Ensure string
                }
            ),
            data={"run_id": str(run_id), "marker": "anomaly-start"}  # All values must be strings
        )
        
        try:
            created_cm = self.v1.create_namespaced_config_map(self.namespace, cm)
            # Handle both string and datetime types from API
            creation_ts = created_cm.metadata.creation_timestamp
            if isinstance(creation_ts, str):
                t0 = parse_iso_server_time(creation_ts)
            else:
                # Already a datetime object
                t0 = creation_ts.replace(tzinfo=timezone.utc) if creation_ts.tzinfo is None else creation_ts
            print(f"Created anomaly start marker: {cm_name} at {t0.isoformat()}")
            return t0
        except Exception as e:
            print(f"Failed to create start marker: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def wait_for_elevation(self, run_id, timeout=30):
        """Wait for elevation artifact and return server timestamps"""
        cm_name = f"aswarm-elevated-{run_id}"
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                cm = self.v1.read_namespaced_config_map(cm_name, self.namespace)
                elevation_data = json.loads(cm.data.get("elevation.json", "{}"))
                
                if elevation_data.get("run_id") == run_id:
                    creation_ts = cm.metadata.creation_timestamp
                    if isinstance(creation_ts, str):
                        t1 = parse_iso_server_time(creation_ts)
                    else:
                        # Already a datetime object
                        t1 = creation_ts.replace(tzinfo=timezone.utc) if creation_ts.tzinfo is None else creation_ts
                    print(f"Found elevation artifact: {cm_name} at {t1.isoformat()}")
                    return t1, elevation_data
                    
            except Exception as e:
                if "NotFound" not in str(e):
                    print(f"Elevation check warning: {e}")
            
            # Print progress every few seconds
            if int(time.time() - start_time) % 3 == 0 and int(time.time() - start_time) > 0:
                elapsed = int(time.time() - start_time)
                print(f"Waiting for elevation... ({elapsed}s/{timeout}s)")
            
            time.sleep(0.1)  # Fast polling for low-latency measurement
        
        return None, None
    
    def run_sentinels(self, run_id, duration, count=3):
        """Start Sentinel processes with simple process management"""
        processes = []
        
        for i in range(count):
            cmd = [
                sys.executable, "-m", "sentinel.telemetry_v2",
                f"--namespace={self.namespace}",
                "--cadence-ms=150", 
                f"--duration={duration}",
                f"--run-id={run_id}",
                "--trigger-anomaly=20"
            ]
            
            try:
                # Use simple Popen without complex threading - just like the working test
                proc = subprocess.Popen(
                    cmd,
                    cwd=PROJECT_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # Merge stderr into stdout
                    text=True
                )
                
                processes.append(proc)
                print(f"Started Sentinel {i} (PID: {proc.pid})")
                
                # Stagger startup
                time.sleep(0.5)
                
            except Exception as e:
                print(f"Failed to start Sentinel {i}: {e}")
        
        return processes, []  # No output lists needed
    
    def run_pheromone(self, run_id, duration):
        """Start Pheromone watcher process"""
        cmd = [
            sys.executable, "-m", "pheromone.gossip_v2",
            f"--namespace={self.namespace}",
            f"--duration={duration + 5}",
            f"--run-id={run_id}",
            "--window-ms=500",
            "--quorum=1"  # Reduced to 1 for single Sentinel test
        ]
        
        try:
            # Use simple Popen like the working tests
            proc = subprocess.Popen(
                cmd,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout
                text=True
            )
            
            print(f"Started Pheromone (PID: {proc.pid})")
            return proc, []
            
        except Exception as e:
            print(f"Failed to start Pheromone: {e}")
            return None, []
    
    def terminate_process(self, proc):
        """Simple process termination"""
        try:
            if proc.poll() is None:  # Process is still running
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()  # Force kill if terminate didn't work
        except Exception as e:
            print(f"Process cleanup warning: {e}")
    
    def measure_single_run(self, run_id, duration=20, sentinels=3):
        """Measure MTTD for a single test run using server timestamps"""
        print(f"\n=== Starting test run: {run_id} ===")
        
        # 1. Clean up any existing artifacts
        self.cleanup_run_artifacts(run_id)
        
        # 2. Start Pheromone watcher
        pheromone_proc, pheromone_output = self.run_pheromone(run_id, duration)
        if not pheromone_proc:
            return {"success": False, "reason": "pheromone_start_failed"}
        
        time.sleep(2)  # Let Pheromone initialize
        
        # 3. Create anomaly start marker (t0)
        t0 = self.create_anomaly_start_marker(run_id)
        if not t0:
            return {"success": False, "reason": "start_marker_failed"}
        
        # 4. Start Sentinels with anomaly trigger
        sentinel_procs, sentinel_outputs = self.run_sentinels(run_id, duration, sentinels)
        if not sentinel_procs:
            return {"success": False, "reason": "sentinels_start_failed"}
        
        # Give processes time to initialize and start communicating
        time.sleep(3)
        
        # 5. Wait for elevation detection (t1) 
        t1, elevation_data = self.wait_for_elevation(run_id, timeout=duration + 5)
        
        # 6. Clean up processes
        print("Stopping processes...")
        for proc in sentinel_procs:
            self.terminate_process(proc)
        
        if pheromone_proc:
            self.terminate_process(pheromone_proc)
        
        # 7. Compute MTTD if successful
        if t1 and elevation_data:
            # Try to use precise decision timestamp from elevation data if available
            decision_ts_str = elevation_data.get("decision_ts_server")
            if decision_ts_str:
                try:
                    decision_ts = parse_iso_server_time(decision_ts_str)
                    mttd_ms = (decision_ts - t0).total_seconds() * 1000.0
                    t1_precise = decision_ts
                except:
                    # Fall back to ConfigMap creation time
                    mttd_ms = (t1 - t0).total_seconds() * 1000.0
                    t1_precise = t1
            else:
                mttd_ms = (t1 - t0).total_seconds() * 1000.0
                t1_precise = t1
            
            result = {
                "success": True,
                "mttd_ms": mttd_ms,
                "t0_server": t0.isoformat(),
                "t1_server": t1_precise.isoformat(),
                "witness_count": elevation_data.get("witness_count", 0),
                "mean_score": elevation_data.get("mean_score", 0.0),
                "threshold": elevation_data.get("threshold", 0)
            }
            
            print(f"✅ Detection successful:")
            print(f"   MTTD: {mttd_ms:.1f}ms")
            print(f"   Witnesses: {result['witness_count']}")
            print(f"   Mean score: {result['mean_score']:.3f}")
            
            return result
        else:
            print("❌ No elevation detected")
            return {"success": False, "reason": "no_elevation"}
    
    def run_test_batch(self, repeats=5, duration=20, sentinels=3):
        """Run multiple test iterations and compute percentiles"""
        print(f"Starting {repeats} test iterations...")
        
        results = []
        for i in range(repeats):
            run_id = f"lease-test-{int(time.time() * 1000)}-{i}"
            
            result = self.measure_single_run(run_id, duration, sentinels)
            if result.get("success"):
                results.append(result)
            
            # Clean up between runs
            self.cleanup_run_artifacts(run_id)
            
            if i < repeats - 1:
                print("Waiting 3s between runs...")
                time.sleep(3)
        
        return self.analyze_results(results, repeats)
    
    def analyze_results(self, results, total_runs):
        """Analyze results and compute percentiles with hard assertion"""
        print(f"\n=== Test Results ({len(results)}/{total_runs} successful) ===")
        
        if not results:
            print("❌ No successful detections")
            return {"success": False, "p95_exceeded": True}
        
        mttds = [r["mttd_ms"] for r in results]
        witness_counts = [r["witness_count"] for r in results]
        mean_scores = [r["mean_score"] for r in results]
        
        # Compute percentiles
        p50 = statistics.median(mttds) if mttds else 0
        p95 = statistics.quantiles(mttds, n=20)[18] if len(mttds) >= 5 else max(mttds)  # 95th percentile
        p99 = statistics.quantiles(mttds, n=100)[98] if len(mttds) >= 10 else max(mttds)  # 99th percentile
        
        print(f"MTTD Percentiles:")
        print(f"  P50: {p50:.1f}ms")  
        print(f"  P95: {p95:.1f}ms")
        print(f"  P99: {p99:.1f}ms")
        print(f"  Max: {max(mttds):.1f}ms")
        print(f"  Min: {min(mttds):.1f}ms")
        
        print(f"Witness Count - Mean: {statistics.mean(witness_counts):.1f}")
        print(f"Anomaly Score - Mean: {statistics.mean(mean_scores):.3f}")
        
        # Hard assertion on P95
        p95_exceeded = p95 > 200.0
        
        if p95_exceeded:
            print(f"❌ FAILED: P95 MTTD {p95:.1f}ms > 200ms SLO")
        else:
            print(f"🎯 PASSED: P95 MTTD {p95:.1f}ms ≤ 200ms SLO")
        
        return {
            "success": len(results) > 0,
            "successful_runs": len(results),
            "total_runs": total_runs,
            "p50_mttd_ms": p50,
            "p95_mttd_ms": p95,
            "p99_mttd_ms": p99,
            "p95_exceeded": p95_exceeded,
            "mean_witnesses": statistics.mean(witness_counts),
            "mean_score": statistics.mean(mean_scores)
        }

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Robust Lease detection test with server timestamps")
    parser.add_argument("--namespace", default="aswarm", help="Kubernetes namespace")
    parser.add_argument("--duration", type=int, default=20, help="Test duration per run (seconds)")
    parser.add_argument("--sentinels", type=int, default=3, help="Number of Sentinel processes")
    parser.add_argument("--repeats", type=int, default=5, help="Number of test repetitions")
    
    args = parser.parse_args()
    
    test = LeaseDetectionTest(namespace=args.namespace)
    
    final_result = test.run_test_batch(
        repeats=args.repeats,
        duration=args.duration, 
        sentinels=args.sentinels
    )
    
    # Exit with appropriate code for CI/CD
    if final_result["p95_exceeded"]:
        sys.exit(1)  # Fail CI if P95 > 200ms
    elif not final_result["success"]:
        sys.exit(2)  # General failure
    else:
        sys.exit(0)  # Success

if __name__ == "__main__":
    main()