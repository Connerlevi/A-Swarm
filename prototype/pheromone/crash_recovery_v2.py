#!/usr/bin/env python3
"""
A-SWARM Crash Recovery v2 - Production-ready WAL implementation
Persistent write-ahead log for anomaly events with boot-time replay
"""
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
import fcntl
import signal
import queue
from collections import deque

logger = logging.getLogger('aswarm.recovery')

@dataclass
class WALEntry:
    """Write-ahead log entry for anomaly persistence"""
    id: str
    ts: str  # ISO timestamp
    seq: int
    src_id: str
    anomaly_data: Dict[str, Any]
    committed: bool = False

class WALManager:
    """Thread-safe Write-Ahead Log for crash recovery"""
    
    def __init__(self, wal_dir: str = "/tmp/aswarm-wal", 
                 max_entries: int = 10000,
                 fsync_every_n: int = 1,
                 fsync_every_ms: int = 5):
        """
        Initialize WAL manager
        
        Args:
            wal_dir: Directory for WAL files (should be persistent volume)
            max_entries: Maximum entries to keep in WAL
            fsync_every_n: Fsync after N writes (1 = every write)
            fsync_every_ms: Max time between fsyncs (ms)
        """
        self.wal_dir = Path(wal_dir)
        self.max_entries = max_entries
        self.fsync_every_n = fsync_every_n
        self.fsync_every_ms = fsync_every_ms
        self.lock = threading.Lock()
        self.sequence = 0
        
        # Batching for fsync optimization
        self.write_queue = queue.Queue(maxsize=1000)
        self.pending_writes = 0
        self.last_fsync = time.time()
        
        # Create WAL directory
        self.wal_dir.mkdir(parents=True, exist_ok=True)
        
        # WAL file paths
        self.wal_file = self.wal_dir / "aswarm.wal"
        self.committed_file = self.wal_dir / "aswarm.committed"
        self.instance_file = self.wal_dir / "instance.lock"
        
        # Set secure permissions
        self._secure_files()
        
        # Generate unique instance ID
        self.instance_id = str(uuid.uuid4())
        
        # Recovery state
        self.replay_count = 0
        self.startup_complete = False
        
        # Disk space monitoring
        self.last_disk_check = 0
        self.disk_free_bytes = 0
        
        logger.info(f"WAL manager initialized: {self.wal_dir} (instance={self.instance_id[:8]}, fsync_every={fsync_every_n})")
    
    def _secure_files(self):
        """Set secure permissions on WAL files"""
        for p in [self.wal_file, self.committed_file, self.instance_file]:
            try:
                os.chmod(p, 0o600)
            except FileNotFoundError:
                pass
    
    def start(self) -> List[WALEntry]:
        """
        Start WAL manager and perform crash recovery
        
        Returns:
            List of entries that need to be replayed
        """
        # Acquire instance lock
        self._acquire_instance_lock()
        
        # Load existing WAL
        entries = self._load_wal()
        committed_ids = self._load_committed_ids()
        
        # Mark committed in-memory (source of truth is committed file)
        for e in entries:
            if e.id in committed_ids:
                e.committed = True
        
        # Find uncommitted entries for replay
        replay_entries = [e for e in entries if not e.committed]
        
        if replay_entries:
            logger.info(f"Found {len(replay_entries)} uncommitted entries for replay")
            self.replay_count = len(replay_entries)
        
        # Update sequence counter
        if entries:
            self.sequence = max(e.seq for e in entries) + 1
        
        # Start writer thread if batching enabled
        if self.fsync_every_n > 1:
            self.writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
            self.writer_thread.start()
        
        self.startup_complete = True
        
        # Emit recovery status
        status = {
            "event": "wal_recovery",
            "replay_count": len(replay_entries),
            "total_entries": len(entries),
            "committed_entries": len(committed_ids),
            "wal_dir": str(self.wal_dir),
            "instance": self.instance_id[:8]
        }
        logger.info(json.dumps(status))
        
        return replay_entries
    
    def append(self, src_id: str, anomaly_data: Dict[str, Any]) -> WALEntry:
        """
        Append anomaly to WAL (thread-safe)
        
        Args:
            src_id: Source identifier
            anomaly_data: Anomaly detection data
            
        Returns:
            WAL entry that was written
        """
        # Check disk space periodically
        if time.time() - self.last_disk_check > 60:
            self._check_disk_space()
        
        with self.lock:
            entry = WALEntry(
                id=str(uuid.uuid4()),
                ts=datetime.now(timezone.utc).isoformat(),
                seq=self.sequence,
                src_id=src_id,
                anomaly_data=anomaly_data,
                committed=False
            )
            
            self.sequence += 1
            
            # Write to WAL file
            if self.fsync_every_n == 1:
                # Direct write with immediate fsync
                self._write_entry_direct(entry)
            else:
                # Queue for batched write
                try:
                    self.write_queue.put_nowait(entry)
                    self.pending_writes += 1
                except queue.Full:
                    # Fallback to direct write if queue full
                    logger.warning("Write queue full, using direct write")
                    self._write_entry_direct(entry)
            
            # Log structured event
            event = {
                "event": "wal_append",
                "entry_id": entry.id,
                "seq": entry.seq,
                "src_id": src_id
            }
            logger.debug(json.dumps(event))
            
            return entry
    
    def commit(self, entry_id: str):
        """Mark entry as committed (processed successfully)"""
        with self.lock:
            # Append to committed log
            commit_record = {
                'id': entry_id,
                'ts': datetime.now(timezone.utc).isoformat(),
                'instance_id': self.instance_id
            }
            
            with open(self.committed_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(commit_record) + '\n')
                f.flush()
                os.fsync(f.fileno())
            
            # Log structured event
            event = {
                "event": "wal_commit",
                "entry_id": entry_id,
                "instance": self.instance_id[:8]
            }
            logger.debug(json.dumps(event))
    
    def cleanup(self):
        """Clean up old WAL entries (never drops uncommitted)"""
        if not self.startup_complete:
            return
            
        with self.lock:
            # Load all entries
            entries = self._load_wal()
            committed_ids = self._load_committed_ids()
            
            # Separate uncommitted and committed
            uncommitted = [e for e in entries if e.id not in committed_ids]
            committed = [e for e in entries if e.id in committed_ids]
            
            # Time-based retention only for committed
            cutoff = time.time() - 3600  # 1 hour
            committed_recent = []
            for e in committed:
                try:
                    entry_ts = datetime.fromisoformat(e.ts).timestamp()
                    if entry_ts >= cutoff:
                        committed_recent.append(e)
                except Exception:
                    # Keep on parse error
                    committed_recent.append(e)
            
            # Always keep all uncommitted, trim committed if needed
            keep_entries = uncommitted + sorted(committed_recent, key=lambda e: e.seq, reverse=True)
            
            if len(keep_entries) > self.max_entries:
                # Only trim committed entries
                headroom = self.max_entries - len(uncommitted)
                if headroom > 0:
                    keep_entries = uncommitted + committed_recent[:headroom]
                else:
                    # WAL is full of uncommitted entries - emit warning
                    logger.warning(f"WAL has {len(uncommitted)} uncommitted entries (limit: {self.max_entries})")
                    keep_entries = uncommitted  # Keep only uncommitted
            
            # Rewrite WAL file
            self._rewrite_wal(keep_entries)
            
            logger.debug(f"WAL cleanup: kept {len(keep_entries)}/{len(entries)} entries ({len(uncommitted)} uncommitted)")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get WAL statistics for monitoring"""
        with self.lock:
            entries = self._load_wal()
            committed_ids = self._load_committed_ids()
            
            uncommitted_count = sum(1 for e in entries if e.id not in committed_ids)
            
            # Get WAL file size
            wal_size = 0
            try:
                wal_size = self.wal_file.stat().st_size
            except Exception:
                pass
            
            return {
                "wal_entries_total": len(entries),
                "wal_uncommitted": uncommitted_count,
                "wal_size_bytes": wal_size,
                "disk_free_bytes": self.disk_free_bytes,
                "replay_count_last_boot": self.replay_count
            }
    
    def _check_disk_space(self):
        """Check available disk space"""
        try:
            stat = os.statvfs(self.wal_dir)
            self.disk_free_bytes = stat.f_bavail * stat.f_bsize
            self.last_disk_check = time.time()
            
            # Warn if low disk space
            if self.disk_free_bytes < 100 * 1024 * 1024:  # < 100MB
                logger.warning(f"Low disk space: {self.disk_free_bytes / 1024 / 1024:.1f}MB free")
        except Exception as e:
            logger.error(f"Disk check failed: {e}")
    
    def _acquire_instance_lock(self):
        """Acquire instance lock to prevent multiple processes"""
        try:
            self.lock_fd = open(self.instance_file, 'w')
            fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            
            # Write instance info
            instance_info = {
                'instance_id': self.instance_id,
                'pid': os.getpid(),
                'start_ts': datetime.now(timezone.utc).isoformat(),
                'hostname': os.uname().nodename
            }
            
            self.lock_fd.write(json.dumps(instance_info))
            self.lock_fd.flush()
            
        except (IOError, OSError) as e:
            raise RuntimeError(f"Could not acquire instance lock: {e}")
    
    def _write_entry_direct(self, entry: WALEntry):
        """Write entry to WAL file with immediate fsync"""
        line = json.dumps(asdict(entry)) + '\n'
        
        with open(self.wal_file, 'a', encoding='utf-8') as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())  # Force to disk
    
    def _writer_loop(self):
        """Background writer thread for batched fsync"""
        batch = []
        
        while True:
            try:
                # Wait for entries with timeout
                timeout = self.fsync_every_ms / 1000.0
                
                try:
                    entry = self.write_queue.get(timeout=timeout)
                    batch.append(entry)
                except queue.Empty:
                    pass
                
                # Flush if batch size reached or timeout
                should_flush = (
                    len(batch) >= self.fsync_every_n or
                    (batch and time.time() - self.last_fsync > timeout)
                )
                
                if should_flush and batch:
                    # Write batch
                    with open(self.wal_file, 'a', encoding='utf-8') as f:
                        for entry in batch:
                            line = json.dumps(asdict(entry)) + '\n'
                            f.write(line)
                        f.flush()
                        os.fsync(f.fileno())
                    
                    self.last_fsync = time.time()
                    with self.lock:
                        self.pending_writes -= len(batch)
                    
                    batch.clear()
                    
            except Exception as e:
                logger.error(f"Writer thread error: {e}")
    
    def _load_wal(self) -> List[WALEntry]:
        """Load all entries from WAL file"""
        entries = []
        
        if not self.wal_file.exists():
            return entries
        
        try:
            with open(self.wal_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        data = json.loads(line)
                        entry = WALEntry(**data)
                        entries.append(entry)
                    except Exception as e:
                        logger.warning(f"Corrupt WAL line {line_num}: {e}")
                        
        except Exception as e:
            logger.error(f"Failed to load WAL: {e}")
        
        return entries
    
    def _load_committed_ids(self) -> set:
        """Load set of committed entry IDs"""
        committed = set()
        
        if not self.committed_file.exists():
            return committed
        
        try:
            with open(self.committed_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        data = json.loads(line)
                        committed.add(data['id'])
                    except Exception:
                        pass  # Skip corrupt lines
                        
        except Exception as e:
            logger.error(f"Failed to load committed IDs: {e}")
        
        return committed
    
    def _rewrite_wal(self, entries: List[WALEntry]):
        """Rewrite WAL file with given entries"""
        temp_file = self.wal_file.with_suffix('.tmp')
        
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                for entry in entries:
                    f.write(json.dumps(asdict(entry)) + '\n')
                f.flush()
                os.fsync(f.fileno())
            
            # Atomic rename
            temp_file.replace(self.wal_file)
            
            # Update permissions
            os.chmod(self.wal_file, 0o600)
            
        except Exception as e:
            logger.error(f"Failed to rewrite WAL: {e}")
            if temp_file.exists():
                temp_file.unlink()

class CrashRecoveryManager:
    """High-level crash recovery coordinator"""
    
    def __init__(self, wal_dir: str = "/tmp/aswarm-wal", 
                 elevation_callback: Optional[callable] = None,
                 backpressure_callback: Optional[callable] = None,
                 fsync_every_n: int = 1):
        """
        Initialize crash recovery manager
        
        Args:
            wal_dir: Directory for WAL persistence
            elevation_callback: Callback for replaying elevations
            backpressure_callback: Callback when WAL is under pressure
            fsync_every_n: Fsync batching (1 = every write)
        """
        self.wal = WALManager(wal_dir, fsync_every_n=fsync_every_n)
        self.elevation_callback = elevation_callback or self._default_elevation_callback
        self.backpressure_callback = backpressure_callback
        self.cleanup_timer = None
        
    def start(self):
        """Start recovery manager and replay uncommitted entries"""
        # Perform crash recovery
        replay_entries = self.wal.start()
        
        if replay_entries:
            logger.info(f"Performing crash recovery: replaying {len(replay_entries)} entries")
            
            for entry in replay_entries:
                try:
                    # Reconstruct elevation data
                    elevation_data = entry.anomaly_data.copy()
                    elevation_data['_recovery'] = {
                        'entry_id': entry.id,
                        'original_ts': entry.ts,
                        'src_id': entry.src_id
                    }
                    
                    # Replay the elevation
                    self.elevation_callback(elevation_data, ('recovery', 0))
                    
                    # Log structured event
                    event = {
                        "event": "wal_replay",
                        "entry_id": entry.id,
                        "seq": entry.seq,
                        "src_id": entry.src_id
                    }
                    logger.info(json.dumps(event))
                    
                except Exception as e:
                    logger.error(f"Failed to replay entry {entry.id}: {e}")
        
        # Start periodic cleanup
        self._start_cleanup_timer()
        
        logger.info("Crash recovery complete")
    
    def log_anomaly(self, src_id: str, anomaly_data: Dict[str, Any]) -> str:
        """
        Log anomaly to WAL and return entry ID
        
        Args:
            src_id: Source identifier
            anomaly_data: Anomaly data to persist
            
        Returns:
            Entry ID for later commit
        """
        # Check WAL pressure
        stats = self.wal.get_stats()
        if stats['wal_uncommitted'] > 1000:  # Threshold
            logger.warning(f"WAL pressure: {stats['wal_uncommitted']} uncommitted entries")
            if self.backpressure_callback:
                self.backpressure_callback('wal_pressure', stats)
        
        entry = self.wal.append(src_id, anomaly_data)
        return entry.id
    
    def commit_anomaly(self, entry_id: str):
        """Mark anomaly as successfully processed"""
        self.wal.commit(entry_id)
    
    def get_health(self) -> Dict[str, Any]:
        """Get health metrics for monitoring"""
        return self.wal.get_stats()
    
    def _default_elevation_callback(self, anomaly_data: Dict[str, Any], source: Tuple[str, int]):
        """Default callback for replayed elevations"""
        logger.info(f"REPLAY: {json.dumps(anomaly_data)}")
    
    def _start_cleanup_timer(self):
        """Start periodic WAL cleanup"""
        def cleanup_loop():
            while True:
                time.sleep(300)  # Clean every 5 minutes
                try:
                    self.wal.cleanup()
                except Exception as e:
                    logger.error(f"WAL cleanup error: {e}")
        
        self.cleanup_timer = threading.Thread(target=cleanup_loop, daemon=True)
        self.cleanup_timer.start()
    
    def stop(self):
        """Graceful shutdown"""
        logger.info("Stopping crash recovery manager")
        # Cleanup timer is daemon thread, will stop automatically

# Kubernetes lease management for instance coordination
class LeaseManager:
    """Manage Kubernetes leases with TTL and instance tracking"""
    
    def __init__(self, namespace: str = "aswarm", lease_name: str = "pheromone-leader"):
        """
        Initialize lease manager
        
        Args:
            namespace: Kubernetes namespace
            lease_name: Name of the coordination lease
        """
        self.namespace = namespace
        self.lease_name = lease_name
        self.instance_id = str(uuid.uuid4())
        self.lease_ttl = 30  # seconds
        self.running = False
        self.api = None
        
        # Try to load Kubernetes config
        try:
            from kubernetes import client, config
            
            # Try in-cluster config first, fall back to kubeconfig
            try:
                config.load_incluster_config()
            except:
                config.load_kube_config()
            
            self.api = client.CoordinationV1Api()
            logger.info(f"Kubernetes API client initialized")
        except Exception as e:
            logger.warning(f"Could not initialize Kubernetes client: {e}")
    
    def start(self):
        """Start lease management"""
        if not self.api:
            logger.warning("Lease manager disabled - no Kubernetes API")
            return
            
        self.running = True
        
        # Create initial lease
        self._create_or_update_lease()
        
        # Start renewal thread
        renewal_thread = threading.Thread(target=self._renewal_loop, daemon=True)
        renewal_thread.start()
        
        # Setup graceful shutdown (only in main thread)
        try:
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTERM, self._handle_shutdown)
                signal.signal(signal.SIGINT, self._handle_shutdown)
        except Exception as e:
            logger.debug(f"Signal hook skipped: {e}")
        
        logger.info(f"Lease manager started: {self.lease_name} (instance={self.instance_id[:8]})")
    
    def _create_or_update_lease(self):
        """Create or update Kubernetes lease"""
        if not self.api:
            return
            
        from kubernetes import client
        from kubernetes.client import ApiException
        
        body = client.V1Lease(
            metadata=client.V1ObjectMeta(
                name=self.lease_name,
                namespace=self.namespace,
                annotations={
                    "aswarm.ai/instance-id": self.instance_id,
                    "aswarm.ai/updated": datetime.now(timezone.utc).isoformat(),
                    "aswarm.ai/pid": str(os.getpid()),
                    "aswarm.ai/hostname": os.uname().nodename
                }
            ),
            spec=client.V1LeaseSpec(
                holder_identity=self.instance_id,
                lease_duration_seconds=self.lease_ttl,
                renew_time=datetime.now(timezone.utc)
            )
        )
        
        try:
            # Try to patch existing lease
            self.api.patch_namespaced_lease(
                name=self.lease_name,
                namespace=self.namespace,
                body=body
            )
            logger.debug(f"Updated lease {self.lease_name}")
        except ApiException as e:
            if e.status == 404:
                # Create new lease
                try:
                    self.api.create_namespaced_lease(
                        namespace=self.namespace,
                        body=body
                    )
                    logger.info(f"Created lease {self.lease_name}")
                except ApiException as create_err:
                    logger.error(f"Failed to create lease: {create_err}")
            else:
                logger.error(f"Lease update error: {e}")
    
    def _renewal_loop(self):
        """Periodically renew the lease"""
        while self.running:
            try:
                time.sleep(self.lease_ttl // 3)  # Renew at 1/3 TTL
                if self.running:
                    self._create_or_update_lease()
            except Exception as e:
                logger.error(f"Lease renewal error: {e}")
    
    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down gracefully")
        self.stop()
    
    def stop(self):
        """Stop lease manager and clean up lease"""
        self.running = False
        
        if not self.api:
            return
            
        # Delete lease on graceful shutdown
        try:
            from kubernetes.client import ApiException
            
            self.api.delete_namespaced_lease(
                name=self.lease_name,
                namespace=self.namespace
            )
            logger.info(f"Released lease {self.lease_name}")
        except ApiException as e:
            if e.status != 404:
                logger.warning(f"Failed to release lease: {e}")
        except Exception as e:
            logger.error(f"Lease cleanup error: {e}")

def integrate_crash_recovery(udp_listener, wal_dir: str = "/tmp/aswarm-wal",
                           fsync_every_n: int = 1):
    """
    Integrate crash recovery into existing UDP listener
    
    Args:
        udp_listener: FastPathListener instance
        wal_dir: WAL directory path
        fsync_every_n: Fsync batching (1 = every write)
        
    Returns:
        CrashRecoveryManager instance
    """
    # Wrap the original elevation callback
    original_callback = udp_listener.elevation_callback
    
    # Create backpressure callback that degrades to audit-only
    def backpressure_callback(reason: str, stats: Dict[str, Any]):
        if hasattr(udp_listener, 'system_mode'):
            with udp_listener.mode_lock:
                if udp_listener.system_mode.value == 'normal':
                    udp_listener.system_mode = udp_listener.SystemMode.DEGRADED
                    logger.warning(f"System degraded due to {reason}: {stats}")
    
    # Create recovery manager
    recovery = CrashRecoveryManager(
        wal_dir, 
        original_callback,
        backpressure_callback,
        fsync_every_n
    )
    
    def enhanced_callback(elevation_data: Dict[str, Any], source: Tuple[str, int]):
        """Enhanced callback with WAL logging"""
        try:
            # Extract source ID with fallbacks
            src_id = (elevation_data.get('_fastpath', {}).get('src_id')
                     or elevation_data.get('node_id')
                     or elevation_data.get('_fastpath', {}).get('source_ip', 'unknown'))
            
            # Log to WAL first
            entry_id = recovery.log_anomaly(src_id, elevation_data)
            
            # Process elevation
            try:
                original_callback(elevation_data, source)
                
                # Mark as committed if successful
                recovery.commit_anomaly(entry_id)
                
            except Exception as e:
                logger.error(f"Elevation processing failed for {entry_id}: {e}")
                # Entry remains uncommitted for replay
                
        except Exception as e:
            logger.error(f"WAL logging failed: {e}")
            # Fallback to direct processing
            original_callback(elevation_data, source)
    
    # Replace callback
    udp_listener.elevation_callback = enhanced_callback
    
    # Start recovery
    recovery.start()
    
    return recovery

def main():
    """CLI for testing crash recovery"""
    import argparse
    
    parser = argparse.ArgumentParser(description='A-SWARM Crash Recovery Manager')
    parser.add_argument('--wal-dir', default='/tmp/aswarm-wal', help='WAL directory')
    parser.add_argument('--test-write', action='store_true', help='Write test entries')
    parser.add_argument('--test-replay', action='store_true', help='Test replay from WAL')
    parser.add_argument('--cleanup', action='store_true', help='Run cleanup')
    parser.add_argument('--stats', action='store_true', help='Show WAL statistics')
    
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    if args.test_write:
        # Test writing entries
        recovery = CrashRecoveryManager(args.wal_dir, fsync_every_n=3)
        recovery.start()
        
        # Write some test entries
        for i in range(5):
            anomaly = {
                'score': 0.8 + i * 0.05,
                'witness_count': i + 1,
                'selector': f'app=test-{i}',
                'event_type': 'lateral_movement'
            }
            
            entry_id = recovery.log_anomaly(f'test-src-{i}', anomaly)
            
            if i % 2 == 0:  # Commit every other entry
                recovery.commit_anomaly(entry_id)
        
        print(f"Wrote 5 test entries to {args.wal_dir}")
        print(json.dumps(recovery.get_health(), indent=2))
        
    elif args.test_replay:
        # Test replay
        def test_callback(anomaly_data, source):
            print(f"REPLAYED: score={anomaly_data.get('score')} from {source}")
        
        recovery = CrashRecoveryManager(args.wal_dir, test_callback)
        recovery.start()
        
        print(f"Recovery complete. Stats:")
        print(json.dumps(recovery.get_health(), indent=2))
        
    elif args.cleanup:
        # Test cleanup
        recovery = CrashRecoveryManager(args.wal_dir)
        recovery.wal.startup_complete = True  # Skip replay
        recovery.wal.cleanup()
        print("WAL cleanup complete")
        
    elif args.stats:
        # Show statistics
        recovery = CrashRecoveryManager(args.wal_dir)
        recovery.wal.startup_complete = True  # Skip replay
        stats = recovery.get_health()
        print(json.dumps(stats, indent=2))
        
    else:
        print("Use --test-write, --test-replay, --cleanup, or --stats")

if __name__ == '__main__':
    main()