#!/usr/bin/env python3
"""
Minimal integration test to isolate the issue
"""
import sys
import time
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

def test_minimal_integration():
    print("=== Minimal Integration Test ===")
    
    run_id = f"minimal-{int(time.time())}"
    print(f"Run ID: {run_id}")
    
    # Test 1: Can we start Sentinel successfully?
    print("\n1. Testing Sentinel startup...")
    sentinel_cmd = [
        sys.executable, "-m", "sentinel.telemetry_v2",
        "--namespace=aswarm",
        "--cadence-ms=1000", 
        "--duration=5",
        f"--run-id={run_id}",
        "--trigger-anomaly=2"
    ]
    
    try:
        result = subprocess.run(
            sentinel_cmd, cwd=PROJECT_ROOT, 
            capture_output=True, text=True, timeout=10
        )
        
        print(f"Sentinel return code: {result.returncode}")
        print(f"Sentinel stdout:\n{result.stdout}")
        if result.stderr:
            print(f"Sentinel stderr:\n{result.stderr}")
            
    except Exception as e:
        print(f"Sentinel test failed: {e}")
        return False
    
    # Test 2: Did Sentinel create a Lease?
    print(f"\n2. Checking if Lease was created...")
    time.sleep(2)  # Wait a moment
    
    from kubernetes import client, config
    try:
        config.load_kube_config()
        coordination_v1 = client.CoordinationV1Api()
        
        leases = coordination_v1.list_namespaced_lease("aswarm")
        sentinel_leases = [l for l in leases.items if "sentinel" in l.metadata.name]
        
        if sentinel_leases:
            lease = sentinel_leases[0]
            annotations = lease.metadata.annotations or {}
            labels = lease.metadata.labels or {}
            
            print(f"Found Lease: {lease.metadata.name}")
            print(f"  Labels: {labels}")
            print(f"  Run ID (label): {labels.get('aswarm.ai/run-id', 'MISSING')}")
            print(f"  Run ID (annotation): {annotations.get('aswarm.ai/run-id', 'MISSING')}")
            print(f"  Score: {annotations.get('aswarm.ai/score', 'MISSING')}")
            
            if labels.get('aswarm.ai/run-id') == run_id:
                print("✅ Lease has correct run_id")
                return True
            else:
                print(f"❌ Run ID mismatch: expected {run_id}")
                return False
        else:
            print("❌ No Sentinel leases found")
            return False
            
    except Exception as e:
        print(f"Lease check failed: {e}")
        return False

if __name__ == "__main__":
    success = test_minimal_integration()
    print(f"\n=== Result: {'PASS' if success else 'FAIL'} ===")
    sys.exit(0 if success else 1)