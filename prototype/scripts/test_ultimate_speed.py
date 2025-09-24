#!/usr/bin/env python3
"""
Ultimate speed test - most aggressive parameters possible
Tests theoretical limits of the Lease-based detection system
"""
import sys
import time
import json
import subprocess
from pathlib import Path
from kubernetes import client, config

PROJECT_ROOT = Path(__file__).resolve().parents[2]

class UltimateSpeedTest:
    def __init__(self, namespace="aswarm"):
        self.namespace = namespace
        try:
            config.load_kube_config()
        except:
            config.load_incluster_config()
        self.v1 = client.CoreV1Api()
    
    def run_ultimate_trial(self, trial_num):
        """Most aggressive possible configuration"""
        run_id = f"ultimate-{int(time.time() * 1000)}-{trial_num}"
        print(f"Ultimate Trial {trial_num}: {run_id}")
        
        # 1. Ultra-aggressive Pheromone
        pheromone_cmd = [
            sys.executable, "-m", "pheromone.gossip_v2",
            f"--namespace={self.namespace}",
            "--duration=6",
            f"--run-id={run_id}",
            "--window-ms=50",            # Minimal window
            "--quorum=1",                # Single witness
            "--node-score-threshold=0.5", # Very low threshold  
            "--fast-path-score=0.75"     # Aggressive fast-path
        ]
        
        pheromone_proc = subprocess.Popen(
            pheromone_cmd, cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        
        time.sleep(1)  # Minimal init
        
        # 2. Record trigger time and start aggressive Sentinel
        print("  TRIGGERING ANOMALY...")
        anomaly_trigger_time = time.perf_counter()
        
        # Ultra-aggressive Sentinel - immediate high signal
        sentinel_cmd = [
            sys.executable, "-m", "sentinel.telemetry_v2",
            f"--namespace={self.namespace}",
            "--cadence-ms=30",           # Maximum speed
            "--duration=5",
            f"--run-id={run_id}",
            "--trigger-anomaly=15"       # Maximum signal strength
        ]
        
        sentinel_proc = subprocess.Popen(
            sentinel_cmd, cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        
        # 3. Ultra-high frequency elevation monitoring
        elevation_time = None
        elevation_data = None
        
        for i in range(150):  # 15 second window
            try:
                cm = self.v1.read_namespaced_config_map(f"aswarm-elevated-{run_id}", self.namespace)
                elevation_data = json.loads(cm.data.get("elevation.json", "{}"))
                
                if elevation_data.get("run_id") == run_id:
                    elevation_time = time.perf_counter()
                    detection_iteration = i
                    break
                    
            except:
                pass
            
            time.sleep(0.1)  # 100ms polling
        
        # 4. Cleanup
        sentinel_proc.terminate()
        pheromone_proc.terminate()
        
        try:
            sentinel_proc.wait(timeout=1)
            pheromone_proc.wait(timeout=1)
        except:
            sentinel_proc.kill()
            pheromone_proc.kill()
        
        # 5. Results
        if elevation_time and elevation_data:
            mttd_ms = (elevation_time - anomaly_trigger_time) * 1000.0
            
            result = {
                "mttd_ms": mttd_ms,
                "detection_iteration": detection_iteration,
                "witness_count": elevation_data.get("witness_count", 0),
                "mean_score": elevation_data.get("mean_score", 0.0),
                "p95_score": elevation_data.get("p95_score", 0.0),
                "reason": elevation_data.get("reason", "unknown"),
                "window_ms": elevation_data.get("window_ms", 0),
                "success": True
            }
            
            status = "🎯" if mttd_ms < 200 else "🔶" if mttd_ms < 500 else "❌"
            print(f"  {status} ULTIMATE MTTD: {mttd_ms:.1f}ms")
            print(f"     Reason: {result['reason']}")
            print(f"     Score: mean={result['mean_score']:.3f}, p95={result['p95_score']:.3f}")
            print(f"     Window: {result['window_ms']}ms")
            print(f"     Detected at iteration {detection_iteration} (~{detection_iteration*100}ms polling)")
            
            return result
        else:
            print(f"  ❌ No elevation detected in 15 seconds")
            return {"success": False}
    
    def cleanup_all(self, run_id):
        """Aggressive cleanup"""
        try:
            cms = self.v1.list_namespaced_config_map(
                self.namespace, label_selector=f"aswarm.ai/run-id={run_id}"
            )
            for cm in cms.items:
                self.v1.delete_namespaced_config_map(cm.metadata.name, self.namespace)
                
            # Also cleanup Leases
            coordination_v1 = client.CoordinationV1Api()
            leases = coordination_v1.list_namespaced_lease(
                self.namespace, label_selector=f"aswarm.ai/run-id={run_id}"
            )
            for lease in leases.items:
                coordination_v1.delete_namespaced_lease(lease.metadata.name, self.namespace)
        except:
            pass
    
    def run_ultimate_test(self, trials=5):
        """Run ultimate speed test"""
        print(f"=== ULTIMATE SPEED TEST ({trials} trials) ===")
        print("Parameters: 30ms cadence, 50ms window, 0.5 threshold, 0.75 fast-path")
        print("Goal: Find theoretical minimum MTTD for Lease-based detection\\n")
        
        results = []
        for i in range(trials):
            result = self.run_ultimate_trial(i + 1)
            if result and result.get("success"):
                results.append(result)
            
            if result and "ultimate" in str(result):
                # Extract run_id for cleanup
                pass  # Cleanup handled per trial
            
            time.sleep(2)  # Pause between ultimate trials
            print()
        
        self.analyze_ultimate_results(results, trials)
        return len(results) > 0
    
    def analyze_ultimate_results(self, results, total_trials):
        """Analyze ultimate test results"""
        print(f"=== ULTIMATE ANALYSIS ===")
        
        if not results:
            print("❌ No successful detections")
            return
        
        import statistics
        
        mttds = [r["mttd_ms"] for r in results]
        success_rate = (len(results) / total_trials) * 100
        
        print(f"Success Rate: {len(results)}/{total_trials} ({success_rate:.1f}%)")
        print(f"ULTIMATE MTTD Results:")
        
        if mttds:
            print(f"  Best (Min): {min(mttds):.1f}ms")
            print(f"  Worst (Max): {max(mttds):.1f}ms")
            print(f"  Average: {statistics.mean(mttds):.1f}ms")
            if len(mttds) >= 2:
                print(f"  Median: {statistics.median(mttds):.1f}ms")
        
        # Analysis of detection paths
        fast_path = [r for r in results if "fast_path" in r.get("reason", "")]
        hysteresis = [r for r in results if "hysteresis" in r.get("reason", "")]
        
        if fast_path:
            fast_mttds = [r["mttd_ms"] for r in fast_path]
            print(f"  Fast Path: {len(fast_path)} trials, avg {statistics.mean(fast_mttds):.1f}ms")
        
        if hysteresis:
            hyst_mttds = [r["mttd_ms"] for r in hysteresis]
            print(f"  Hysteresis: {len(hysteresis)} trials, avg {statistics.mean(hyst_mttds):.1f}ms")
        
        # Check if any trial hit target
        under_200 = [r for r in results if r["mttd_ms"] < 200]
        under_500 = [r for r in results if r["mttd_ms"] < 500]
        
        print(f"\\nTARGET ANALYSIS:")
        print(f"  Under 200ms: {len(under_200)}/{len(results)}")
        print(f"  Under 500ms: {len(under_500)}/{len(results)}")
        
        if under_200:
            print(f"🎯 SUCCESS! {len(under_200)} trials achieved <200ms:")
            for r in under_200:
                print(f"    {r['mttd_ms']:.1f}ms via {r['reason']}")
        elif under_500:
            print(f"🔶 CLOSE! {len(under_500)} trials under 500ms - close to target")
        else:
            print(f"❌ No trials under 500ms - fundamental architecture limit")
            print(f"   Theoretical minimum with current parameters: ~{min(mttds):.0f}ms")

def main():
    tester = UltimateSpeedTest()
    success = tester.run_ultimate_test(trials=5)
    
    print(f"\\n=== CONCLUSION ===")
    if success:
        print("System operational - results show practical limits of Lease-based detection")
    else:
        print("System issues detected - need architecture fixes")

if __name__ == "__main__":
    main()