#!/usr/bin/env python3
"""
Test UDP Fast-Path End-to-End Latency
Target: <200ms MTTD via direct UDP bypass
"""
import argparse
import json
import logging
import os
import socket
import statistics
import struct
import sys
import threading
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional

# Add parent dirs to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sentinel'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pheromone'))

from sentinel.fast_path import FastPathSender, MAGIC as SENDER_MAGIC, VERSION as SENDER_VERSION, TYPE_ELEVATION as SENDER_TYPE
from pheromone.udp_listener import FastPathListener, MAGIC as LISTENER_MAGIC, VERSION as LISTENER_VERSION, TYPE_ELEVATION as LISTENER_TYPE, HEADER_SIZE

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger('test_fast_path')

def pct(sorted_vals: List[float], q: float) -> float:
    """Calculate percentile safely"""
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * q))
    return sorted_vals[idx]

class LatencyCollector:
    """Collects end-to-end latency measurements"""
    
    def __init__(self, ack_sender: Optional['AckSender'] = None):
        self.latencies = []
        self.lock = threading.Lock()
        self.start_times = {}
        self.received_count = 0
        self.ack_sender = ack_sender
    
    def mark_send(self, seq: int):
        """Mark when a packet was sent"""
        with self.lock:
            self.start_times[seq] = time.perf_counter()
    
    def mark_receive(self, elevation_data: Dict[str, Any], source: Tuple[str, int]):
        """Mark when elevation was received and calculate latency"""
        try:
            # Extract sequence from payload
            seq = elevation_data.get('sequence32')  # Try sequence32 first
            if seq is None:
                seq = elevation_data.get('sequence')  # Fallback to sequence
            if seq is None:
                return
            
            with self.lock:
                if seq in self.start_times:
                    latency_ms = (time.perf_counter() - self.start_times[seq]) * 1000
                    self.latencies.append(latency_ms)
                    del self.start_times[seq]
                    self.received_count += 1
                    
                    if latency_ms < 10:
                        logger.debug(f"Fast elevation: seq={seq}, latency={latency_ms:.2f}ms")
            
            # Send ACK if configured
            if self.ack_sender:
                self.ack_sender.send_ack(seq, elevation_data, source)
                    
        except Exception as e:
            logger.error(f"Error in mark_receive: {e}")
    
    def get_stats(self) -> Dict[str, float]:
        """Calculate latency statistics"""
        with self.lock:
            if not self.latencies:
                return {
                    'count': 0, 'min': 0, 'p50': 0, 'p90': 0, 
                    'p95': 0, 'p99': 0, 'max': 0, 'mean': 0, 'stdev': 0
                }
            
            sorted_lat = sorted(self.latencies)
            n = len(sorted_lat)
            
            return {
                'count': n,
                'min': sorted_lat[0],
                'p50': pct(sorted_lat, 0.50),
                'p90': pct(sorted_lat, 0.90),
                'p95': pct(sorted_lat, 0.95),
                'p99': pct(sorted_lat, 0.99),
                'max': sorted_lat[-1],
                'mean': statistics.mean(sorted_lat),
                'stdev': statistics.stdev(sorted_lat) if n > 1 else 0
            }

class AckSender:
    """Sends ACK packets back to sender for remote latency measurement"""
    
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    def send_ack(self, seq: int, elevation_data: Dict[str, Any], source: Tuple[str, int]):
        """Send ACK packet back to sender"""
        try:
            ack = {
                'sequence': seq,
                'listener_recv_wall_ts': time.time(),
                'listener_queue_delay_ms': 0  # Could calculate from _fastpath metadata
            }
            ack_data = json.dumps(ack).encode('utf-8')
            self.sock.sendto(ack_data, source)
        except Exception as e:
            logger.debug(f"Failed to send ACK: {e}")
    
    def close(self):
        self.sock.close()

