#!/usr/bin/env python3
"""
A-SWARM Fast-Path UDP Sender v0.3
Production-ready sender for <200ms MTTD bypass
"""
import argparse
import json
import logging
import os
import socket
import struct
import time
import hmac
import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'  # JSON logs, no timestamp prefix
)
logger = logging.getLogger('sentinel.fastpath')

# Packet format constants
MAGIC = b'ASWM'
VERSION = 2
TYPE_ELEVATION = 1
HEADER_FMT = '!4sBBQHHB'  # magic(4), version(1), type(1), ts_ns(8), seq16(2), payload_len(2), key_id(1)
HEADER_SIZE = struct.calcsize(HEADER_FMT)
HMAC_SIZE = 32
MAX_PACKET_SIZE = 1200

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
                 gap_ms: int = 6):
        """
        Initialize fast-path sender
        
        Args:
            host: Target host (Pheromone service)
            port: Target UDP port
            shared_key: HMAC key (required)
            key_id: Key identifier (1-255)
            dupes: Number of duplicate sends
            gap_ms: Gap between duplicate sends in milliseconds
        """
        self.host = host
        self.port = port
        self.key_id = key_id
        self.dupes = dupes
        self.gap_ms = gap_ms
        self.sequence = 0
        self.sequence_lock = threading.Lock()
        
        # Get HMAC key - fail fast if missing
        key_source = shared_key or os.environ.get('ASWARM_FASTPATH_KEY')
        if not key_source:
            raise ValueError("HMAC key required: set ASWARM_FASTPATH_KEY env var or pass shared_key")
        self.hmac_key = key_source.encode('utf-8')
        
        # Create and connect socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.connect((host, port))
        
        # Set socket options
        try:
            # Set DSCP EF (Expedited Forwarding)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0xb8)
        except (AttributeError, OSError) as e:
            logger.warning(f"Could not set IP_TOS: {e}")
        
        try:
            # Increase send buffer
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        except OSError as e:
            logger.warning(f"Could not increase SO_SNDBUF: {e}")
    
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
        
        # Add optional fields if space permits
        optional_fields = [
            ('pod_ips', anomaly_data.get('pod_ips')),
            ('detection_window_ms', anomaly_data.get('detection_window_ms')),
            ('event_type', anomaly_data.get('event_type')),
            ('extra_meta', anomaly_data.get('extra_meta'))
        ]
        
        # Compact JSON
        payload = json.dumps(payload_dict, separators=(',', ':')).encode('utf-8')
        
        # Add optional fields until we hit size limit
        for field_name, field_value in optional_fields:
            if field_value is None:
                continue
                
            test_dict = payload_dict.copy()
            test_dict['anomaly'][field_name] = field_value
            test_payload = json.dumps(test_dict, separators=(',', ':')).encode('utf-8')
            
            if HEADER_SIZE + len(test_payload) + HMAC_SIZE <= MAX_PACKET_SIZE:
                payload_dict['anomaly'][field_name] = field_value
                payload = test_payload
            else:
                break  # Stop adding fields
        
        # Build packet
        packet = self._build_packet(seq16, payload)
        
        # Send with duplicates
        stats = self._send_with_dupes(packet, seq16)
        
        # Log the send
        log_entry = {
            'seq16': seq16,
            'key_id': self.key_id,
            'bytes': stats.bytes,
            'send_ms': round(stats.send_ms_first, 1),
            'dupes': stats.dupes
        }
        
        if logger.isEnabledFor(logging.DEBUG):
            log_entry['payload_preview'] = payload[:128].hex()
        
        logger.info(json.dumps(log_entry))
        
        return stats
    
    def _build_packet(self, seq16: int, payload: bytes) -> bytes:
        """Build authenticated packet"""
        # Monotonic timestamp in nanoseconds
        ts_ns = int(time.monotonic() * 1e9)
        
        # Pack header
        header = struct.pack(
            HEADER_FMT,
            MAGIC,
            VERSION,
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
        """Send packet with duplicates"""
        dropped = 0
        
        # First send
        start_time = time.monotonic()
        try:
            self.sock.send(packet)
        except Exception as e:
            logger.warning(f"Send failed for seq16={seq16}: {e}")
            dropped += 1
        
        send_ms_first = (time.monotonic() - start_time) * 1000
        
        # Duplicate sends
        for i in range(1, self.dupes):
            time.sleep(self.gap_ms / 1000.0)
            try:
                self.sock.send(packet)
            except Exception as e:
                logger.warning(f"Dupe {i} send failed for seq16={seq16}: {e}")
                dropped += 1
        
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
    parser = argparse.ArgumentParser(description='A-SWARM Fast-Path UDP Sender')
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
    
    args = parser.parse_args()
    
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    # Get key ID from env if not specified
    key_id = args.key_id
    if not args.key_id and os.environ.get('ASWARM_FASTPATH_KEY_ID'):
        key_id = int(os.environ['ASWARM_FASTPATH_KEY_ID'])
    
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
            key_id=key_id,
            dupes=args.dupes,
            gap_ms=args.gap_ms
        )
        
        stats = sender.send_elevation(anomaly_data, run_id=args.run_id)
        sender.close()
        
        if stats.dropped_exceptions > 0:
            return 1
        return 0
        
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1


if __name__ == '__main__':
    exit(main())