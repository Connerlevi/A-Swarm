#!/usr/bin/env python3
"""
A-SWARM Fast-Path UDP Listener v0.3
Production-ready receiver for <200ms MTTD
"""
import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import queue
import socket
import struct
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, Set, Deque

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s'
)
logger = logging.getLogger('pheromone.fastpath')

# Protocol constants (must match sender)
MAGIC = b'ASWM'
VERSION = 2
TYPE_ELEVATION = 1
HEADER_FMT = '!4sBBQHHB'  # magic(4), version(1), type(1), ts_ns(8), seq16(2), payload_len(2), key_id(1)
HEADER_SIZE = struct.calcsize(HEADER_FMT)
HMAC_SIZE = 32
MAX_PAYLOAD = 1200 - HEADER_SIZE - HMAC_SIZE
MAX_PACKET_SIZE = 1200

# Default configuration
DEFAULT_QUEUE_SIZE = 10000
DEFAULT_WORKERS = min(4, os.cpu_count() or 4)
DEFAULT_STALE_WINDOW_SEC = 60
REPLAY_CACHE_SIZE = 10000
STATS_WINDOW_SIZE = 1024

class Stats:
    """Thread-safe statistics tracking"""
    def __init__(self):
        self.lock = threading.Lock()
        self.counters = {
            'received': 0,
            'valid': 0,
            'invalid_magic': 0,
            'invalid_version': 0,
            'invalid_type': 0,
            'invalid_key': 0,
            'invalid_hmac': 0,
            'invalid_json': 0,
            'replays': 0,
            'stale': 0,
            'dropped_queue_full': 0
        }
        # Latency tracking
        self.latencies: Deque[float] = deque(maxlen=STATS_WINDOW_SIZE)
    
    def increment(self, counter: str, count: int = 1):
        with self.lock:
            self.counters[counter] += count
    
    def record_latency(self, ms: float):
        with self.lock:
            self.latencies.append(ms)
    
    def get_snapshot(self) -> Dict[str, Any]:
        with self.lock:
            snapshot = self.counters.copy()
            if self.latencies:
                sorted_latencies = sorted(self.latencies)
                n = len(sorted_latencies)
                snapshot['p50_ms'] = round(sorted_latencies[n//2], 2)
                snapshot['p95_ms'] = round(sorted_latencies[int(n*0.95)], 2)
            else:
                snapshot['p50_ms'] = 0
                snapshot['p95_ms'] = 0
        return snapshot

class FastPathListener:
    """High-performance UDP listener for elevation signals"""
    
    def __init__(self, bind_addr: str = '0.0.0.0', bind_port: int = 8888,
                 shared_keys: Optional[Dict[int, str]] = None,
                 elevation_callback: Optional[callable] = None,
                 queue_size: int = DEFAULT_QUEUE_SIZE,
                 num_workers: int = DEFAULT_WORKERS,
                 stale_window_sec: int = DEFAULT_STALE_WINDOW_SEC,
                 allow_cidrs: Optional[list] = None):
        """
        Initialize listener with production settings
        
        Args:
            bind_addr: Address to bind to
            bind_port: UDP port
            shared_keys: Map of key_id -> key string
            elevation_callback: Callback for valid elevations
            queue_size: Max packets in processing queue
            num_workers: Worker thread count
            stale_window_sec: Max age for valid packets
            allow_cidrs: Optional source IP filtering
        """
        self.bind_addr = bind_addr
        self.bind_port = bind_port
        self.elevation_callback = elevation_callback or self._default_callback
        self.queue_size = queue_size
        self.num_workers = num_workers
        self.stale_window_sec = stale_window_sec
        self.stats = Stats()
        self.running = False
        
        # Load HMAC keys
        self.hmac_keys = self._load_keys(shared_keys)
        if not self.hmac_keys:
            raise ValueError("No HMAC keys configured")
        
        # Setup allowed CIDRs
        self.allowed_networks = []
        if allow_cidrs:
            for cidr in allow_cidrs:
                self.allowed_networks.append(ipaddress.ip_network(cidr))
        
        # Replay protection
        self.replay_cache: Set[bytes] = set()
        self.replay_expire: Deque[Tuple[bytes, float]] = deque()
        self.replay_lock = threading.Lock()
        
        # Per-node sequence tracking (for future use)
        self.node_sequences: Dict[str, Deque[int]] = {}
        self.seq_lock = threading.Lock()
        
        # Processing queue and thread pool
        self.packet_queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self.executor = ThreadPoolExecutor(max_workers=num_workers, thread_name_prefix='fastpath-worker')
        
        # Create socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        # Linux-specific: allow multiple processes to bind
        if hasattr(socket, 'SO_REUSEPORT'):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
        
        # Increase receive buffer
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
        except OSError:
            pass
        
        self.sock.bind((bind_addr, bind_port))
        self.sock.settimeout(5.0)  # For periodic maintenance
        
        logger.info(f"Fast-path listener initialized: {bind_addr}:{bind_port}, "
                   f"keys={list(self.hmac_keys.keys())}, workers={num_workers}, "
                   f"queue={queue_size}, MAX_PAYLOAD={MAX_PAYLOAD}")
    
    def _load_keys(self, shared_keys: Optional[Dict[int, str]]) -> Dict[int, bytes]:
        """Load HMAC keys from various sources"""
        keys = {}
        
        # Priority 1: Constructor argument
        if shared_keys:
            for key_id, key_str in shared_keys.items():
                keys[key_id] = key_str.encode('utf-8')
        
        # Priority 2: Individual env vars
        if not keys:
            default_key = os.environ.get('ASWARM_FASTPATH_KEY')
            if default_key:
                key_id = int(os.environ.get('ASWARM_FASTPATH_KEY_ID', '1'))
                keys[key_id] = default_key.encode('utf-8')
        
        # Priority 3: JSON env var
        if not keys:
            keys_json = os.environ.get('ASWARM_FASTPATH_KEYS')
            if keys_json:
                try:
                    key_map = json.loads(keys_json)
                    for key_id_str, key_val in key_map.items():
                        # Handle base64 encoded keys
                        if key_val.startswith('base64:'):
                            key_bytes = base64.b64decode(key_val[7:])
                        else:
                            key_bytes = key_val.encode('utf-8')
                        keys[int(key_id_str)] = key_bytes
                except Exception as e:
                    logger.error(f"Failed to parse ASWARM_FASTPATH_KEYS: {e}")
        
        return keys
    
    def _default_callback(self, elevation_data: Dict[str, Any], source: Tuple[str, int]):
        """Default callback - transform to standard elevation format"""
        try:
            # Build standard elevation artifact
            now = datetime.now(timezone.utc).isoformat()
            anomaly = elevation_data.get('anomaly', {})
            
            standard_elevation = {
                "elevated": "true",
                "ts": now,
                "count": "1",
                "witnesses": str(anomaly.get('witness_count', 1)),
                "confidence": str(anomaly.get('score', 1.0)),
                "scenario": anomaly.get('event_type', 'fastpath'),
                "pattern": "fastpath",
                "run_id": elevation_data.get('run_id', ''),
                "source": "fastpath",
                "node": elevation_data.get('node_id', 'unknown'),
                "selector": anomaly.get('selector', '')
            }
            
            logger.info(f"Elevation from {source[0]}: {json.dumps(standard_elevation)}")
            
            # In cluster, would create ConfigMap here
            # self._create_elevation_configmap(standard_elevation)
            
        except Exception as e:
            logger.error(f"Callback error: {e}")
    
    def start(self):
        """Start listener and worker threads"""
        self.running = True
        
        # Start receiver thread
        self.receiver_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.receiver_thread.start()
        
        # Start worker threads
        for _ in range(self.num_workers):
            self.executor.submit(self._process_loop)
        
        # Start maintenance thread
        self.maintenance_thread = threading.Thread(target=self._maintenance_loop, daemon=True)
        self.maintenance_thread.start()
        
        logger.info("Fast-path listener started")
    
    def stop(self):
        """Graceful shutdown"""
        logger.info("Stopping fast-path listener...")
        self.running = False
        
        # Close socket to interrupt receive
        try:
            self.sock.close()
        except:
            pass
        
        # Shutdown executor
        try:
            self.executor.shutdown(wait=True, timeout=5)
        except TypeError:
            # Python < 3.9 doesn't support timeout parameter
            self.executor.shutdown(wait=True)
        
        # Wait for threads
        if hasattr(self, 'receiver_thread'):
            self.receiver_thread.join(timeout=2)
        if hasattr(self, 'maintenance_thread'):
            self.maintenance_thread.join(timeout=2)
        
        logger.info("Fast-path listener stopped")
    
    def _receive_loop(self):
        """Main receive loop - minimal processing"""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(MAX_PACKET_SIZE)
                self.stats.increment('received')
                
                # Check source filtering
                if self.allowed_networks:
                    src_ip = ipaddress.ip_address(addr[0])
                    allowed = any(src_ip in net for net in self.allowed_networks)
                    if not allowed:
                        continue
                
                # Queue for processing
                try:
                    self.packet_queue.put_nowait((data, addr, time.time()))
                except queue.Full:
                    self.stats.increment('dropped_queue_full')
                    
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logger.error(f"Receive error: {e}")
    
    def _process_loop(self):
        """Worker thread processing loop"""
        while self.running:
            try:
                # Get packet from queue
                item = self.packet_queue.get(timeout=1)
                if item is None:  # Shutdown signal
                    break
                
                data, addr, recv_time = item
                start_time = time.time()
                
                # Process packet
                self._process_packet(data, addr)
                
                # Record processing latency
                process_ms = (time.time() - start_time) * 1000
                self.stats.record_latency(process_ms)
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Worker error: {e}")
    
    def _process_packet(self, data: bytes, addr: Tuple[str, int]):
        """Process a single packet"""
        # Validate size
        if len(data) < HEADER_SIZE + HMAC_SIZE:
            return
        
        # Parse header
        try:
            header = data[:HEADER_SIZE]
            magic, version, pkt_type, ts_ns, seq16, payload_len, key_id = struct.unpack(HEADER_FMT, header)
        except struct.error:
            return
        
        # Validate magic
        if magic != MAGIC:
            self.stats.increment('invalid_magic')
            return
        
        # Validate version
        if version != VERSION:
            self.stats.increment('invalid_version')
            return
        
        # Validate type
        if pkt_type != TYPE_ELEVATION:
            self.stats.increment('invalid_type')
            return
        
        # Validate payload length
        if payload_len > MAX_PAYLOAD:
            return
        
        # Validate total packet size
        expected_size = HEADER_SIZE + payload_len + HMAC_SIZE
        if len(data) != expected_size:
            return
        
        # Extract payload and HMAC
        payload = data[HEADER_SIZE:HEADER_SIZE + payload_len]
        received_hmac = data[HEADER_SIZE + payload_len:]
        
        # Validate key exists
        if key_id not in self.hmac_keys:
            self.stats.increment('invalid_key')
            return
        
        # Verify HMAC
        h = hmac.new(self.hmac_keys[key_id], data[:HEADER_SIZE + payload_len], hashlib.sha256)
        expected_hmac = h.digest()
        if not hmac.compare_digest(received_hmac, expected_hmac):
            self.stats.increment('invalid_hmac')
            return
        
        # Check replay
        packet_hash = hashlib.sha256(data).digest()[:16]
        with self.replay_lock:
            if packet_hash in self.replay_cache:
                self.stats.increment('replays')
                return
            self.replay_cache.add(packet_hash)
            self.replay_expire.append((packet_hash, time.time() + self.stale_window_sec))
        
        # Parse JSON payload
        try:
            payload_data = json.loads(payload.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.stats.increment('invalid_json')
            return
        
        # Check staleness using wall_ts from payload
        wall_ts = payload_data.get('wall_ts')
        if wall_ts:
            try:
                # Handle ISO format or epoch float
                if isinstance(wall_ts, str):
                    wall_dt = datetime.fromisoformat(wall_ts.replace('Z', '+00:00'))
                    wall_epoch = wall_dt.timestamp()
                else:
                    wall_epoch = float(wall_ts)
                
                approx_age_ms = max(0, (time.time() - wall_epoch) * 1000)
                
                if approx_age_ms > self.stale_window_sec * 1000:
                    self.stats.increment('stale')
                    return
                    
            except (ValueError, TypeError):
                # Invalid timestamp format
                pass
        
        # Valid packet!
        self.stats.increment('valid')
        
        # Add metadata
        payload_data['_fastpath'] = {
            'source_ip': addr[0],
            'source_port': addr[1],
            'seq16': seq16,
            'key_id': key_id,
            'ts_ns': ts_ns,
            'approx_age_ms': approx_age_ms if 'approx_age_ms' in locals() else None
        }
        
        # Invoke callback
        try:
            self.elevation_callback(payload_data, addr)
        except Exception as e:
            logger.error(f"Elevation callback error: {e}")
    
    def _maintenance_loop(self):
        """Periodic maintenance tasks"""
        while self.running:
            try:
                time.sleep(10)  # Run every 10 seconds
                
                # Clean expired replay entries
                now = time.time()
                with self.replay_lock:
                    while self.replay_expire and self.replay_expire[0][1] < now:
                        expired_hash, _ = self.replay_expire.popleft()
                        self.replay_cache.discard(expired_hash)
                    
                    # Emergency cleanup if cache too large
                    if len(self.replay_cache) > REPLAY_CACHE_SIZE:
                        overflow = len(self.replay_cache) - REPLAY_CACHE_SIZE
                        for _ in range(overflow):
                            if self.replay_expire:
                                old_hash, _ = self.replay_expire.popleft()
                                self.replay_cache.discard(old_hash)
                
            except Exception as e:
                logger.error(f"Maintenance error: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics with queue info"""
        stats = self.stats.get_snapshot()
        stats['queue_depth'] = self.packet_queue.qsize()
        stats['workers_busy'] = self.executor._threads.__len__()
        return stats


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(description='A-SWARM Fast-Path UDP Listener')
    parser.add_argument('--bind', default='0.0.0.0', help='Bind address')
    parser.add_argument('--port', type=int, default=8888, help='UDP port')
    parser.add_argument('--stats-interval', type=int, default=30, help='Stats interval (sec)')
    parser.add_argument('--json-logs', action='store_true', help='Output JSON log lines')
    parser.add_argument('--allow-cidr', action='append', help='Allowed source CIDRs')
    parser.add_argument('--stale-window', type=int, default=60, help='Max packet age (sec)')
    parser.add_argument('--queue-size', type=int, default=DEFAULT_QUEUE_SIZE, help='Processing queue size')
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS, help='Worker thread count')
    
    args = parser.parse_args()
    
    # Configure JSON logging if requested
    if args.json_logs:
        # Reset to JSON-only output
        logging.getLogger().handlers.clear()
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(message)s'))
        logging.getLogger().addHandler(handler)
    
    # Create and start listener
    listener = FastPathListener(
        bind_addr=args.bind,
        bind_port=args.port,
        queue_size=args.queue_size,
        num_workers=args.workers,
        stale_window_sec=args.stale_window,
        allow_cidrs=args.allow_cidr
    )
    
    listener.start()
    
    try:
        # Log stats periodically
        while True:
            time.sleep(args.stats_interval)
            stats = listener.get_stats()
            
            if args.json_logs:
                logger.info(json.dumps(stats))
            else:
                logger.info(f"Stats: {stats}")
                
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        listener.stop()


if __name__ == '__main__':
    main()