class RemoteLatencyCollector:
    """Collects one-way latency for remote tests"""
    
    def __init__(self):
        self.latencies = []
        self.lock = threading.Lock()
        self.ack_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ack_sock.bind(('', 0))  # Bind to any available port
        self.ack_sock.settimeout(0.2)  # 200ms timeout
        self.ack_port = self.ack_sock.getsockname()[1]
    
    def send_and_measure(self, sender: FastPathSender, anomaly: Dict[str, Any]) -> Optional[float]:
        """Send packet and measure one-way latency via ACK"""
        send_wall_ts = time.time()
        seq = sender.sequence
        
        # Send elevation
        stats = sender.send_elevation(anomaly)
        
        # Wait for ACK
        try:
            ack_data, _ = self.ack_sock.recvfrom(1024)
            ack = json.loads(ack_data.decode('utf-8'))
            
            if ack.get('sequence') == seq:
                one_way_ms = max(0, (ack['listener_recv_wall_ts'] - send_wall_ts) * 1000)
                with self.lock:
                    self.latencies.append(one_way_ms)
                return one_way_ms
        except (socket.timeout, json.JSONDecodeError):
            pass
        
        return None
    
    def get_stats(self) -> Dict[str, float]:
        """Calculate latency statistics"""
        with self.lock:
            if not self.latencies:
                return {
                    'count': 0, 'min': 0, 'p50': 0, 'p90': 0,
                    'p95': 0, 'p99': 0, 'max': 0, 'mean': 0, 'stdev': 0
                }
            
            sorted_lat = sorted(self.latencies)
            n = len(sorted_lat)
            
            return {
                'count': n,
                'min': sorted_lat[0],
                'p50': pct(sorted_lat, 0.50),
                'p90': pct(sorted_lat, 0.90),
                'p95': pct(sorted_lat, 0.95),
                'p99': pct(sorted_lat, 0.99),
                'max': sorted_lat[-1],
                'mean': statistics.mean(sorted_lat),
                'stdev': statistics.stdev(sorted_lat) if n > 1 else 0
            }
    
    def close(self):
        self.ack_sock.close()

def test_loopback_latency(num_packets: int = 100, 
                         rate_pps: int = 100,
                         port: int = 8888,
                         test_key: str = "test-key") -> Dict[str, Any]:
    """Test UDP fast-path latency on loopback"""
    
    # Create ACK sender for loopback ACK testing
    ack_sender = AckSender()
    collector = LatencyCollector(ack_sender=ack_sender)
    
    # Start listener with proper key configuration
    logger.info("Starting UDP listener...")
    listener = FastPathListener(
        bind_addr='127.0.0.1',
        bind_port=port,
        shared_keys={1: test_key},  # Use dict format
        elevation_callback=collector.mark_receive,
        num_workers=2,
        queue_size=10000
    )
    listener.start()
    time.sleep(0.5)  # Let it initialize
    
    # Create sender
    logger.info("Creating UDP sender...")
    sender = FastPathSender(
        host='127.0.0.1',
        port=port,
        shared_key=test_key,
        key_id=1,
        dupes=1,  # Single send for accurate timing
        gap_ms=0
    )
    
    # Warm-up packets (not counted)
    logger.info("Sending 5 warm-up packets...")
    for i in range(5):
        anomaly = {'score': 0.5, 'witness_count': 1, 'selector': 'warmup'}
        sender.send_elevation(anomaly)
        time.sleep(0.01)
    
    # Reset collector after warmup
    collector.latencies.clear()
    collector.received_count = 0
    
    # Send test packets
    logger.info(f"Sending {num_packets} test packets at {rate_pps} pps...")
    
    interval = max(0.0, 1.0 / rate_pps)
    start_time = time.perf_counter()
    sent_count = 0
    
    for i in range(num_packets):
        # Create test anomaly
        anomaly = {
            'score': 0.95,
            'witness_count': 4,
            'selector': f'app=test-{i%10}',
            'event_type': 'test_spike'
        }
        
        # Mark send time
        seq = sender.sequence
        collector.mark_send(seq)
        
        # Send packet
        stats = sender.send_elevation(anomaly)
        sent_count += 1
        
        # Rate limit
        if i < num_packets - 1:
            next_send = start_time + (i + 1) * interval
            sleep_time = next_send - time.perf_counter()
            if sleep_time > 0:
                time.sleep(sleep_time)
    
    # Wait for packets to be processed
    logger.info("Waiting for packets to be processed...")
    max_wait = 5.0
    wait_start = time.perf_counter()
    
    while collector.received_count < num_packets and time.perf_counter() - wait_start < max_wait:
        time.sleep(0.1)
    
    # Calculate drops
    drops = sent_count - collector.received_count
    
    # Get results
    results = collector.get_stats()
    results['sent'] = sent_count
    results['received'] = collector.received_count
    results['drops'] = drops
    
    # Cleanup
    sender.close()
    listener.stop()
    ack_sender.close()
    
    if drops > 0:
        logger.warning(f"Dropped {drops} packets ({drops/sent_count*100:.1f}%)")
    
    return results

