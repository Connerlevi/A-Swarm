#!/usr/bin/env python3
"""
Debug the optimized components to see what's breaking
"""
import sys
import time
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

def test_optimized_sentinel():
    """Test if optimized Sentinel works standalone"""
    print("=== Testing Optimized Sentinel ===")
    
    cmd = [
        sys.executable, "-m", "sentinel.telemetry_v2",
        "--namespace=aswarm",
        "--cadence-ms=50",
        "--duration=3",
        "--run-id=debug-opt-sentinel",
        "--trigger-anomaly=3"
    ]
    
    try:
        result = subprocess.run(
            cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=6
        )
        
        print(f"Return code: {result.returncode}")
        print(f"Stdout:\n{result.stdout}")
        if result.stderr:
            print(f"Stderr:\n{result.stderr}")
            
        return result.returncode == 0
        
    except Exception as e:
        print(f"Sentinel test error: {e}")
        return False

def test_optimized_pheromone():
    """Test if optimized Pheromone works standalone"""
    print("\n=== Testing Optimized Pheromone ===")
    
    cmd = [
        sys.executable, "-m", "pheromone.gossip_v2",
        "--namespace=aswarm",
        "--duration=3",
        "--run-id=debug-opt-pheromone", 
        "--window-ms=80",
        "--quorum=1",
        "--node-score-threshold=0.7",
        "--fast-path-score=0.90"
    ]
    
    try:
        result = subprocess.run(
            cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=6
        )
        
        print(f"Return code: {result.returncode}")
        print(f"Stdout:\n{result.stdout}")
        if result.stderr:
            print(f"Stderr:\n{result.stderr}")
            
        return result.returncode == 0
        
    except Exception as e:
        print(f"Pheromone test error: {e}")
        return False

def main():
    print("=== Optimized Component Debug ===")
    
    sentinel_ok = test_optimized_sentinel()
    pheromone_ok = test_optimized_pheromone()
    
    print(f"\n=== Results ===")
    print(f"Sentinel: {'✅' if sentinel_ok else '❌'}")
    print(f"Pheromone: {'✅' if pheromone_ok else '❌'}")
    
    if not (sentinel_ok and pheromone_ok):
        print("\n❌ Components broken by optimization - need to fix before SLO test")
        return False
    else:
        print("\n✅ Components OK - issue is in integration")
        return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)