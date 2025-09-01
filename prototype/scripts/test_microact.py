#!/usr/bin/env python3
"""
Test script for micro-act catalog
"""
import sys
import json
import time
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from actuators.microact_catalog_v2 import MicroActCatalog, DRY_RUN

def test_basic_operations():
    """Test basic catalog operations"""
    print("=== Testing Micro-Act Catalog v0.2 ===\n")
    
    catalog = MicroActCatalog()
    
    # Test 1: List all actions
    print("1. Listing all actions:")
    actions = catalog.list_actions()
    for action in actions[:3]:  # Show first 3
        print(f"   - {action.id} (Ring {action.ring.value}): {action.name}")
    print(f"   ... and {len(actions)-3} more\n")
    
    # Test 2: Execute network isolation
    print("2. Testing network isolation (NetworkPolicy):")
    result = catalog.execute("networkpolicy_isolate", {
        "namespace": "default",
        "selector": "app=test",
        "ttl_seconds": 30
    })
    print(f"   Success: {result.success}")
    print(f"   Message: {result.message}")
    if result.proof:
        print(f"   Proof: {result.proof['params_hash'][:8]}...")
    print()
    
    # Test 3: Execute with missing params
    print("3. Testing validation (missing params):")
    result = catalog.execute("networkpolicy_isolate", {
        "namespace": "default"
        # Missing selector
    })
    print(f"   Success: {result.success}")
    print(f"   Message: {result.message}\n")
    
    # Test 4: Ring limit enforcement
    print("4. Testing ring limits:")
    result = catalog.execute("container_pause", {
        "namespace": "default",
        "pod": "test-pod",
        "container": "main"
    })
    print(f"   Ring 3 action success: {result.success}")
    print(f"   Message: {result.message}\n")
    
    # Test 5: TTL monitoring
    print("5. Testing TTL auto-revert (30s TTL):")
    if result.success and result.revert_handle:
        print(f"   Scheduled revert: {result.revert_handle}")
        print(f"   Expires at: {result.expires_at}")
        print("   (Monitor will revert automatically)")
    
    return True

def test_idempotency():
    """Test idempotent operations"""
    print("\n6. Testing idempotency:")
    catalog = MicroActCatalog()
    
    # Apply same isolation twice
    params = {
        "namespace": "test",
        "selector": "app=idempotent",
        "ttl_seconds": 10
    }
    
    result1 = catalog.execute("networkpolicy_isolate", params)
    print(f"   First apply: {result1.success}")
    
    result2 = catalog.execute("networkpolicy_isolate", params)
    print(f"   Second apply: {result2.success}")
    print("   (Both should succeed)\n")

def test_concurrent_ttl():
    """Test concurrent TTL tracking"""
    print("7. Testing concurrent TTL actions:")
    catalog = MicroActCatalog()
    
    # Schedule multiple actions with different TTLs
    for i in range(3):
        result = catalog.execute("networkpolicy_isolate", {
            "namespace": f"test-{i}",
            "selector": f"app=test-{i}",
            "ttl_seconds": 5 + i*2  # 5s, 7s, 9s
        })
        if result.success:
            print(f"   Scheduled action {i+1} with {5+i*2}s TTL")
    
    print("   (Actions will revert at different times)")

def main():
    print(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}\n")
    
    # Run basic tests
    if not test_basic_operations():
        return 1
    
    # Additional tests
    test_idempotency()
    test_concurrent_ttl()
    
    print("\n=== All tests completed ===")
    print("Note: In DRY_RUN mode, no actual changes were made.")
    print("Set ASWARM_DRY_RUN=false to test real execution.")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())