#!/usr/bin/env python3
"""
Test UDP Fast-Path Locally (no Kubernetes required)
"""
import os
import sys
import time
import statistics

# Fix Python version compatibility
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_basic_functionality():
    """Test basic UDP send/receive without full infrastructure"""
    print("=== UDP Fast-Path Basic Test ===")
    
    # Test imports
    try:
        from sentinel.fast_path import FastPathSender, HEADER_SIZE, VERSION
        print(f"✓ Sender imported: version={VERSION}, header_size={HEADER_SIZE}")
    except ImportError as e:
        print(f"✗ Failed to import sender: {e}")
        return False
    
    try:
        from pheromone.udp_listener import FastPathListener, HEADER_SIZE as L_HEADER_SIZE
        print(f"✓ Listener imported: header_size={L_HEADER_SIZE}")
    except ImportError as e:
        print(f"✗ Failed to import listener: {e}")
        return False
    
    # Verify compatibility
    if HEADER_SIZE != L_HEADER_SIZE:
        print(f"✗ Header size mismatch: sender={HEADER_SIZE}, listener={L_HEADER_SIZE}")
        return False
    
    print("✓ Header sizes match")
    
    # Test key setup
    test_key = "test-local-key-12345"
    os.environ['ASWARM_FASTPATH_KEY'] = test_key
    
    # Test sender creation
    try:
        sender = FastPathSender(
            host='127.0.0.1',
            port=9999,  # Use different port to avoid conflicts
            shared_key=test_key,
            dupes=1
        )
        print("✓ Sender created successfully")
    except Exception as e:
        print(f"✗ Failed to create sender: {e}")
        return False
    
    # Test packet send (won't actually deliver without listener)
    try:
        anomaly = {
            'score': 0.95,
            'witness_count': 3,
            'selector': 'app=test'
        }
        stats = sender.send_elevation(anomaly)
        print(f"✓ Test packet sent: {stats.bytes} bytes, {stats.send_ms_first:.2f}ms")
    except Exception as e:
        print(f"✗ Failed to send packet: {e}")
        return False
    
    sender.close()
    print("\n✓ Basic functionality test passed!")
    return True

def simple_loopback_test():
    """Simple loopback test with basic timing"""
    print("\n=== Simple Loopback Test ===")
    
    from sentinel.fast_path import FastPathSender
    from pheromone.udp_listener import FastPathListener
    
    test_key = "test-simple-key"
    results = []
    received = []
    
    def capture_elevation(data, addr):
        received.append((time.perf_counter(), data))
    
    # Start listener
    listener = FastPathListener(
        bind_addr='127.0.0.1',
        bind_port=8899,
        shared_keys={1: test_key},
        elevation_callback=capture_elevation,
        num_workers=1
    )
    listener.start()
    time.sleep(0.5)  # Let it start
    
    # Create sender
    sender = FastPathSender(
        host='127.0.0.1',
        port=8899,
        shared_key=test_key,
        key_id=1,
        dupes=1
    )
    
    # Send test packets
    print("Sending 10 test packets...")
    for i in range(10):
        start = time.perf_counter()
        
        anomaly = {
            'score': 0.90 + i/100,
            'witness_count': i % 5 + 1,
            'selector': f'test={i}'
        }
        
        sender.send_elevation(anomaly)
        results.append(start)
        time.sleep(0.01)  # Small delay between packets
    
    # Wait for processing
    time.sleep(0.5)
    
    # Calculate latencies
    latencies = []
    for send_time in results[:len(received)]:
        recv_time, _ = received[results.index(send_time)] if results.index(send_time) < len(received) else (None, None)
        if recv_time:
            latency_ms = (recv_time - send_time) * 1000
            latencies.append(latency_ms)
    
    # Cleanup
    sender.close()
    listener.stop()
    
    # Results
    print(f"\nResults:")
    print(f"  Sent: {len(results)}")
    print(f"  Received: {len(received)}")
    
    if latencies:
        print(f"  Latency (ms):")
        print(f"    Min: {min(latencies):.2f}")
        print(f"    Avg: {statistics.mean(latencies):.2f}")
        print(f"    Max: {max(latencies):.2f}")
        
        if statistics.mean(latencies) < 50:
            print("\n✓ Loopback test passed! Average < 50ms")
            return True
    
    print("\n✗ Loopback test failed")
    return False

def main():
    print("UDP Fast-Path Local Test")
    print("=" * 40)
    
    # Run tests
    if not test_basic_functionality():
        return 1
    
    if not simple_loopback_test():
        return 1
    
    print("\n✅ All tests passed!")
    print("\nNext steps:")
    print("1. Fix the Kubernetes deployment to include the Python code")
    print("2. Or use a container image with the code built-in")
    print("3. Then run the full integration test")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())