#!/usr/bin/env python3
"""
Quick validation script to test individual components before full integration
"""
import os
import sys
import json
import time
import subprocess
from pathlib import Path
from kubernetes import client, config

PROJECT_ROOT = Path(__file__).resolve().parents[2] 
sys.path.insert(0, str(PROJECT_ROOT))

def test_sentinel_imports():
    """Test that Sentinel v2 can be imported and has correct interface"""
    try:
        from sentinel.telemetry_v2 import LeaseBasedTelemetry
        
        # Test instantiation
        sentinel = LeaseBasedTelemetry(cadence_ms=200)
        
        # Check key methods exist
        assert hasattr(sentinel, 'update_lease'), "Missing update_lease method"
        assert hasattr(sentinel, 'score_signal'), "Missing score_signal method" 
        assert hasattr(sentinel, 'run_telemetry_loop'), "Missing run_telemetry_loop method"
        
        print("✅ Sentinel v2 imports and interface OK")
        return True
        
    except Exception as e:
        print(f"❌ Sentinel v2 import failed: {e}")
        return False

def test_pheromone_imports():
    """Test that Pheromone v2 can be imported and has correct interface"""
    try:
        from pheromone.gossip_v2 import LeaseWatcher
        from pheromone.signal_types import LeaseSignal, QuorumMetrics, ElevationEvent
        
        # Test instantiation
        watcher = LeaseWatcher(window_ms=500, quorum_threshold=2)
        
        # Check key methods exist
        assert hasattr(watcher, 'parse_lease_signal'), "Missing parse_lease_signal method"
        assert hasattr(watcher, 'compute_sliding_window_quorum'), "Missing compute_sliding_window_quorum method"
        assert hasattr(watcher, 'should_elevate'), "Missing should_elevate method"
        assert hasattr(watcher, 'create_elevation_artifact'), "Missing create_elevation_artifact method"
        
        print("✅ Pheromone v2 imports and interface OK")
        return True
        
    except Exception as e:
        print(f"❌ Pheromone v2 import failed: {e}")
        return False

def test_kubernetes_connectivity():
    """Test Kubernetes API connectivity and namespace"""
    try:
        config.load_kube_config()
        v1 = client.CoreV1Api()
        
        # Test namespace access
        try:
            ns = v1.read_namespace("aswarm")
            print("✅ Kubernetes connectivity and aswarm namespace OK")
            return True
        except:
            print("⚠️  aswarm namespace not found, will need: kubectl create namespace aswarm")
            return True  # Still OK, just needs setup
            
    except Exception as e:
        print(f"❌ Kubernetes connectivity failed: {e}")
        return False

def test_cli_interfaces():
    """Test that CLI entry points work"""
    try:
        # Test Sentinel CLI
        result = subprocess.run([
            sys.executable, "-m", "sentinel.telemetry_v2", "--help"
        ], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            print("✅ Sentinel CLI interface OK")
        else:
            print(f"❌ Sentinel CLI failed: {result.stderr}")
            return False
        
        # Test Pheromone CLI
        result = subprocess.run([
            sys.executable, "-m", "pheromone.gossip_v2", "--help"
        ], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            print("✅ Pheromone CLI interface OK")
        else:
            print(f"❌ Pheromone CLI failed: {result.stderr}")
            return False
            
        return True
        
    except Exception as e:
        print(f"❌ CLI interface test failed: {e}")
        return False

def test_schema_alignment():
    """Test that Sentinel and Pheromone use compatible schemas"""
    try:
        from pheromone.signal_types import LeaseSignal
        
        # Test parsing a minimal signal
        test_lease = type('MockLease', (), {
            'metadata': type('MockMeta', (), {
                'name': 'aswarm-sentinel-test-node',
                'annotations': {
                    'aswarm.ai/ts': '2025-08-28T10:00:00Z',
                    'aswarm.ai/seq': '42',
                    'aswarm.ai/score': '0.750',
                    'aswarm.ai/elevate': 'true',
                    'aswarm.ai/elevate-ts': '2025-08-28T10:00:00Z',
                    'aswarm.ai/run-id': 'test-123'
                }
            })(),
            'spec': type('MockSpec', (), {'renew_time': None})()
        })()
        
        from pheromone.gossip_v2 import LeaseWatcher
        watcher = LeaseWatcher()
        signal = watcher.parse_lease_signal(test_lease)
        
        assert signal is not None, "Failed to parse lease signal"
        assert signal.node == "test-node", f"Wrong node: {signal.node}"
        assert signal.seq == 42, f"Wrong sequence: {signal.seq}"
        assert signal.score == 0.750, f"Wrong score: {signal.score}"
        assert signal.elevate == True, f"Wrong elevate: {signal.elevate}"
        assert signal.run_id == "test-123", f"Wrong run_id: {signal.run_id}"
        
        print("✅ Schema alignment OK")
        return True
        
    except Exception as e:
        print(f"❌ Schema alignment test failed: {e}")
        return False

def main():
    print("=== A-SWARM Lease Detection Component Validation ===\n")
    
    all_tests = [
        ("Sentinel v2 Import", test_sentinel_imports),
        ("Pheromone v2 Import", test_pheromone_imports), 
        ("Kubernetes Connectivity", test_kubernetes_connectivity),
        ("CLI Interfaces", test_cli_interfaces),
        ("Schema Alignment", test_schema_alignment)
    ]
    
    passed = 0
    total = len(all_tests)
    
    for name, test_func in all_tests:
        print(f"Testing {name}...")
        if test_func():
            passed += 1
        print()
    
    print(f"=== Validation Summary: {passed}/{total} tests passed ===")
    
    if passed == total:
        print("🎯 All components ready for integration testing")
        print("Next step: python scripts/test_lease_detection_v2.py --repeats=3")
    else:
        print("⚠️  Some components need fixes before integration testing")
        
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)