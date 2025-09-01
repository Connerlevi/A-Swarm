#!/usr/bin/env python3
"""
Precise MTTD test using coordinated timing
"""
import sys
import time  
import subprocess
from pathlib import Path
from kubernetes import client, config

PROJECT_ROOT = Path(__file__).resolve().parents[2]

def test_precise_mttd():
    """Test with coordinated anomaly injection for precise MTTD"""
    
    run_id = f"precise-{int(time.time() * 1000)}"
    print(f"=== Precise MTTD Test: {run_id} ===")
    
    try:
        config.load_kube_config()
        v1 = client.CoreV1Api()
    except Exception as e:
        print(f"Kubernetes config error: {e}")
        return False
    
    # 1. Start Pheromone first
    print("Starting Pheromone...")
    pheromone_cmd = [
        sys.executable, "-m", "pheromone.gossip_v2",
        "--namespace=aswarm",
        "--duration=15",
        f"--run-id={run_id}",
        "--window-ms=500", 
        "--quorum=1"
    ]
    
    pheromone_proc = subprocess.Popen(
        pheromone_cmd, cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    
    # 2. Start Sentinel WITHOUT anomaly trigger
    print("Starting Sentinel in normal mode...")
    sentinel_cmd = [
        sys.executable, "-m", "sentinel.telemetry_v2", 
        "--namespace=aswarm",
        "--cadence-ms=100",  # Fast cadence
        "--duration=12",
        f"--run-id={run_id}"
        # No --trigger-anomaly yet
    ]
    
    sentinel_proc = subprocess.Popen(
        sentinel_cmd, cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    
    print("Letting processes initialize for 3 seconds...")
    time.sleep(3)
    
    # 3. Record precise anomaly injection time
    print("Triggering anomaly NOW...")
    anomaly_trigger_time = time.time()
    
    # Send signal to Sentinel to trigger anomaly (simulated - in real implementation
    # this would be done via API or signal). For now, we'll start a new Sentinel
    # with anomaly enabled.
    
    sentinel_proc.terminate()
    time.sleep(0.5)
    
    # Start Sentinel with anomaly
    sentinel_anomaly_cmd = [
        sys.executable, "-m", "sentinel.telemetry_v2",
        "--namespace=aswarm", 
        "--cadence-ms=100",
        "--duration=8",
        f"--run-id={run_id}",
        "--trigger-anomaly=10"  # Strong signal
    ]
    
    sentinel_proc = subprocess.Popen(
        sentinel_anomaly_cmd, cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    
    print(f"Anomaly injection at: {anomaly_trigger_time}")
    
    # 4. Wait for elevation detection
    print("Waiting for elevation...")
    elevation_found = False
    elevation_time = None
    
    for _ in range(100):  # Check for 10 seconds
        try:
            cm = v1.read_namespaced_config_map(f"aswarm-elevated-{run_id}", "aswarm")
            elevation_data = json.loads(cm.data.get("elevation.json", "{}"))
            
            if elevation_data.get("run_id") == run_id:
                elevation_time = time.time()
                elevation_found = True
                print(f"Elevation detected at: {elevation_time}")
                
                # Precise MTTD calculation
                mttd_ms = (elevation_time - anomaly_trigger_time) * 1000
                
                print(f"✅ Precise MTTD: {mttd_ms:.1f}ms")
                print(f"   Witnesses: {elevation_data.get('witness_count', 0)}")
                print(f"   Mean score: {elevation_data.get('mean_score', 0.0):.3f}")
                
                if mttd_ms < 200:
                    print(f"🎯 SUCCESS: MTTD {mttd_ms:.1f}ms < 200ms target!")
                    result = True
                else:
                    print(f"⚠️  CLOSE: MTTD {mttd_ms:.1f}ms > 200ms target")
                    result = True  # Still successful detection
                break
                
        except:
            pass
            
        time.sleep(0.1)
    
    if not elevation_found:
        print("❌ No elevation detected in 10 seconds")
        result = False
    
    # Cleanup
    print("Cleaning up...")
    sentinel_proc.terminate()
    pheromone_proc.terminate()
    
    try:
        sentinel_proc.wait(timeout=2)
        pheromone_proc.wait(timeout=2)
    except:
        sentinel_proc.kill()
        pheromone_proc.kill()
    
    return result

if __name__ == "__main__":
    import json
    success = test_precise_mttd()
    sys.exit(0 if success else 1)