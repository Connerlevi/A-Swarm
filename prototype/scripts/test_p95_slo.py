#!/usr/bin/env python3
"""
P95 MTTD <200ms SLO validation test (20 trials)
Uses optimized parameters for sub-200ms target
"""
import sys
import time
import json
import statistics
import subprocess
from pathlib import Path
from kubernetes import client, config

PROJECT_ROOT = Path(__file__).resolve().parents[2]

def parse_iso_server_time(ts_str):
    """Parse server timestamp to UTC datetime"""
    from datetime import datetime, timezone
    return datetime.fromisoformat(ts_str.replace('Z', '+00:00')).astimezone(timezone.utc)

class P95SLOValidator:
    def __init__(self, namespace="aswarm"):
        self.namespace = namespace
        try:
            config.load_kube_config()
        except:
            config.load_incluster_config()
        self.v1 = client.CoreV1Api()
    
    def run_single_trial(self, trial_num, total_trials):
        """Run single optimized trial for <200ms MTTD"""
        run_id = f"slo-{int(time.time() * 1000)}-{trial_num}"
        print(f"Trial {trial_num}/{total_trials}: {run_id}")
        
        # 1. Start optimized Pheromone
        pheromone_cmd = [
            sys.executable, "-m", "pheromone.gossip_v2",
            f"--namespace={self.namespace}",
            "--duration=8",
            f"--run-id={run_id}",
            "--window-ms=80",           # Optimized: 80ms window
            "--quorum=1",               # Single witness for speed
            "--node-score-threshold=0.7",
            "--fast-path-score=0.90"    # Fast-path elevation
        ]
        
        pheromone_proc = subprocess.Popen(
            pheromone_cmd, cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        
        time.sleep(1)  # Minimal Pheromone init
        
        # 2. Create anomaly start marker (t0)
        t0 = self.create_start_marker(run_id)
        if not t0:
            return None
        
        # 3. Start optimized Sentinel with immediate anomaly
        sentinel_cmd = [
            sys.executable, "-m", "sentinel.telemetry_v2",
            f"--namespace={self.namespace}",
            "--cadence-ms=50",          # Optimized: 50ms cadence
            "--duration=6",
            f"--run-id={run_id}",
            "--trigger-anomaly=8"       # Strong immediate signal
        ]
        
        sentinel_proc = subprocess.Popen(
            sentinel_cmd, cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        
        # 4. Wait for elevation with precise timing
        t1, elevation_data = self.wait_for_elevation(run_id, timeout=6)
        
        # 5. Cleanup
        sentinel_proc.terminate()
        pheromone_proc.terminate()
        
        try:
            sentinel_proc.wait(timeout=2)
            pheromone_proc.wait(timeout=2) 
        except:
            sentinel_proc.kill()
            pheromone_proc.kill()
        
        # 6. Compute MTTD
        if t1 and elevation_data:
            # Use precise decision timestamp from elevation data if available
            decision_ts_str = elevation_data.get("decision_ts_server")
            if decision_ts_str:
                try:
                    t1_precise = parse_iso_server_time(decision_ts_str)
                    mttd_ms = (t1_precise - t0).total_seconds() * 1000.0
                except:
                    mttd_ms = (t1 - t0).total_seconds() * 1000.0
            else:
                mttd_ms = (t1 - t0).total_seconds() * 1000.0
            
            result = {
                "trial": trial_num,
                "run_id": run_id, 
                "mttd_ms": mttd_ms,
                "witness_count": elevation_data.get("witness_count", 0),
                "mean_score": elevation_data.get("mean_score", 0.0),
                "p95_score": elevation_data.get("p95_score", 0.0),
                "reason": elevation_data.get("reason", "unknown"),
                "success": True
            }
            
            status = "✅" if mttd_ms < 200 else "🔶" if mttd_ms < 500 else "❌"
            print(f"  {status} MTTD: {mttd_ms:.1f}ms, witnesses: {result['witness_count']}, score: {result['mean_score']:.3f}")
            
            return result
        else:
            print(f"  ❌ No elevation detected")
            return {"trial": trial_num, "run_id": run_id, "success": False}
    
    def create_start_marker(self, run_id):
        """Create anomaly start marker with server timestamp"""
        cm_name = f"aswarm-anomaly-start-{run_id}"
        
        cm = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(
                name=cm_name,
                namespace=self.namespace,
                labels={"aswarm.ai/run-id": run_id, "type": "anomaly-start"}
            ),
            data={"run_id": run_id}
        )
        
        try:
            created_cm = self.v1.create_namespaced_config_map(self.namespace, cm)
            creation_ts = created_cm.metadata.creation_timestamp
            if isinstance(creation_ts, str):
                t0 = parse_iso_server_time(creation_ts)
            else:
                t0 = creation_ts.replace(tzinfo=timezone.utc) if creation_ts.tzinfo is None else creation_ts
            return t0
        except Exception as e:
            print(f"  Failed to create start marker: {e}")
            return None
    
    def wait_for_elevation(self, run_id, timeout=6):
        """Wait for elevation artifact and return timestamps"""
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
                        t1 = creation_ts.replace(tzinfo=timezone.utc) if creation_ts.tzinfo is None else creation_ts
                    return t1, elevation_data
                    
            except Exception as e:
                if "NotFound" not in str(e):
                    print(f"  Elevation check error: {e}")
            
            time.sleep(0.05)  # 50ms polling for precision
        
        return None, None
    
    def cleanup_trial(self, run_id):
        """Clean up trial artifacts"""
        try:
            cms = self.v1.list_namespaced_config_map(
                self.namespace, label_selector=f"aswarm.ai/run-id={run_id}"
            )
            for cm in cms.items:
                self.v1.delete_namespaced_config_map(cm.metadata.name, self.namespace)
        except:
            pass
    
    def run_p95_validation(self, trials=20):
        """Run P95 MTTD validation with specified number of trials"""
        print(f"=== P95 MTTD <200ms SLO Validation ({trials} trials) ===\\n")
        
        results = []
        successful_results = []
        
        for i in range(trials):
            result = self.run_single_trial(i + 1, trials)
            if result:
                results.append(result)
                if result.get("success"):
                    successful_results.append(result)
            
            # Cleanup between trials
            if result and result.get("run_id"):
                self.cleanup_trial(result["run_id"])
            
            # Brief pause between trials
            if i < trials - 1:
                time.sleep(0.5)
        
        return self.analyze_results(successful_results, trials)
    
    def analyze_results(self, results, total_trials):
        """Analyze results and validate P95 SLO"""
        print(f"\\n=== SLO Analysis ===")
        
        if not results:
            print("❌ No successful detections")
            return False
        
        success_rate = (len(results) / total_trials) * 100
        mttds = [r["mttd_ms"] for r in results]
        
        # Compute percentiles
        if len(mttds) >= 2:
            p50 = statistics.median(mttds)
            p95 = statistics.quantiles(mttds, n=20)[18] if len(mttds) >= 5 else max(mttds)
            p99 = statistics.quantiles(mttds, n=100)[98] if len(mttds) >= 10 else max(mttds)
        else:
            p50 = p95 = p99 = mttds[0] if mttds else 0
        
        print(f"Success Rate: {len(results)}/{total_trials} ({success_rate:.1f}%)")
        print(f"MTTD Percentiles:")
        print(f"  P50: {p50:.1f}ms")
        print(f"  P95: {p95:.1f}ms ({'✅ PASS' if p95 <= 200 else '❌ FAIL'} - SLO target)")
        print(f"  P99: {p99:.1f}ms") 
        print(f"  Max: {max(mttds):.1f}ms")
        print(f"  Min: {min(mttds):.1f}ms")
        
        # Additional metrics
        witness_counts = [r["witness_count"] for r in results]
        mean_scores = [r["mean_score"] for r in results]
        fast_path_count = len([r for r in results if "fast_path" in r.get("reason", "")])
        
        print(f"Average Witnesses: {statistics.mean(witness_counts):.1f}")
        print(f"Average Mean Score: {statistics.mean(mean_scores):.3f}")
        print(f"Fast Path Elevations: {fast_path_count}/{len(results)} ({fast_path_count/len(results)*100:.1f}%)")
        
        # SLO validation
        slo_pass = p95 <= 200.0 and success_rate >= 90.0
        
        print(f"\\n=== SLO VERDICT ===")
        if slo_pass:
            print(f"🎯 PASS: P95 MTTD {p95:.1f}ms ≤ 200ms, {success_rate:.1f}% success rate")
        else:
            print(f"❌ FAIL: P95 MTTD {p95:.1f}ms {'>' if p95 > 200 else '≤'} 200ms, {success_rate:.1f}% success rate")
            
        return slo_pass

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="P95 MTTD <200ms SLO Validation")
    parser.add_argument("--namespace", default="aswarm", help="Kubernetes namespace")
    parser.add_argument("--trials", type=int, default=20, help="Number of test trials")
    
    args = parser.parse_args()
    
    validator = P95SLOValidator(namespace=args.namespace)
    slo_pass = validator.run_p95_validation(trials=args.trials)
    
    sys.exit(0 if slo_pass else 1)

if __name__ == "__main__":
    main()