def test_remote_latency(target_host: str,
                       num_packets: int = 100,
                       rate_pps: int = 50,
                       port: int = 8888) -> Dict[str, Any]:
    """Test latency to remote Pheromone service with ACK measurement"""
    
    # Note: This assumes the remote listener sends ACKs back
    # In production, you'd need to ensure the remote listener has ACK support
    
    logger.info(f"Testing fast-path to {target_host}:{port}")
    
    # Create remote collector
    collector = RemoteLatencyCollector()
    
    # Create sender
    sender = FastPathSender(
        host=target_host,
        port=port,
        dupes=1,  # Single send for accurate timing
        gap_ms=0
    )
    
    # Warm-up
    logger.info("Sending 5 warm-up packets...")
    for i in range(5):
        anomaly = {'score': 0.5, 'witness_count': 1, 'selector': 'warmup'}
        sender.send_elevation(anomaly)
        time.sleep(0.01)
    
    # Send test burst
    logger.info(f"Sending {num_packets} test packets at {rate_pps} pps...")
    sent_count = 0
    received_acks = 0
    interval = max(0.0, 1.0 / rate_pps)
    start_time = time.perf_counter()
    
    for i in range(num_packets):
        anomaly = {
            'score': 0.90 + (i % 10) / 100,
            'witness_count': 3 + (i % 3),
            'selector': 'app=fastpath-test',
            'event_type': 'latency_test'
        }
        
        one_way = collector.send_and_measure(sender, anomaly)
        sent_count += 1
        if one_way is not None:
            received_acks += 1
        
        # Rate limit
        if i < num_packets - 1:
            next_send = start_time + (i + 1) * interval
            sleep_time = next_send - time.perf_counter()
            if sleep_time > 0:
                time.sleep(sleep_time)
    
    sender.close()
    collector.close()
    
    # Get results
    results = collector.get_stats()
    results['sent'] = sent_count
    results['received'] = received_acks
    results['drops'] = sent_count - received_acks
    
    if results['drops'] > 0:
        logger.warning(f"No ACK for {results['drops']} packets ({results['drops']/sent_count*100:.1f}%)")
    
    return results

def test_burst_mode(target_host: str = '127.0.0.1',
                   burst_size: int = 1000,
                   port: int = 8888) -> Dict[str, Any]:
    """Test burst mode to stress queue/backpressure"""
    
    logger.info(f"Testing burst mode: {burst_size} packets to {target_host}:{port}")
    
    # Create sender
    sender = FastPathSender(
        host=target_host,
        port=port,
        dupes=1,
        gap_ms=0
    )
    
    # Send burst as fast as possible
    start_time = time.perf_counter()
    sent_count = 0
    
    for i in range(burst_size):
        anomaly = {
            'score': 0.99,
            'witness_count': 10,
            'selector': 'app=burst-test',
            'event_type': 'burst_flood'
        }
        
        try:
            stats = sender.send_elevation(anomaly)
            sent_count += 1
        except Exception as e:
            logger.debug(f"Send error in burst: {e}")
    
    elapsed = time.perf_counter() - start_time
    pps = sent_count / elapsed if elapsed > 0 else 0
    
    sender.close()
    
    return {
        'mode': 'burst',
        'sent': sent_count,
        'duration_sec': elapsed,
        'pps': pps,
        'failed': burst_size - sent_count
    }

def verify_packet_format():
    """Verify packet format compatibility"""
    logger.info("Verifying packet format...")
    
    # Test header size
    from sentinel.fast_path import HEADER_SIZE as SENDER_HEADER_SIZE
    from pheromone.udp_listener import HEADER_SIZE as LISTENER_HEADER_SIZE
    
    assert SENDER_HEADER_SIZE == LISTENER_HEADER_SIZE, \
        f"Header size mismatch: sender={SENDER_HEADER_SIZE}, listener={LISTENER_HEADER_SIZE}"
    
    logger.info(f"✓ Header size: {SENDER_HEADER_SIZE} bytes")
    
    # Test version compatibility
    assert SENDER_VERSION == LISTENER_VERSION, \
        f"Version mismatch: sender={SENDER_VERSION}, listener={LISTENER_VERSION}"
    
    logger.info(f"✓ Protocol version: {SENDER_VERSION}")
    
    # Test magic bytes
    assert SENDER_MAGIC == LISTENER_MAGIC, \
        f"Magic mismatch: sender={SENDER_MAGIC}, listener={LISTENER_MAGIC}"
    
    logger.info(f"✓ Magic bytes: {SENDER_MAGIC}")
    
    # Test type constants
    assert SENDER_TYPE == LISTENER_TYPE, \
        f"Type mismatch: sender={SENDER_TYPE}, listener={LISTENER_TYPE}"
    
    logger.info(f"✓ Elevation type: {SENDER_TYPE}")
    
    return True

