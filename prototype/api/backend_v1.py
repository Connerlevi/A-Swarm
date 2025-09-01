#!/usr/bin/env python3
"""
A-SWARM Production API Backend v1 - Real-time FastAPI server for UI
Provides REST API and WebSocket streams for real-time monitoring
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Set, Tuple
from dataclasses import dataclass, asdict
import threading
import queue
from collections import deque
import signal
import sys
import secrets

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Query, Header
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, Response
    from pydantic import BaseModel, Field
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    print("FastAPI not available - install with: pip install fastapi uvicorn websockets")
    sys.exit(1)

try:
    from kubernetes import client, config, watch
    from kubernetes.client import ApiException
    HAS_K8S = True
except ImportError:
    HAS_K8S = False
    logging.warning("Kubernetes client not available - limited functionality")

# Import A-SWARM components
sys.path.append('/app')
try:
    from pheromone.kill_switch_v1 import KillSwitchGovernance, RequestType, ApprovalRole
    from pheromone.crash_recovery_v2 import CrashRecoveryManager
except ImportError as e:
    logging.warning(f"Could not import A-SWARM components: {e}")

logger = logging.getLogger('aswarm.api')

# Event storage and streaming
MAX_EVENTS = 10000  # Keep last 10k events in memory
EVENT_RETENTION_HOURS = 24

from enum import Enum

class Level(str, Enum):
    INFO = "info"
    WARNING = "warning" 
    ERROR = "error"
    CRITICAL = "critical"

class Source(str, Enum):
    FASTPATH = "fastpath"
    WAL = "wal"
    KILLSWITCH = "killswitch"
    SYSTEM = "system"

@dataclass 
class Event:
    """Real-time event for UI streaming"""
    id: str
    timestamp: str
    level: Level
    source: Source
    message: str
    metadata: Dict[str, Any]

class EventStore:
    """Thread-safe event storage with streaming support"""
    
    def __init__(self, max_events: int = MAX_EVENTS):
        self.max_events = max_events
        self.events: deque[Event] = deque(maxlen=max_events)
        self.lock = threading.Lock()
        self.subscribers: Set[Tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self.subscriber_lock = threading.Lock()
        
    def add_event(self, event: Event):
        """Add event to store and notify subscribers"""
        with self.lock:
            self.events.append(event)
        
        # Thread-safe fan-out to WebSocket queues
        with self.subscriber_lock:
            for loop, q in list(self.subscribers):
                try:
                    if q.full():
                        try:
                            q.get_nowait()  # Drop oldest to make room
                        except asyncio.QueueEmpty:
                            pass
                    loop.call_soon_threadsafe(q.put_nowait, event)
                except Exception:
                    self.subscribers.discard((loop, q))
    
    def get_events(self, limit: Optional[int] = None, 
                  since: Optional[str] = None,
                  level_filter: Optional[str] = None,
                  source_filter: Optional[str] = None) -> List[Event]:
        """Get events with optional filtering"""
        with self.lock:
            events = list(self.events)
        
        # Apply filters
        if since:
            try:
                since_dt = datetime.fromisoformat(since)
                events = [e for e in events 
                         if datetime.fromisoformat(e.timestamp) >= since_dt]
            except ValueError:
                pass
        
        if level_filter:
            events = [e for e in events if e.level == level_filter]
            
        if source_filter:
            events = [e for e in events if e.source == source_filter]
        
        # Apply limit
        if limit:
            events = events[-limit:]
        
        return events
    
    def subscribe(self, queue_ref: asyncio.Queue):
        """Subscribe to real-time events"""
        loop = asyncio.get_running_loop()
        with self.subscriber_lock:
            self.subscribers.add((loop, queue_ref))
    
    def unsubscribe(self, queue_ref: asyncio.Queue):
        """Unsubscribe from events"""
        with self.subscriber_lock:
            self.subscribers = {t for t in self.subscribers if t[1] is not queue_ref}
    
    def prune_old(self):
        """Remove events older than retention period"""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=EVENT_RETENTION_HOURS)
        with self.lock:
            while self.events and datetime.fromisoformat(self.events[0].timestamp) < cutoff:
                self.events.popleft()

# Global event store
event_store = EventStore()

# API Key authentication
API_KEY = os.environ.get("ASWARM_API_KEY")

async def require_api_key(x_api_key: str = Header(None)):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

# Pydantic models for API
class KillSwitchRequestModel(BaseModel):
    request_type: str = Field(pattern="^(disable_fastpath|enable_audit_only|emergency_shutdown|restore_normal|reload_keys|flush_wal)$")
    requester: str
    reason: str
    metadata: Optional[Dict[str, Any]] = None

class ApprovalModel(BaseModel):
    approver: str
    role: str

class SystemStatsModel(BaseModel):
    """System statistics model"""
    timestamp: str
    pheromone_stats: Dict[str, Any]
    wal_stats: Optional[Dict[str, Any]]
    kill_switch_pending: int
    system_mode: str

# FastAPI app
app = FastAPI(
    title="A-SWARM Monitoring API",
    description="Real-time monitoring and control API for A-SWARM",
    version="1.0.0"
)

# CORS configuration from environment
origins = os.environ.get("ASWARM_UI_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global components (initialized in startup)
kill_switch_governance: Optional[KillSwitchGovernance] = None
crash_recovery: Optional[CrashRecoveryManager] = None
system_stats_cache = {}
stats_lock = threading.Lock()
shutdown_event = asyncio.Event()

@app.on_event("startup")
async def startup():
    """Initialize components on startup"""
    global kill_switch_governance, crash_recovery, shutdown_event
    shutdown_event = asyncio.Event()
    
    namespace = os.environ.get('KUBERNETES_NAMESPACE', 'aswarm')
    
    # Initialize kill-switch governance
    try:
        kill_switch_governance = KillSwitchGovernance(
            namespace=namespace,
            action_callback=_handle_kill_switch_action
        )
        kill_switch_governance.start()
        logger.info("Kill-switch governance initialized")
    except Exception as e:
        logger.error(f"Failed to initialize kill-switch: {e}")
    
    # Initialize crash recovery (read-only for API)
    try:
        wal_dir = os.environ.get('ASWARM_WAL_DIR', '/tmp/aswarm-wal')
        crash_recovery = CrashRecoveryManager(wal_dir)
        logger.info("Crash recovery manager initialized")
    except Exception as e:
        logger.error(f"Failed to initialize crash recovery: {e}")
    
    # Start stats collection
    asyncio.create_task(_stats_collector())
    
    logger.info("A-SWARM API backend started")

@app.on_event("shutdown")
async def on_shutdown():
    """Graceful shutdown"""
    shutdown_event.set()
    if kill_switch_governance:
        kill_switch_governance.stop()

def _handle_kill_switch_action(request_type: RequestType, request):
    """Handle approved kill-switch actions"""
    # Log the action
    event = Event(
        id=f"ks-{int(time.time())}-{request.request_id[:8]}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        level=Level.CRITICAL,
        source=Source.KILLSWITCH,
        message=f"Kill-switch executed: {request_type.value}",
        metadata={
            "request_id": request.request_id,
            "requester": request.requester,
            "approvals": list(request.approvals.keys())
        }
    )
    event_store.add_event(event)

async def _stats_collector():
    """Background task to collect system stats"""
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            # Collect stats every 5 seconds
            try:
                # Resilient WAL stats
                wal_stats = None
                if crash_recovery and hasattr(crash_recovery, "get_health"):
                    wal_stats = crash_recovery.get_health()
                
                stats = SystemStatsModel(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    pheromone_stats={},  # TODO: Connect to actual pheromone stats
                    wal_stats=wal_stats,
                    kill_switch_pending=len(kill_switch_governance.get_pending_requests()) if kill_switch_governance else 0,
                    system_mode="normal"  # TODO: Connect to actual system mode
                )
                
                with stats_lock:
                    system_stats_cache['latest'] = stats
                
                # Prune old events
                event_store.prune_old()
                
            except Exception as e:
                logger.error(f"Stats collection error: {e}")

# REST API Endpoints

@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/api/v1/events")
async def get_events(
    limit: Optional[int] = Query(100, le=1000),
    since: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    source: Optional[str] = Query(None)
):
    """Get historical events with filtering"""
    events = event_store.get_events(
        limit=limit,
        since=since,
        level_filter=level,
        source_filter=source
    )
    
    return {
        "events": [asdict(e) for e in events],
        "total": len(events),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/api/v1/stats")
async def get_system_stats():
    """Get current system statistics"""
    with stats_lock:
        stats = system_stats_cache.get('latest')
    
    if not stats:
        raise HTTPException(status_code=503, detail="Stats not available")
    
    return asdict(stats)

@app.get("/api/v1/kill-switch/requests", dependencies=[Depends(require_api_key)])
async def get_kill_switch_requests():
    """Get pending kill-switch requests"""
    if not kill_switch_governance:
        raise HTTPException(status_code=503, detail="Kill-switch governance not available")
    
    requests = kill_switch_governance.get_pending_requests()
    return {"requests": requests}

@app.post("/api/v1/kill-switch/requests", dependencies=[Depends(require_api_key)])
async def create_kill_switch_request(request: KillSwitchRequestModel):
    """Create a new kill-switch request"""
    if not kill_switch_governance:
        raise HTTPException(status_code=503, detail="Kill-switch governance not available")
    
    try:
        request_type = RequestType(request.request_type)
        request_id = kill_switch_governance.create_request(
            request_type=request_type,
            requester=request.requester,
            reason=request.reason,
            metadata=request.metadata
        )
        
        # Create event
        event = Event(
            id=f"ks-create-{int(time.time())}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=Level.WARNING,
            source=Source.KILLSWITCH,
            message=f"Kill-switch request created: {request_type.value}",
            metadata={
                "request_id": request_id,
                "requester": request.requester,
                "reason": request.reason
            }
        )
        event_store.add_event(event)
        
        return {"request_id": request_id, "status": "created"}
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create request: {e}")

@app.post("/api/v1/kill-switch/requests/{request_id}/approve", dependencies=[Depends(require_api_key)])
async def approve_kill_switch_request(request_id: str, approval: ApprovalModel):
    """Approve a kill-switch request"""
    if not kill_switch_governance:
        raise HTTPException(status_code=503, detail="Kill-switch governance not available")
    
    try:
        role = ApprovalRole(approval.role)
        success = kill_switch_governance.approve_request(
            request_id=request_id,
            approver=approval.approver,
            role=role
        )
        
        if not success:
            raise HTTPException(status_code=400, detail="Approval failed")
        
        # Create event
        event = Event(
            id=f"ks-approve-{int(time.time())}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=Level.WARNING,
            source=Source.KILLSWITCH,
            message=f"Kill-switch approved by {approval.role}",
            metadata={
                "request_id": request_id,
                "approver": approval.approver,
                "role": approval.role
            }
        )
        event_store.add_event(event)
        
        return {"status": "approved"}
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to approve: {e}")

def _csv_safe(s: str) -> str:
    """Prevent CSV injection by escaping dangerous characters"""
    if s and s[0] in ("=", "+", "-", "@"):
        return "'" + s
    return s

@app.get("/api/v1/events/export")
async def export_events_csv(
    since: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    source: Optional[str] = Query(None)
):
    """Export events as CSV"""
    events = event_store.get_events(
        since=since,
        level_filter=level, 
        source_filter=source
    )
    
    import csv
    import io
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['timestamp', 'level', 'source', 'message', 'metadata'])
    
    # Write events with CSV injection protection
    for event in events:
        writer.writerow([
            _csv_safe(event.timestamp),
            _csv_safe(event.level),
            _csv_safe(event.source),
            _csv_safe(event.message),
            _csv_safe(json.dumps(event.metadata))
        ])
    
    csv_content = output.getvalue()
    output.close()
    
    return Response(
        content=csv_content,
        media_type='text/csv',
        headers={"Content-Disposition": "attachment; filename=aswarm-events.csv"}
    )

# WebSocket endpoint for real-time streaming
@app.websocket("/api/v1/events/stream")
async def websocket_events(websocket: WebSocket):
    """WebSocket endpoint for real-time event streaming"""
    await websocket.accept()
    
    # Create queue for this client
    client_queue = asyncio.Queue(maxsize=100)
    event_store.subscribe(client_queue)
    
    try:
        # Send recent events on connect
        recent_events = event_store.get_events(limit=50)
        for event in recent_events:
            await websocket.send_json({
                "type": "event",
                "data": asdict(event)
            })
        
        # Send initial stats
        with stats_lock:
            stats = system_stats_cache.get('latest')
        if stats:
            await websocket.send_json({
                "type": "stats",
                "data": asdict(stats)
            })
        
        # Stream real-time events
        while True:
            try:
                # Get event from queue with timeout
                event = await asyncio.wait_for(client_queue.get(), timeout=30.0)
                await websocket.send_json({
                    "type": "event", 
                    "data": asdict(event)
                })
            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        event_store.unsubscribe(client_queue)

@app.websocket("/api/v1/stats/stream")
async def websocket_stats(websocket: WebSocket):
    """WebSocket endpoint for real-time stats streaming"""
    await websocket.accept()
    
    try:
        while True:
            # Send current stats every 2 seconds
            with stats_lock:
                stats = system_stats_cache.get('latest')
            
            if stats:
                await websocket.send_json(asdict(stats))
            
            await asyncio.sleep(2)
            
    except WebSocketDisconnect:
        logger.debug("Stats WebSocket client disconnected")
    except Exception as e:
        logger.error(f"Stats WebSocket error: {e}")

# Metrics collection integration
class ElevationCallbackBridge:
    """Bridge elevation callbacks to API event store"""
    
    def __init__(self, event_store: EventStore):
        self.event_store = event_store
    
    def __call__(self, elevation_data: Dict[str, Any], source: tuple):
        """Handle elevation from UDP listener"""
        # Extract key metrics
        anomaly = elevation_data.get('anomaly', {})
        score = anomaly.get('score', 0)
        fastpath_meta = elevation_data.get('_fastpath', {})
        
        # Determine severity
        if score >= 0.95:
            level = Level.CRITICAL
        elif score >= 0.8:
            level = Level.ERROR
        elif score >= 0.6:
            level = Level.WARNING
        else:
            level = Level.INFO
        
        # Create event
        event = Event(
            id=f"elev-{int(time.time() * 1000)}-{secrets.token_hex(4)}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=level,
            source=Source.FASTPATH,
            message=f"Anomaly detected: score={score:.3f}",
            metadata={
                "score": score,
                "witness_count": anomaly.get('witness_count', 0),
                "selector": anomaly.get('selector', ''),
                "source_ip": source[0],
                "src_id": fastpath_meta.get('src_id', ''),
                "seq16": fastpath_meta.get('seq16', 0),
                "node_id": elevation_data.get('node_id', 'unknown')
            }
        )
        
        self.event_store.add_event(event)

def _handle_kill_switch_action(request_type: RequestType, request):
    """Handle approved kill-switch actions"""
    # Log the action
    event = Event(
        id=f"ks-{int(time.time())}-{request.request_id[:8]}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        level=Level.CRITICAL,
        source=Source.KILLSWITCH,
        message=f"Kill-switch executed: {request_type.value}",
        metadata={
            "request_id": request.request_id,
            "requester": request.requester,
            "approvals": list(request.approvals.keys())
        }
    )
    event_store.add_event(event)

async def _stats_collector():
    """Background task to collect system stats"""
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            # Collect stats every 5 seconds
            try:
                # Resilient WAL stats
                wal_stats = None
                if crash_recovery and hasattr(crash_recovery, "get_health"):
                    wal_stats = crash_recovery.get_health()
                
                stats = SystemStatsModel(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    pheromone_stats={},  # TODO: Connect to actual pheromone stats
                    wal_stats=wal_stats,
                    kill_switch_pending=len(kill_switch_governance.get_pending_requests()) if kill_switch_governance else 0,
                    system_mode="normal"  # TODO: Connect to actual system mode
                )
                
                with stats_lock:
                    system_stats_cache['latest'] = stats
                
                # Prune old events
                event_store.prune_old()
                
            except Exception as e:
                logger.error(f"Stats collection error: {e}")

def create_test_events():
    """Create some test events for UI development"""
    test_events = [
        Event(
            id=f"test-{i}",
            timestamp=(datetime.now(timezone.utc) - timedelta(minutes=i)).isoformat(),
            level=[Level.INFO, Level.WARNING, Level.ERROR, Level.CRITICAL][i % 4],
            source=[Source.FASTPATH, Source.WAL, Source.KILLSWITCH, Source.SYSTEM][i % 4],
            message=f"Test event {i}",
            metadata={"test": True, "sequence": i}
        )
        for i in range(20)
    ]
    
    for event in reversed(test_events):  # Add in chronological order
        event_store.add_event(event)

# Production server setup
def create_app() -> FastAPI:
    """Create configured FastAPI app"""
    return app

def run_server(host: str = "0.0.0.0", port: int = 8000, debug: bool = False):
    """Run the API server"""
    # Add test events if in debug mode
    if debug:
        create_test_events()
        logger.info("Added test events for development")
    
    # Configure uvicorn with WebSocket tuning
    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info" if not debug else "debug",
        access_log=False,  # Disable access logs for performance
        workers=1,  # Single worker to avoid event store fragmentation
        ws_ping_interval=20,
        ws_ping_timeout=20,
        ws_max_size=1_048_576  # 1 MiB
    )
    
    server = uvicorn.Server(config)
    
    # Graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Shutting down API server...")
        if kill_switch_governance:
            kill_switch_governance.stop()
        server.should_exit = True
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info(f"Starting A-SWARM API server on {host}:{port}")
    server.run()

def main():
    """CLI entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='A-SWARM Production API Backend')
    parser.add_argument('--host', default='0.0.0.0', help='Bind host')
    parser.add_argument('--port', type=int, default=8000, help='HTTP port')
    parser.add_argument('--debug', action='store_true', help='Debug mode with test data')
    parser.add_argument('--namespace', default='aswarm', help='Kubernetes namespace')
    
    args = parser.parse_args()
    
    # Set namespace for components
    os.environ.setdefault('KUBERNETES_NAMESPACE', args.namespace)
    
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    
    # Run server
    run_server(host=args.host, port=args.port, debug=args.debug)

if __name__ == '__main__':
    main()