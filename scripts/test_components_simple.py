#!/usr/bin/env python3
"""
Simple test to verify individual components work before integration
"""
import os
import sys
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

def test_sentinel_standalone():
    """Test Sentinel v2 can run standalone"""
    print("=== Testing Sentinel v2 Standalone ===")
    
    cmd = [
        sys.executable, "-m", "sentinel.telemetry_v2",
        "--namespace=aswarm",
        "--cadence-ms=500", 
        "--duration=5",
        "--run-id=test-sentinel",
        "--trigger-anomaly=3"
    ]
    
    try:
        result = subprocess.run(
            cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10
        )
        
        print(f"Return code: {result.returncode}")
        print(f"Stdout: {result.stdout}")
        if result.stderr:
            print(f"Stderr: {result.stderr}")
            
        if result.returncode == 0:
            print("✅ Sentinel standalone OK")
            return True
        else:
            print(f"❌ Sentinel failed with code {result.returncode}")
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ Sentinel timed out")
        return False
    except Exception as e:
        print(f"❌ Sentinel test error: {e}")
        return False

def test_pheromone_standalone():
    """Test Pheromone v2 can run standalone"""
    print("\n=== Testing Pheromone v2 Standalone ===")
    
    cmd = [
        sys.executable, "-m", "pheromone.gossip_v2",
        "--namespace=aswarm",
        "--duration=3",
        "--run-id=test-pheromone",
        "--window-ms=500",
        "--quorum=1"
    ]
    
    try:
        result = subprocess.run(
            cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=8
        )
        
        print(f"Return code: {result.returncode}")
        print(f"Stdout: {result.stdout}")
        if result.stderr:
            print(f"Stderr: {result.stderr}")
            
        if result.returncode == 0:
            print("✅ Pheromone standalone OK")
            return True
        else:
            print(f"❌ Pheromone failed with code {result.returncode}")
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ Pheromone timed out")
        return False
    except Exception as e:
        print(f"❌ Pheromone test error: {e}")
        return False

def check_namespace():
    """Check if aswarm namespace exists"""
    print("\n=== Checking Kubernetes Namespace ===")
    try:
        result = subprocess.run(
            ["kubectl", "get", "namespace", "aswarm"], 
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode == 0:
            print("✅ aswarm namespace exists")
            return True
        else:
            print("⚠️  aswarm namespace not found, creating...")
            create_result = subprocess.run(
                ["kubectl", "create", "namespace", "aswarm"],
                capture_output=True, text=True, timeout=10
            )
            if create_result.returncode == 0:
                print("✅ Created aswarm namespace")
                return True
            else:
                print(f"❌ Failed to create namespace: {create_result.stderr}")
                return False
                
    except Exception as e:
        print(f"❌ Namespace check error: {e}")
        return False

def main():
    print("A-SWARM Component Testing\n")
    
    tests = [
        ("Kubernetes Namespace", check_namespace),
        ("Sentinel Standalone", test_sentinel_standalone), 
        ("Pheromone Standalone", test_pheromone_standalone)
    ]
    
    passed = 0
    for name, test_func in tests:
        if test_func():
            passed += 1
            
    print(f"\n=== Results: {passed}/{len(tests)} tests passed ===")
    
    if passed == len(tests):
        print("🎯 Components ready for integration test")
    else:
        print("⚠️  Fix component issues before integration")
        
    return passed == len(tests)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)