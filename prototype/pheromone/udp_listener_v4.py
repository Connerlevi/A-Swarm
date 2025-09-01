#!/usr/bin/env python3
"""
A-SWARM Fast-Path UDP Listener v0.4 - Production-Ready with Back-pressure & Burst Control
Enhanced receiver with ring buffer, per-IP rate limits, and adaptive degradation
"""
import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import socket
import struct
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, Set, Deque
from dataclasses import dataclass
from enum import Enum
import http.server
import socketserver
import signal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s'
)
logger = logging.getLogger('pheromone.fastpath')

# Protocol constants (must match sender)
MAGIC = b'ASWM'
VERSION = 3  # Enhanced format with src_id + nonce32
TYPE_ELEVATION = 1
# New format: magic(4), version(1), type(1), ts_unix_ms(8), src_id(4), seq16(2), nonce32(4), payload_len(2), key_id(1)
HEADER_FMT = '!4sBBQLHLHB'
HEADER_SIZE = struct.calcsize(HEADER_FMT)
HMAC_SIZE = 32
MAX_PAYLOAD = 1200 - HEADER_SIZE - HMAC_SIZE
MAX_PACKET_SIZE = 1200

# Enhanced configuration
DEFAULT_RING_BUFFER_SIZE = 10000
DEFAULT_WORKERS = min(32, max(2, (os.cpu_count() or 2) * 2))  # Scale with CPU, cap at 32
DEFAULT_STALE_WINDOW_SEC = 60
REPLAY_CACHE_SIZE = 10000
STATS_WINDOW_SIZE = 1024

# Back-pressure thresholds
DROP_RATE_WARN_THRESHOLD = 0.005  # 0.5%
DROP_RATE_DEGRADE_THRESHOLD = 0.02  # 2%
DROP_RATE_CHECK_WINDOW = 60  # seconds
DEGRADE_CHECK_WINDOW = 30  # seconds

class SystemMode(Enum):
    """System operating modes"""
    NORMAL = "normal"
    DEGRADED = "degraded"  # Audit-only mode
    OVERLOAD = "overload"  # Emergency mode

@dataclass
class RingBufferEntry:
    """Entry in the ring buffer"""
    data: bytes
    addr: Tuple[str, int]
    recv_time: float
    
class RingBuffer:
    """Thread-safe ring buffer with drop-oldest policy"""
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.buffer = deque(maxlen=capacity)
        self.lock = threading.Lock()
        self.not_empty = threading.Condition(self.lock)
        self.dropped_count = 0
        
    def push(self, entry: RingBufferEntry) -> bool:
        """Push entry, dropping oldest if full. Returns True if added without drop."""
        with self.lock:
            was_full = len(self.buffer) >= self.capacity
            if was_full:
                self.dropped_count += 1
            self.buffer.append(entry)
            self.not_empty.notify()
            return not was_full
    
    def pop(self, timeout: float = 1.0) -> Optional[RingBufferEntry]:
        """Pop entry from buffer with timeout"""
        with self.not_empty:
            if not self.buffer:
                self.not_empty.wait(timeout=timeout)
            if self.buffer:
                return self.buffer.popleft()
            return None
    
    def size(self) -> int:
        """Get current buffer size"""
        with self.lock:
            return len(self.buffer)
    
    def get_dropped_count(self) -> int:
        """Get and reset dropped count"""
        with self.lock:
            count = self.dropped_count
            self.dropped_count = 0
            return count