def main():
    parser = argparse.ArgumentParser(description='Test UDP Fast-Path Performance')
    parser.add_argument('--mode', choices=['loopback', 'remote', 'burst', 'verify'], 
                       default='loopback', help='Test mode')
    parser.add_argument('--host', help='Remote host for remote/burst mode')
    parser.add_argument('--port', type=int, default=8888, help='UDP port')
    parser.add_argument('--packets', type=int, default=100, help='Number of test packets')
    parser.add_argument('--rate', type=int, default=100, help='Packets per second')
    parser.add_argument('--key', help='Override ASWARM_FASTPATH_KEY')
    parser.add_argument('--json', action='store_true', help='Output only JSON result')
    parser.add_argument('--burst-size', type=int, default=1000, help='Burst mode packet count')
    
    args = parser.parse_args()
    
    # Configure logging for JSON mode
    if args.json:
        logging.getLogger().setLevel(logging.ERROR)
    
    # Set key if provided
    if args.key:
        os.environ['ASWARM_FASTPATH_KEY'] = args.key
    
    # Check key for remote mode
    if args.mode in ['remote', 'burst'] and not args.key and not os.environ.get('ASWARM_FASTPATH_KEY'):
        logger.error("ASWARM_FASTPATH_KEY not set. Use --key or set environment variable.")
        return 1
    
    if args.mode == 'verify':
        verify_packet_format()
        return 0
    
    elif args.mode == 'loopback':
        if not args.json:
            logger.info("=== UDP Fast-Path Loopback Test ===")
        
        test_key = args.key or os.environ.get('ASWARM_FASTPATH_KEY', 'test-loopback-key')
        results = test_loopback_latency(
            num_packets=args.packets,
            rate_pps=args.rate,
            port=args.port,
            test_key=test_key
        )
        results['mode'] = 'loopback'
        
    elif args.mode == 'remote':
        if not args.host:
            logger.error("--host required for remote mode")
            return 1
        
        if not args.json:
            logger.info("=== UDP Fast-Path Remote Test ===")
        
        results = test_remote_latency(
            target_host=args.host,
            num_packets=args.packets,
            rate_pps=args.rate,
            port=args.port
        )
        results['mode'] = 'remote'
        
    elif args.mode == 'burst':
        if not args.host:
            args.host = '127.0.0.1'
        
        if not args.json:
            logger.info("=== UDP Fast-Path Burst Test ===")
        
        results = test_burst_mode(
            target_host=args.host,
            burst_size=args.burst_size,
            port=args.port
        )
    
    # Output results
    if args.json:
        # Machine-readable JSON output
        print(json.dumps(results))
    else:
        # Human-readable output
        if 'error' in results:
            logger.error(f"Test failed: {results['error']}")
            return 1
        
        logger.info("\n=== Results ===")
        
        if results.get('mode') == 'burst':
            logger.info(f"Burst size: {results['sent']} packets")
            logger.info(f"Duration: {results['duration_sec']:.2f} sec")
            logger.info(f"Rate: {results['pps']:.0f} pps")
            logger.info(f"Failed: {results['failed']}")
        else:
            logger.info(f"Packets: {results.get('sent', 0)} sent, {results.get('received', 0)} received")
            
            if results.get('drops', 0) > 0:
                logger.warning(f"Drops: {results['drops']}")
            
            if results.get('count', 0) > 0:
                logger.info(f"Latency (ms):")
                logger.info(f"  Min:  {results['min']:.2f}")
                logger.info(f"  P50:  {results['p50']:.2f}")
                logger.info(f"  P90:  {results['p90']:.2f}")
                logger.info(f"  P95:  {results['p95']:.2f}")
                logger.info(f"  P99:  {results['p99']:.2f}")
                logger.info(f"  Max:  {results['max']:.2f}")
                logger.info(f"  Mean: {results['mean']:.2f}")
                logger.info(f"  Stdev: {results['stdev']:.2f}")
    
    # Check against target for non-burst modes
    if results.get('mode') in ['loopback', 'remote'] and results.get('count', 0) > 0:
        p95 = results.get('p95', 0)
        if p95 < 200:
            if not args.json:
                logger.info(f"\n✅ P95 latency {p95:.2f}ms < 200ms target")
            return 0
        else:
            if not args.json:
                logger.warning(f"\n⚠️  P95 latency {p95:.2f}ms exceeds 200ms target")
            return 1
    
    return 0

if __name__ == '__main__':
    sys.exit(main())