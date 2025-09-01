#!/usr/bin/env python3
"""
Check what's happening during a live test run
"""
import time
from kubernetes import client, config

def check_live_state(run_id="lease-test-1756346886429-0"):
    """Check what resources exist for a specific run"""
    try:
        config.load_kube_config()
        v1 = client.CoreV1Api()
        coordination_v1 = client.CoordinationV1Api()
        
        print(f"=== Live State Check for {run_id} ===")
        
        # Check for specific run_id Leases
        print(f"\nLeases with run-id={run_id}:")
        try:
            leases = coordination_v1.list_namespaced_lease("aswarm", label_selector=f"aswarm.ai/run-id={run_id}")
            if leases.items:
                for lease in leases.items:
                    annotations = lease.metadata.annotations or {}
                    labels = lease.metadata.labels or {}
                    print(f"  {lease.metadata.name}:")
                    print(f"    Created: {lease.metadata.creation_timestamp}")
                    print(f"    Labels: {labels}")
                    print(f"    Run ID: {annotations.get('aswarm.ai/run-id', 'MISSING')}")
                    print(f"    Score: {annotations.get('aswarm.ai/score', 'MISSING')}")
                    print(f"    Sequence: {annotations.get('aswarm.ai/seq', 'MISSING')}")
                    print(f"    Elevate: {annotations.get('aswarm.ai/elevate', 'false')}")
                    print(f"    Timestamp: {annotations.get('aswarm.ai/ts', 'MISSING')}")
            else:
                print(f"  No leases found with run-id={run_id}")
        except Exception as e:
            print(f"  Error checking leases: {e}")
        
        # Check for ConfigMaps
        print(f"\nConfigMaps with run-id={run_id}:")
        try:
            cms = v1.list_namespaced_config_map("aswarm", label_selector=f"aswarm.ai/run-id={run_id}")
            if cms.items:
                for cm in cms.items:
                    print(f"  {cm.metadata.name}:")
                    print(f"    Created: {cm.metadata.creation_timestamp}")
                    print(f"    Labels: {cm.metadata.labels}")
                    print(f"    Data keys: {list(cm.data.keys()) if cm.data else []}")
                    if "elevation.json" in (cm.data or {}):
                        import json
                        elevation = json.loads(cm.data["elevation.json"])
                        print(f"    Elevation data: {elevation}")
            else:
                print(f"  No ConfigMaps found with run-id={run_id}")
        except Exception as e:
            print(f"  Error checking ConfigMaps: {e}")
            
        # Check all Leases (might be labeling issue)
        print(f"\nAll Leases in aswarm namespace:")
        try:
            all_leases = coordination_v1.list_namespaced_lease("aswarm")
            for lease in all_leases.items:
                if "sentinel" in lease.metadata.name:
                    annotations = lease.metadata.annotations or {}
                    labels = lease.metadata.labels or {}
                    print(f"  {lease.metadata.name}:")
                    print(f"    Run ID in annotations: {annotations.get('aswarm.ai/run-id', 'NONE')}")
                    print(f"    Run ID in labels: {labels.get('aswarm.ai/run-id', 'NONE')}")
                    print(f"    Component label: {labels.get('app.kubernetes.io/component', 'NONE')}")
        except Exception as e:
            print(f"  Error checking all leases: {e}")
            
    except Exception as e:
        print(f"Check error: {e}")

if __name__ == "__main__":
    # Check the most recent failed run
    check_live_state("lease-test-1756346886429-0")