class Stats:
    """Enhanced thread-safe statistics tracking with rate calculations"""
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
            'dropped_queue_full': 0,
            'dropped_oldest': 0,  # Tracks ring buffer drops
            'rate_limited': 0  # Tracks per-IP rate limit drops
        }
        # Latency tracking
        self.latencies: Deque[float] = deque(maxlen=STATS_WINDOW_SIZE)
        
        # Rate tracking for back-pressure decisions
        self.rate_window_start = time.time()
        self.window_received = 0
        self.window_dropped = 0
        
    def increment(self, counter: str, count: int = 1):
        with self.lock:
            self.counters[counter] += count
            # Track windowed rates
            if counter == 'received':
                self.window_received += count
            elif counter in ['dropped_queue_full', 'dropped_oldest', 'rate_limited']:
                self.window_dropped += count
    
    def record_latency(self, ms: float):
        with self.lock:
            self.latencies.append(ms)
    
    def get_drop_rate(self) -> Tuple[float, int]:
        """Get current drop rate and window duration"""
        with self.lock:
            now = time.time()
            window_duration = now - self.rate_window_start
            
            # Reset window if too old
            if window_duration > DROP_RATE_CHECK_WINDOW:
                self.rate_window_start = now
                self.window_received = 0
                self.window_dropped = 0
                return 0.0, 0
            
            if self.window_received == 0:
                return 0.0, int(window_duration)
            
            drop_rate = self.window_dropped / self.window_received
            return drop_rate, int(window_duration)
    
    def get_snapshot(self) -> Dict[str, Any]:
        with self.lock:
            snapshot = self.counters.copy()
            if self.latencies:
                sorted_latencies = sorted(self.latencies)
                n = len(sorted_latencies)
                snapshot['p50_ms'] = round(sorted_latencies[n//2], 2)
                # Clamp indices to prevent out of range
                idx95 = max(0, min(n - 1, int(n * 0.95)))
                idx99 = max(0, min(n - 1, int(n * 0.99)))
                snapshot['p95_ms'] = round(sorted_latencies[idx95], 2)
                snapshot['p99_ms'] = round(sorted_latencies[idx99], 2)
            else:
                snapshot['p50_ms'] = 0
                snapshot['p95_ms'] = 0
                snapshot['p99_ms'] = 0
            
            # Add rate metrics
            drop_rate, window = self.get_drop_rate()
            snapshot['drop_rate'] = round(drop_rate, 4)
            snapshot['rate_window_sec'] = window
            
        return snapshot

class FastPathListener:
    """Enhanced UDP listener with back-pressure and burst control"""
    
    def __init__(self, bind_addr: str = '0.0.0.0', bind_port: int = 8888,
                 shared_keys: Optional[Dict[int, str]] = None,
                 elevation_callback: Optional[callable] = None,
                 ring_buffer_size: int = DEFAULT_RING_BUFFER_SIZE,
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
            ring_buffer_size: Size of ring buffer for burst absorption
            num_workers: Worker thread count (default: CPU×2)
            stale_window_sec: Max age for valid packets
            allow_cidrs: Optional source IP filtering
        """
        self.bind_addr = bind_addr
        self.bind_port = bind_port
        self.elevation_callback = elevation_callback or self._default_callback
        self.num_workers = num_workers
        self.stale_window_sec = stale_window_sec
        self.stats = Stats()
        self.running = False
        
        # System mode for adaptive behavior
        self.system_mode = SystemMode.NORMAL
        self.mode_lock = threading.Lock()
        self.last_degrade_check = time.time()
        self.sample_divisor = 8  # For OVERLOAD mode sampling
        
        # Per-IP rate limiting
        self.rate_caps = {'capacity': 100, 'fill_per_sec': 50}
        self._buckets: Dict[str, Tuple[float, float]] = {}  # ip -> (tokens, last_ts)
        self._bucket_lock = threading.Lock()
        
        # Track inflight work
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        
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
        
        # Per-source sequence tracking with sliding window
        self.src_sequences: Dict[int, Tuple[int, Set[int]]] = {}  # src_id -> (highest_seq, bloom_of_last_256)
        self.seq_lock = threading.Lock()
        
        # Ring buffer for burst absorption
        self.ring_buffer = RingBuffer(ring_buffer_size)
        
        # Worker thread pool
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
        
        # Increase receive buffer (try larger sizes)
        for sz in (8*1024*1024, 4*1024*1024, 1*1024*1024, 262144):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, sz)
                logger.debug(f"Set UDP receive buffer to {sz} bytes")
                break
            except OSError:
                continue
        
        self.sock.bind((bind_addr, bind_port))
        self.sock.settimeout(5.0)  # For periodic maintenance
        
        logger.info(f"Fast-path listener initialized: {bind_addr}:{bind_port}, "
                   f"keys={list(self.hmac_keys.keys())}, workers={num_workers}, "
                   f"ring_buffer={ring_buffer_size}, MAX_PAYLOAD={MAX_PAYLOAD}")
    
    def _parse_keyval(self, val: str) -> bytes:
        """Parse key value from various formats"""
        if val.startswith('base64:'):
            return base64.b64decode(val[7:])
        if val.startswith('hex:'):
            return bytes.fromhex(val[4:])
        return val.encode('utf-8')
    
    def _load_keys(self, shared_keys: Optional[Dict[int, str]]) -> Dict[int, bytes]:
        """Load HMAC keys from various sources"""
        keys = {}
        
        # Priority 1: Constructor argument
        if shared_keys:
            for key_id, key_str in shared_keys.items():
                keys[key_id] = self._parse_keyval(key_str)
        
        # Priority 2: Individual env vars
        if not keys:
            default_key = os.environ.get('ASWARM_FASTPATH_KEY')
            if default_key:
                key_id = int(os.environ.get('ASWARM_FASTPATH_KEY_ID', '1'))
                keys[key_id] = self._parse_keyval(default_key)
        
        # Priority 3: JSON env var
        if not keys:
            keys_json = os.environ.get('ASWARM_FASTPATH_KEYS')
            if keys_json:
                try:
                    key_map = json.loads(keys_json)
                    for key_id_str, key_val in key_map.items():
                        keys[int(key_id_str)] = self._parse_keyval(key_val)
                except Exception as e:
                    logger.error(f"Failed to parse ASWARM_FASTPATH_KEYS: {e}")
        
        return keys
    
    def reload_keys(self):
        """Reload keys from environment (for hot reload)"""
        new_keys = self._load_keys(None)
        if new_keys:
            self.hmac_keys = new_keys
            logger.info(f"Reloaded HMAC keys: {list(new_keys.keys())}")
    
    def _default_callback(self, elevation_data: Dict[str, Any], source: Tuple[str, int]):
        """Default callback - transform to standard elevation format"""
        # Skip callbacks in degraded mode (audit-only)
        if self.system_mode != SystemMode.NORMAL:
            logger.debug(f"Skipping callback in {self.system_mode.value} mode")
            return
            
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
        
        # Start monitoring thread for back-pressure
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        # Start HTTP health/metrics server
        self._httpd = self._start_http(self.get_stats, port=int(os.environ.get('ASWARM_HTTP_PORT', '9000')))
        
        # Setup signal handlers for key reload
        signal.signal(signal.SIGHUP, lambda sig, frame: self.reload_keys())
        
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
        for thread in ['receiver_thread', 'maintenance_thread', 'monitor_thread']:
            if hasattr(self, thread):
                getattr(self, thread).join(timeout=2)
        
        logger.info("Fast-path listener stopped")
    
    def _receive_loop(self):
        """Main receive loop - minimal processing with ring buffer"""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(MAX_PACKET_SIZE)
                self.stats.increment('received')
                
                src_ip = addr[0]
                
                # Check source filtering
                if self.allowed_networks:
                    ip_obj = ipaddress.ip_address(src_ip)
                    if not any(ip_obj in net for net in self.allowed_networks):
                        continue
                
                # Per-IP rate limit (before expensive operations)
                if not self._allow_ip(src_ip):
                    continue
                
                # Add to ring buffer
                entry = RingBufferEntry(data, addr, time.time())
                self.ring_buffer.push(entry)  # Dropped count tracked internally
                    
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logger.error(f"Receive error: {e}", exc_info=True)
    
    def _process_loop(self):
        """Worker thread processing loop - pulls from ring buffer"""
        while self.running:
            try:
                # Get packet from ring buffer
                entry = self.ring_buffer.pop(timeout=1.0)
                if entry is None:
                    continue
                
                # Track inflight work
                with self._inflight_lock:
                    self._inflight += 1
                
                try:
                    start_time = time.time()
                    
                    # Check for OVERLOAD sampling
                    with self.mode_lock:
                        overload = (self.system_mode == SystemMode.DEGRADED and 
                                   self.ring_buffer.size() / self.ring_buffer.capacity > 0.98)
                    
                    if overload and (int(entry.recv_time * 1e9 + entry.addr[1]) % self.sample_divisor != 0):
                        # Sample 1/8 packets in OVERLOAD mode
                        continue
                    
                    # Process packet
                    self._process_packet(entry.data, entry.addr)
                    
                    # Record processing latency
                    process_ms = (time.time() - start_time) * 1000
                    self.stats.record_latency(process_ms)
                finally:
                    with self._inflight_lock:
                        self._inflight -= 1
                        
            except Exception as e:
                logger.error(f"Worker error: {e}", exc_info=True)
    
    def _process_packet(self, data: bytes, addr: Tuple[str, int]):
        """Process a single packet"""
        # Validate size
        if len(data) < HEADER_SIZE + HMAC_SIZE:
            return
        
        # Parse header
        try:
            header = data[:HEADER_SIZE]
            magic, version, pkt_type, ts_unix_ms, src_id, seq16, nonce32, payload_len, key_id = struct.unpack(HEADER_FMT, header)
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
        
        # Check timestamp age before expensive operations (5s window)
        now_ms = int(time.time() * 1000)
        age_ms = abs(now_ms - ts_unix_ms)
        if age_ms > 5000:  # Strict 5-second window
            self.stats.increment('stale')
            return
        
        # Verify HMAC (use memoryview to avoid copy)
        view = memoryview(data)
        h = hmac.new(self.hmac_keys[key_id], view[:HEADER_SIZE + payload_len], hashlib.sha256)
        expected_hmac = h.digest()
        if not hmac.compare_digest(received_hmac, expected_hmac):
            self.stats.increment('invalid_hmac')
            return
        
        # Enhanced replay protection with sequence tracking
        src_id_key = f"{src_id:08x}"
        with self.seq_lock:
            highest_seq, seen_seqs = self.src_sequences.get(src_id, (0, set()))
            
            # Check if sequence is too old or already seen
            if seq16 <= highest_seq - 256:  # Too old
                self.stats.increment('replays')
                return
            elif seq16 in seen_seqs:  # Already seen
                self.stats.increment('replays')
                return
            
            # Update tracking
            if seq16 > highest_seq:
                highest_seq = seq16
            seen_seqs.add(seq16)
            
            # Keep bloom at 256 entries max
            if len(seen_seqs) > 256:
                min_keep = highest_seq - 255
                seen_seqs = {s for s in seen_seqs if s >= min_keep}
            
            self.src_sequences[src_id] = (highest_seq, seen_seqs)
        
        # Traditional packet hash replay check (secondary)
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
        
        # Check staleness using wall_ts from payload (secondary check)
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
            'src_id': f"{src_id:08x}",
            'seq16': seq16,
            'nonce32': f"{nonce32:08x}",
            'key_id': key_id,
            'ts_unix_ms': ts_unix_ms,
            'age_ms': age_ms
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
                
                # Collect dropped packets from ring buffer
                dropped = self.ring_buffer.get_dropped_count()
                if dropped > 0:
                    self.stats.increment('dropped_oldest', dropped)
                
            except Exception as e:
                logger.error(f"Maintenance error: {e}")
    
    def _monitor_loop(self):
        """Monitor system health and apply back-pressure"""
        while self.running:
            try:
                time.sleep(1)  # Check every second
                
                # Get current metrics
                drop_rate, window = self.stats.get_drop_rate()
                q_depth = self.ring_buffer.size()
                q_ratio = q_depth / max(1, self.ring_buffer.capacity)
                
                # Only check if we have enough data
                if window < 10:
                    continue
                
                with self.mode_lock:
                    current_mode = self.system_mode
                    now = time.time()
                    
                    # Queue-pressure driven degrade
                    if q_ratio > 0.9:
                        hot_for = now - self.last_degrade_check
                        if hot_for > 3 and current_mode == SystemMode.NORMAL:
                            self.system_mode = SystemMode.DEGRADED
                            logger.warning(f"System degraded to audit-only mode (queue_ratio={q_ratio:.2%})")
                            self._emit_metric('aswarm_mode_change', {'mode': 'degraded', 'reason': 'queue_pressure'})
                    
                    # Drop rate check (secondary)
                    elif drop_rate > DROP_RATE_DEGRADE_THRESHOLD:
                        if current_mode == SystemMode.NORMAL:
                            if now - self.last_degrade_check > DEGRADE_CHECK_WINDOW:
                                self.system_mode = SystemMode.DEGRADED
                                logger.warning(f"System degraded to audit-only mode (drop_rate={drop_rate:.2%})")
                                self._emit_metric('aswarm_mode_change', {'mode': 'degraded', 'reason': 'high_drop_rate'})
                            
                    elif drop_rate > DROP_RATE_WARN_THRESHOLD:
                        if current_mode == SystemMode.NORMAL:
                            logger.warning(f"High drop rate: {drop_rate:.2%} over {window}s")
                            self._emit_metric('aswarm_drop_rate_warning', {'rate': drop_rate})
                    
                    # Recovery conditions
                    elif q_ratio < 0.2 and drop_rate < DROP_RATE_WARN_THRESHOLD / 2:
                        if current_mode == SystemMode.DEGRADED:
                            self.system_mode = SystemMode.NORMAL
                            logger.info("System recovered to normal mode")
                            self._emit_metric('aswarm_mode_change', {'mode': 'normal', 'reason': 'queue_recovered'})
                            self.last_degrade_check = now
                
            except Exception as e:
                logger.error(f"Monitor error: {e}")
    
    def _emit_metric(self, metric_name: str, labels: Dict[str, Any]):
        """Emit metric for monitoring (stub for Prometheus integration)"""
        logger.info(f"METRIC: {metric_name} {json.dumps(labels)}")
    
    def _allow_ip(self, ip: str) -> bool:
        """Check if IP is allowed under rate limit"""
        now = time.time()
        with self._bucket_lock:
            tokens, last = self._buckets.get(ip, (self.rate_caps['capacity'], now))
            delta = max(0.0, now - last)
            tokens = min(self.rate_caps['capacity'], tokens + delta * self.rate_caps['fill_per_sec'])
            if tokens < 1.0:
                self.stats.increment('rate_limited')
                self._buckets[ip] = (tokens, now)
                return False
            tokens -= 1.0
            self._buckets[ip] = (tokens, now)
            return True
    
    def _start_http(self, get_stats_fn, port=9000):
        """Start HTTP server for health/metrics"""
        class _Health(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/healthz':
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'ok')
                elif self.path == '/metrics':
                    snap = get_stats_fn()
                    lines = []
                    for k, v in snap.items():
                        if isinstance(v, (int, float)):
                            # Convert to prometheus format
                            metric_name = f"aswarm_{k}"
                            lines.append(f"{metric_name} {v}")
                    body = '\n'.join(lines).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain')
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()
            
            def log_message(self, *args):
                return  # Disable request logging
        
        handler = _Health
        httpd = socketserver.TCPServer(("0.0.0.0", port), handler)
        httpd.get_stats = get_stats_fn
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        logger.info(f"HTTP health/metrics server started on port {port}")
        return httpd
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics with enhanced metrics"""
        stats = self.stats.get_snapshot()
        stats['queue_depth'] = self.ring_buffer.size()
        
        # Track actual inflight work
        with self._inflight_lock:
            stats['workers_busy'] = self._inflight
            
        stats['ring_buffer_capacity'] = self.ring_buffer.capacity
        
        with self.mode_lock:
            stats['system_mode'] = self.system_mode.value
            
        # Calculate total drops (monotonic counter)
        stats['aswarm_ingest_dropped_total'] = (
            stats.get('dropped_oldest', 0) + 
            stats.get('rate_limited', 0)
        )
        
        return stats


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(description='A-SWARM Fast-Path UDP Listener v0.4 (Production)')
    parser.add_argument('--bind', default='0.0.0.0', help='Bind address')
    parser.add_argument('--port', type=int, default=8888, help='UDP port')
    parser.add_argument('--stats-interval', type=int, default=30, help='Stats interval (sec)')
    parser.add_argument('--json-logs', action='store_true', help='Output JSON log lines')
    parser.add_argument('--allow-cidr', action='append', help='Allowed source CIDRs')
    parser.add_argument('--stale-window', type=int, default=60, help='Max packet age (sec)')
    parser.add_argument('--ring-buffer-size', type=int, default=DEFAULT_RING_BUFFER_SIZE, 
                       help='Ring buffer size for burst control')
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
        ring_buffer_size=args.ring_buffer_size,
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
                
                # Alert on drop rate
                if stats.get('drop_rate', 0) > DROP_RATE_WARN_THRESHOLD:
                    logger.warning(f"⚠️  Drop rate alert: {stats['drop_rate']:.2%}")
                    
                # Show mode transitions
                logger.info(f"Mode: {stats['system_mode']}, Queue: {stats['queue_depth']}/{stats['ring_buffer_capacity']}")
                
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        listener.stop()


if __name__ == '__main__':
    main()