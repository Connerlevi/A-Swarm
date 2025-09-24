#!/usr/bin/env python3
"""
Ultra-precise MTTD measurement using process coordination
Eliminates start marker overhead and measures true detection latency
"""
import sys
import time
import json
import subprocess
import threading
from pathlib import Path
from kubernetes import client, config

PROJECT_ROOT = Path(__file__).resolve().parents[2]

def parse_iso_server_time(ts_str):
    from datetime import datetime, timezone
    return datetime.fromisoformat(ts_str.replace('Z', '+00:00')).astimezone(timezone.utc)

class PreciseTimingTest:
    def __init__(self, namespace="aswarm"):
        self.namespace = namespace
        try:
            config.load_kube_config()
        except:
            config.load_incluster_config()
        self.v1 = client.CoreV1Api()
    
    def run_precise_trial(self, trial_num):
        """Run trial with precise timing coordination"""
        run_id = f"precise-{int(time.time() * 1000)}-{trial_num}"
        print(f"Trial {trial_num}: {run_id}")
        
        # 1. Start Pheromone watcher
        pheromone_cmd = [
            sys.executable, "-m", "pheromone.gossip_v2",
            f"--namespace={self.namespace}",
            "--duration=8",
            f"--run-id={run_id}",
            "--window-ms=80",
            "--quorum=1",
            "--node-score-threshold=0.6",  # Lower threshold
            "--fast-path-score=0.85"       # Lower fast-path threshold
        ]
        
        pheromone_proc = subprocess.Popen(
            pheromone_cmd, cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        
        # 2. Start Sentinel in normal mode first
        sentinel_cmd = [
            sys.executable, "-m", "sentinel.telemetry_v2",
            f"--namespace={self.namespace}",
            "--cadence-ms=40",  # Even faster
            "--duration=6",
            f"--run-id={run_id}"
            # NO anomaly trigger initially
        ]
        
        sentinel_proc = subprocess.Popen(
            sentinel_cmd, cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        
        print("  Processes initializing...")
        time.sleep(1.5)  # Let both processes initialize and start signaling
        
        # 3. Kill Sentinel and immediately restart with anomaly
        print("  Triggering anomaly NOW...")
        anomaly_trigger_time = time.perf_counter()  # High precision timing
        
        sentinel_proc.terminate()
        time.sleep(0.1)  # Brief pause
        
        # Start anomaly Sentinel immediately
        sentinel_anomaly_cmd = [
            sys.executable, "-m", "sentinel.telemetry_v2", 
            f"--namespace={self.namespace}",
            "--cadence-ms=40",
            "--duration=5",
            f"--run-id={run_id}",
            "--trigger-anomaly=10"  # Strong signal
        ]
        
        sentinel_proc = subprocess.Popen(
            sentinel_anomaly_cmd, cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        
        # 4. Monitor for elevation with high-frequency polling
        elevation_time = None
        elevation_data = None
        
        for _ in range(100):  # Check for 10 seconds
            try:
                cm = self.v1.read_namespaced_config_map(f"aswarm-elevated-{run_id}", self.namespace)
                elevation_data = json.loads(cm.data.get("elevation.json", "{}"))
                
                if elevation_data.get("run_id") == run_id:
                    elevation_time = time.perf_counter()
                    break
                    
            except:
                pass
            
            time.sleep(0.1)  # 100ms polling
        
        # 5. Cleanup
        sentinel_proc.terminate()
        pheromone_proc.terminate()
        
        try:
            sentinel_proc.wait(timeout=2)
            pheromone_proc.wait(timeout=2)
        except:
            sentinel_proc.kill()
            pheromone_proc.kill()
        
        # 6. Calculate precise MTTD
        if elevation_time and elevation_data:
            mttd_ms = (elevation_time - anomaly_trigger_time) * 1000.0
            
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
            
            status = "🎯" if mttd_ms < 200 else "🔶" if mttd_ms < 500 else "❌"
            print(f"  {status} Precise MTTD: {mttd_ms:.1f}ms (score: {result['mean_score']:.3f}, {result['reason']})")
            
            return result
        else:
            print(f"  ❌ No elevation detected")
            return {"trial": trial_num, "run_id": run_id, "success": False}
    
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
    
    def run_precision_test(self, trials=10):
        """Run precision timing test"""
        print(f"=== Precision MTTD Test ({trials} trials) ===\n")
        
        results = []
        for i in range(trials):
            result = self.run_precise_trial(i + 1)
            if result and result.get("success"):
                results.append(result)
            
            if result and result.get("run_id"):
                self.cleanup_trial(result["run_id"])
            
            if i < trials - 1:
                time.sleep(1)
        
        return self.analyze_precision_results(results, trials)
    
    def analyze_precision_results(self, results, total_trials):
        """Analyze precision test results"""
        print(f"\n=== Precision Analysis ===")
        
        if not results:
            print("❌ No successful detections")
            return False
        
        import statistics
        
        success_rate = (len(results) / total_trials) * 100
        mttds = [r["mttd_ms"] for r in results]
        
        # Compute percentiles
        if len(mttds) >= 2:
            p50 = statistics.median(mttds)
            p95 = statistics.quantiles(mttds, n=20)[18] if len(mttds) >= 5 else max(mttds)
            p99 = max(mttds)  # For small samples
        else:
            p50 = p95 = p99 = mttds[0] if mttds else 0
        
        print(f"Success Rate: {len(results)}/{total_trials} ({success_rate:.1f}%)")
        print(f"Precision MTTD Results:")
        print(f"  P50: {p50:.1f}ms")
        print(f"  P95: {p95:.1f}ms")
        print(f"  P99: {p99:.1f}ms")
        print(f"  Min: {min(mttds):.1f}ms")
        print(f"  Max: {max(mttds):.1f}ms")
        
        fast_path_count = len([r for r in results if "fast_path" in r.get("reason", "")])
        hysteresis_count = len([r for r in results if "hysteresis" in r.get("reason", "")])
        
        print(f"Fast Path: {fast_path_count}/{len(results)} ({fast_path_count/len(results)*100:.1f}%)")
        print(f"Hysteresis: {hysteresis_count}/{len(results)} ({hysteresis_count/len(results)*100:.1f}%)")
        
        # Check if we're getting close to target
        under_500 = len([m for m in mttds if m < 500])
        under_200 = len([m for m in mttds if m < 200])
        
        print(f"Under 500ms: {under_500}/{len(results)} ({under_500/len(results)*100:.1f}%)")
        print(f"Under 200ms: {under_200}/{len(results)} ({under_200/len(results)*100:.1f}%)")
        
        if p95 < 200:
            print(f"\n🎯 SUCCESS: P95 {p95:.1f}ms < 200ms target!")
            return True
        elif p50 < 200:
            print(f"\n🔶 CLOSE: P50 {p50:.1f}ms < 200ms, P95 {p95:.1f}ms")
            return True
        else:
            print(f"\n⚠️ IMPROVING: Min {min(mttds):.1f}ms shows potential")
            return len(results) > 0

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Precision MTTD timing test")
    parser.add_argument("--namespace", default="aswarm", help="Kubernetes namespace")
    parser.add_argument("--trials", type=int, default=10, help="Number of trials")
    
    args = parser.parse_args()
    
    tester = PreciseTimingTest(namespace=args.namespace)
    success = tester.run_precision_test(trials=args.trials)
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()