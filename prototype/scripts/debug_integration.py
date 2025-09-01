#!/usr/bin/env python3
"""
Debug script to see what's happening in the integration
"""
import sys
import time
import subprocess
import threading
from pathlib import Path
from kubernetes import client, config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

def debug_kubernetes_state(namespace="aswarm", run_id="debug-test"):
    """Check what resources exist in the cluster"""
    try:
        config.load_kube_config()
        v1 = client.CoreV1Api()
        coordination_v1 = client.CoordinationV1Api()
        
        print("=== Kubernetes State Debug ===")
        
        # Check Leases
        print("\nLeases in aswarm namespace:")
        try:
            leases = coordination_v1.list_namespaced_lease(namespace)
            if leases.items:
                for lease in leases.items:
                    annotations = lease.metadata.annotations or {}
                    print(f"  {lease.metadata.name}:")
                    print(f"    Labels: {lease.metadata.labels}")
                    print(f"    Holder: {lease.spec.holder_identity}")
                    print(f"    Annotations: {list(annotations.keys())}")
                    if "aswarm.ai/score" in annotations:
                        print(f"    Score: {annotations['aswarm.ai/score']}")
                    if "aswarm.ai/run-id" in annotations:
                        print(f"    Run ID: {annotations['aswarm.ai/run-id']}")
            else:
                print("  No leases found")
        except Exception as e:
            print(f"  Error listing leases: {e}")
        
        # Check ConfigMaps
        print("\nConfigMaps in aswarm namespace:")
        try:
            cms = v1.list_namespaced_config_map(namespace)
            relevant_cms = [cm for cm in cms.items if 'aswarm' in cm.metadata.name]
            if relevant_cms:
                for cm in relevant_cms:
                    print(f"  {cm.metadata.name}:")
                    print(f"    Labels: {cm.metadata.labels}")
                    print(f"    Data keys: {list(cm.data.keys()) if cm.data else []}")
            else:
                print("  No aswarm ConfigMaps found")
        except Exception as e:
            print(f"  Error listing ConfigMaps: {e}")
            
    except Exception as e:
        print(f"Debug error: {e}")

def run_debug_test():
    """Run a very simple integration test with detailed output"""
    run_id = f"debug-{int(time.time())}"
    print(f"\n=== Debug Integration Test: {run_id} ===")
    
    # Start one Sentinel
    print("Starting Sentinel...")
    sentinel_cmd = [
        sys.executable, "-m", "sentinel.telemetry_v2",
        "--namespace=aswarm",
        "--cadence-ms=300",  # Slower for easier debugging
        "--duration=10",
        f"--run-id={run_id}",
        "--trigger-anomaly=5"
    ]
    
    sentinel_proc = subprocess.Popen(
        sentinel_cmd, cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    
    # Start Pheromone
    print("Starting Pheromone...")
    pheromone_cmd = [
        sys.executable, "-m", "pheromone.gossip_v2",
        "--namespace=aswarm",
        "--duration=12",
        f"--run-id={run_id}",
        "--window-ms=1000",  # Longer window 
        "--quorum=1"  # Only need 1 witness
    ]
    
    pheromone_proc = subprocess.Popen(
        pheromone_cmd, cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    
    # Let them run
    print("Letting processes run for 8 seconds...")
    time.sleep(8)
    
    # Check state mid-run
    debug_kubernetes_state(run_id=run_id)
    
    # Let them finish
    time.sleep(4)
    
    # Clean up
    print("\nStopping processes...")
    sentinel_proc.terminate()
    pheromone_proc.terminate()
    
    # Collect output
    sentinel_stdout, sentinel_stderr = sentinel_proc.communicate(timeout=3)
    pheromone_stdout, pheromone_stderr = pheromone_proc.communicate(timeout=3)
    
    print(f"\n=== Sentinel Output ===")
    print(f"Stdout:\n{sentinel_stdout}")
    if sentinel_stderr:
        print(f"Stderr:\n{sentinel_stderr}")
    
    print(f"\n=== Pheromone Output ===")
    print(f"Stdout:\n{pheromone_stdout}")
    if pheromone_stderr:
        print(f"Stderr:\n{pheromone_stderr}")
    
    # Final state check
    print(f"\n=== Final State Check ===")
    debug_kubernetes_state(run_id=run_id)

if __name__ == "__main__":
    run_debug_test()