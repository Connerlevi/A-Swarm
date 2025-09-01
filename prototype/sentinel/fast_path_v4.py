#!/usr/bin/env python3
"""
A-SWARM Fast-Path UDP Sender v0.4 - Enhanced with Replay Protection
Production-ready sender with nonce32, src_id, proper timestamp, and compatibility
"""
import argparse
import base64
import json
import logging
import os
import socket
import struct
import time
import hmac
import hashlib
import threading
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'  # JSON logs, no timestamp prefix
)
logger = logging.getLogger('sentinel.fastpath')

# Enhanced packet format constants with compatibility
MAGIC = b'ASWM'
V2 = 2
V3 = 3
TYPE_ELEVATION = 1
# V2 format: magic(4), version(1), type(1), ts_ns(8), seq16(2), payload_len(2), key_id(1)
V2_HEADER_FMT = '!4sBBQHHB'
# V3 format: magic(4), version(1), type(1), ts_unix_ms(8), src_id(4), seq16(2), nonce32(4), payload_len(2), key_id(1)
V3_HEADER_FMT = '!4sBBQLHLHB'
HMAC_SIZE = 32
MAX_PACKET_SIZE = 1200

def _parse_keyval(val: str) -> bytes:
    """Parse key value from various formats"""
    if val.startswith('base64:'):
        return base64.b64decode(val[7:])
    if val.startswith('hex:'):
        return bytes.fromhex(val[4:])
    return val.encode('utf-8')

@dataclass
class SendStats:
    """Statistics for a send operation"""
    bytes: int
    send_ms_first: float
    dupes: int
    dropped_exceptions: int

class FastPathSender:
    """UDP fast-path sender for critical anomaly signals"""
    
    def __init__(self, host: str, port: int = 8888, 
                 shared_key: Optional[str] = None,
                 key_id: int = 1,
                 dupes: int = 3,
                 gap_ms: int = 6,
                 protocol_version: int = V3,
                 node_id: Optional[str] = None):
        """
        Initialize fast-path sender
        
        Args:
            host: Target host (Pheromone service)
            port: Target UDP port
            shared_key: HMAC key (overrides env vars)
            key_id: Key identifier (1-255)
            dupes: Number of duplicate sends
            gap_ms: Gap between duplicate sends in milliseconds
            protocol_version: Protocol version (2 or 3)
            node_id: Override node ID for stable src_id
        """
        self.host = host
        self.port = port
        self.key_id = key_id
        self.dupes = dupes
        self.gap_ms = gap_ms
        self.protocol_version = protocol_version
        self.sequence = 0
        self.sequence_lock = threading.Lock()
        
        # Stable src_id from node identity (not pod hostname)
        node_name = node_id or os.environ.get('NODE_NAME') or socket.gethostname()
        self.src_id = struct.unpack('!L', hashlib.sha256(node_name.encode()).digest()[:4])[0]
        
        # Set header format based on protocol version
        if protocol_version == V3:
            self.header_fmt = V3_HEADER_FMT
            self.header_size = struct.calcsize(V3_HEADER_FMT)
        else:
            self.header_fmt = V2_HEADER_FMT
            self.header_size = struct.calcsize(V2_HEADER_FMT)
        
        # Calculate payload budget
        self.payload_budget = MAX_PACKET_SIZE - self.header_size - HMAC_SIZE
        
        # Load HMAC key with multi-key support
        self.hmac_key = self._load_key(shared_key, key_id)
        
        # Create and connect socket with IPv6 support
        self.sock = self._connect_udp(host, port)
        
        logger.info(f"FastPath sender initialized: {node_name} -> {host}:{port} (src_id={self.src_id:08x}, proto=v{protocol_version})")
    
    def _load_key(self, shared_key: Optional[str], key_id: int) -> bytes:
        """Load HMAC key with multi-key support"""
        if shared_key:
            return _parse_keyval(shared_key)
        
        # Try multi-key JSON first
        keys_json = os.environ.get('ASWARM_FASTPATH_KEYS')
        if keys_json:
            try:
                key_map = json.loads(keys_json)
                key_val = key_map.get(str(key_id)) or key_map.get(key_id)
                if key_val:
                    return _parse_keyval(key_val)
            except Exception as e:
                logger.error(f"Failed to parse ASWARM_FASTPATH_KEYS: {e}")
        
        # Fallback to single key
        key_source = os.environ.get('ASWARM_FASTPATH_KEY')
        if not key_source:
            raise ValueError("HMAC key required: set ASWARM_FASTPATH_KEY env var or pass shared_key")
        return _parse_keyval(key_source)
    
    def _connect_udp(self, host: str, port: int) -> socket.socket:
        """Connect UDP socket with IPv6 support and DSCP"""
        last_err = None
        for fam, stype, proto, _, addr in socket.getaddrinfo(host, port, 0, socket.SOCK_DGRAM):
            try:
                s = socket.socket(fam, stype, proto)
                s.connect(addr)
                
                # Set DSCP/TOS for both IPv4 and IPv6
                try:
                    if fam == socket.AF_INET:
                        s.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0xb8)
                    elif fam == socket.AF_INET6 and hasattr(socket, 'IPPROTO_IPV6'):
                        s.setsockopt(socket.IPPROTO_IPV6, getattr(socket, 'IPV6_TCLASS', 67), 0xb8)
                except OSError:
                    pass
                
                # Increase send buffer
                s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
                
                # Bound TTL for intra-cluster traffic
                try:
                    if fam == socket.AF_INET:
                        s.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, 16)
                    elif fam == socket.AF_INET6:
                        s.setsockopt(socket.IPPROTO_IPV6, getattr(socket, 'IPV6_UNICAST_HOPS', 16), 16)
                except OSError:
                    pass
                
                return s
                
            except OSError as e:
                last_err = e
                continue
        
        raise OSError(f"Could not connect UDP to {host}:{port}: {last_err}")
    
    def send_elevation(self, anomaly_data: Dict[str, Any], run_id: Optional[str] = None) -> SendStats:
        """
        Send elevation signal via UDP with duplicates
        
        Args:
            anomaly_data: Detection data (score, witness_count, selector)
            run_id: Optional run identifier
            
        Returns:
            SendStats with transmission details
        """
        # Get sequence number atomically
        with self.sequence_lock:
            seq32 = self.sequence
            seq16 = self.sequence & 0xFFFF
            self.sequence += 1
        
        # Build payload
        payload_dict = {
            'node_id': socket.gethostname(),
            'wall_ts': datetime.now(timezone.utc).isoformat(),
            'sequence32': seq32,
            'anomaly': {
                'score': anomaly_data.get('score', 0),
                'witness_count': anomaly_data.get('witness_count', 0),
                'selector': anomaly_data.get('selector', '')
            }
        }
        
        if run_id:
            payload_dict['run_id'] = run_id
        
        # Compact JSON with budget check
        payload = json.dumps(payload_dict, separators=(',', ':')).encode('utf-8')
        
        # Fail fast if base payload too large
        if len(payload) > self.payload_budget:
            raise ValueError(f"Payload {len(payload)} exceeds budget {self.payload_budget}")
        
        # Split optional fields by category
        optional_anomaly = ['detection_window_ms', 'event_type']
        optional_meta = ['pod_ips', 'extra_meta']
        
        # Add optional anomaly fields
        for field_name in optional_anomaly:
            field_value = anomaly_data.get(field_name)
            if field_value is None:
                continue
                
            test_dict = payload_dict.copy()
            test_dict['anomaly'][field_name] = field_value
            test_payload = json.dumps(test_dict, separators=(',', ':')).encode('utf-8')
            
            if len(test_payload) <= self.payload_budget:
                payload_dict['anomaly'][field_name] = field_value
                payload = test_payload
            else:
                break
        
        # Add optional meta fields
        for field_name in optional_meta:
            field_value = anomaly_data.get(field_name)
            if field_value is None:
                continue
                
            test_dict = payload_dict.copy()
            test_dict[field_name] = field_value
            test_payload = json.dumps(test_dict, separators=(',', ':')).encode('utf-8')
            
            if len(test_payload) <= self.payload_budget:
                payload_dict[field_name] = field_value
                payload = test_payload
            else:
                break
        
        # Build packet
        packet = self._build_packet(seq16, payload)
        
        # Send with duplicates
        stats = self._send_with_dupes(packet, seq16)
        
        # Log the send with enhanced metadata
        log_entry = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'seq16': seq16,
            'key_id': self.key_id,
            'src_id': f"{self.src_id:08x}",
            'dest': f'{self.host}:{self.port}',
            'proto_ver': self.protocol_version,
            'bytes': stats.bytes,
            'send_ms': round(stats.send_ms_first, 1),
            'dupes': stats.dupes
        }
        
        if logger.isEnabledFor(logging.DEBUG):
            log_entry['payload_preview'] = payload[:128].hex()
        
        logger.info(json.dumps(log_entry))
        
        return stats
    
    def _build_packet(self, seq16: int, payload: bytes) -> bytes:
        """Build authenticated packet with protocol version support"""
        if self.protocol_version == V3:
            # V3: Unix timestamp in milliseconds + src_id + nonce32
            ts_unix_ms = int(time.time() * 1000)
            
            # Get sequence for nonce derivation
            with self.sequence_lock:
                seq32 = self.sequence - 1  # Current sequence (already incremented)
            
            # Enhanced nonce: random ^ sequence to reduce collision risk
            nonce32 = secrets.randbits(32) ^ (seq16 | (seq32 << 16))
            
            header = struct.pack(
                self.header_fmt,
                MAGIC,
                V3,
                TYPE_ELEVATION,
                ts_unix_ms,
                self.src_id,
                seq16,
                nonce32,
                len(payload),
                self.key_id
            )
        else:
            # V2: timestamp in nanoseconds, no src_id/nonce
            ts_ns = int(time.time() * 1_000_000_000)
            header = struct.pack(
                self.header_fmt,
                MAGIC,
                V2,
                TYPE_ELEVATION,
                ts_ns,
                seq16,
                len(payload),
                self.key_id
            )
        
        # Calculate HMAC
        packet_data = header + payload
        h = hmac.new(self.hmac_key, packet_data, hashlib.sha256)
        packet_hmac = h.digest()
        
        return packet_data + packet_hmac
    
    def _send_with_dupes(self, packet: bytes, seq16: int) -> SendStats:
        """Send packet with duplicates using optimized timing pattern"""
        dropped = 0
        
        # Burst pattern: [0ms, gap/3ms, gap_ms] with jitter
        gaps = [0, max(1, self.gap_ms // 3), self.gap_ms]
        
        start_time = time.monotonic()
        
        # Use sendmsg if available (Linux optimization)
        send_func = getattr(self.sock, 'sendmsg', None)
        if send_func:
            for i, delay in enumerate(gaps[:self.dupes]):
                if delay:
                    jitter = secrets.randbelow(2)  # ±1ms jitter
                    time.sleep((delay + jitter) / 1000.0)
                try:
                    send_func([packet])
                except Exception as e:
                    logger.warning(f"Dupe {i} sendmsg failed for seq16={seq16}: {e}")
                    dropped += 1
        else:
            # Fallback to send()
            for i, delay in enumerate(gaps[:self.dupes]):
                if delay:
                    jitter = secrets.randbelow(2)
                    time.sleep((delay + jitter) / 1000.0)
                try:
                    self.sock.send(packet)
                except Exception as e:
                    logger.warning(f"Dupe {i} send failed for seq16={seq16}: {e}")
                    dropped += 1
        
        send_ms_first = (time.monotonic() - start_time) * 1000
        
        return SendStats(
            bytes=len(packet),
            send_ms_first=send_ms_first,
            dupes=self.dupes,
            dropped_exceptions=dropped
        )
    
    def close(self):
        """Close socket"""
        self.sock.close()


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(description='A-SWARM Fast-Path UDP Sender v0.4')
    parser.add_argument('--host', required=True, help='Target host')
    parser.add_argument('--port', type=int, default=8888, help='Target port')
    parser.add_argument('--key-id', type=int, default=1, help='Key ID (1-255)')
    parser.add_argument('--dupes', type=int, default=3, help='Duplicate sends')
    parser.add_argument('--gap-ms', type=int, default=6, help='Gap between dupes (ms)')
    parser.add_argument('--run-id', help='Optional run ID')
    parser.add_argument('--payload-file', help='JSON payload file')
    parser.add_argument('--score', type=float, help='Anomaly score')
    parser.add_argument('--selector', help='Pod selector')
    parser.add_argument('--witness-count', type=int, help='Witness count')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    parser.add_argument('--protocol-version', type=int, choices=[2,3], default=3, help='Protocol version')
    parser.add_argument('--node-id', help='Override node ID for src_id calculation')
    parser.add_argument('--key', help='One-off HMAC key (hex:/base64:/raw)')
    
    args = parser.parse_args()
    
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    # Key ID: CLI overrides env, env overrides default
    key_id = int(os.environ.get('ASWARM_FASTPATH_KEY_ID', args.key_id))
    
    # Key source: CLI --key overrides everything
    key_source = args.key if args.key else None
    
    # Build anomaly data
    if args.payload_file:
        with open(args.payload_file) as f:
            anomaly_data = json.load(f)
    else:
        anomaly_data = {}
        if args.score is not None:
            anomaly_data['score'] = args.score
        if args.selector:
            anomaly_data['selector'] = args.selector
        if args.witness_count is not None:
            anomaly_data['witness_count'] = args.witness_count
    
    # Create sender and send
    try:
        sender = FastPathSender(
            host=args.host,
            port=args.port,
            shared_key=key_source,
            key_id=key_id,
            dupes=args.dupes,
            gap_ms=args.gap_ms,
            protocol_version=args.protocol_version,
            node_id=args.node_id
        )
        
        stats = sender.send_elevation(anomaly_data, run_id=args.run_id)
        sender.close()
        
        # Human-readable summary on stderr (if not JSON logging)
        if not args.debug and logger.level > logging.DEBUG:
            print(f"Sent {stats.bytes}B in {stats.send_ms_first:.1f}ms ({stats.dupes} dupes, {stats.dropped_exceptions} failed)", file=sys.stderr)
        
        if stats.dropped_exceptions > 0:
            return 1
        return 0
        
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1


if __name__ == '__main__':
    exit(main())