# Mission Control Integration Guide

## Quick Start Integration (30 minutes)

### 1. Backend WebSocket Enhancement
Update `api/backend_v1.py` to serve the React app and handle the expected message format:

```python
# Add to backend_v1.py
@app.get("/")
async def serve_dashboard():
    """Serve the React dashboard"""
    return FileResponse('dashboard/build/index.html')

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    # Send initial state
    await websocket.send_json({
        "type": "fleet_status",
        "total": len(fleet_nodes),
        "healthy": healthy_count,
        "avg_cpu": avg_cpu,
        "avg_memory": avg_memory,
        "nodes": [{"name": n.name, "status": n.status, "cpu": n.cpu, "memory": n.memory} for n in nodes]
    })
    
    # Subscribe to event stream
    event_queue = asyncio.Queue()
    event_store.subscribe(event_queue)
    
    try:
        while True:
            # Forward events from A-SWARM components
            event = await event_queue.get()
            
            # Transform to expected format
            if event.source == Source.FASTPATH:
                msg = {
                    "type": "event",
                    "level": event.level.value,
                    "message": event.message,
                    "metadata": event.metadata
                }
            elif event.source == "episode":
                msg = {
                    "type": "episode",
                    "id": event.metadata.get("id"),
                    "attacklet_name": event.metadata.get("name"),
                    "status": event.metadata.get("status"),
                    "ttd_seconds": event.metadata.get("ttd"),
                    "score": event.metadata.get("score")
                }
            
            await websocket.send_json(msg)
    except WebSocketDisconnect:
        event_store.unsubscribe(event_queue)
```

### 2. Build Setup

```bash
# Install dependencies
cd dashboard
npm install react react-dom @types/react
npm install framer-motion lucide-react recharts
npm install -D @vitejs/plugin-react vite tailwindcss

# Create vite config
cat > vite.config.ts << 'EOF'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true
      }
    }
  }
})
EOF

# Build for production
npm run build
```

### 3. Docker Integration

```dockerfile
# Add to Dockerfile.production
FROM node:18-alpine as ui-builder
WORKDIR /app/dashboard
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM python:3.11-slim
# ... existing Python setup ...
COPY --from=ui-builder /app/dashboard/dist /app/dashboard/dist
```

### 4. Kubernetes Deployment

```yaml
# Update blue-api-server.yaml
spec:
  containers:
  - name: api-server
    env:
    - name: VITE_ASWARM_WS_URL
      value: "ws://aswarm-api.aswarm.svc.cluster.local:8000/ws"
    volumeMounts:
    - name: dashboard
      mountPath: /app/dashboard/dist
  volumes:
  - name: dashboard
    configMap:
      name: dashboard-build
```

## Real-time Data Flow

### Episode Updates from Red/Blue Harness
```python
# In redswarm/harness_v2_secure.py
async def broadcast_episode(episode: EpisodeResult):
    """Send episode to Mission Control"""
    await event_store.add_event(Event(
        id=str(uuid4()),
        timestamp=datetime.utcnow().isoformat(),
        level=Level.INFO,
        source="episode",
        message=f"Episode {episode.episode_id}: {episode.status}",
        metadata=asdict(episode)
    ))
```

### Fleet Status from Sentinels
```python
# In sentinel/telemetry_v2.py
def report_health(self):
    """Report sentinel health to Pheromone"""
    health = {
        "name": self.node_name,
        "status": "healthy" if self.healthy else "degraded",
        "cpu": psutil.cpu_percent(),
        "memory": psutil.Process().memory_info().rss / 1024 / 1024
    }
    # Send via UDP fast-path
    self.fastpath_sender.send_health(health)
```

### Kill Switch Integration
```python
# In pheromone/kill_switch_v1.py
def get_status(self):
    """Get kill switch status for UI"""
    return {
        "engaged": self.is_engaged(),
        "approvals": [
            self.has_approval(ApprovalRole.OPERATOR_1),
            self.has_approval(ApprovalRole.OPERATOR_2)
        ],
        "ttl": self.remaining_ttl()
    }
```

## Testing the Integration

### 1. Local Development
```bash
# Terminal 1: Backend
cd prototype
python api/backend_v1.py

# Terminal 2: Frontend dev server
cd dashboard
npm run dev

# Terminal 3: Simulate events
python scripts/simulate_events.py
```

### 2. Kubernetes Testing
```bash
# Deploy full stack
kubectl apply -f deploy/blue-api-production.yaml

# Port-forward for access
kubectl port-forward svc/aswarm-api 8000:8000

# Access dashboard
open http://localhost:8000
```

## Message Protocol Reference

### Event Types
```typescript
interface WSMessage {
  type: "event" | "episode" | "fleet_status" | "rules_update" | "kill_switch"
  // Type-specific fields...
}
```

### Fleet Status
```json
{
  "type": "fleet_status",
  "total": 12,
  "healthy": 11,
  "avg_cpu": 0.8,
  "avg_memory": 18,
  "nodes": [
    {"name": "node-1", "status": "healthy", "cpu": 0.7, "memory": 17}
  ]
}
```

### Episode Update
```json
{
  "type": "episode",
  "id": "ep-001",
  "attacklet_name": "privilege-escalation-v1",
  "status": "detected",
  "ttd_seconds": 0.12,
  "score": 95,
  "technique": "T1068"
}
```

## Production Deployment Checklist

- [ ] Build React app with production optimizations
- [ ] Update backend to serve static files
- [ ] Configure WebSocket URL for cluster DNS
- [ ] Add CORS headers for cross-origin requests
- [ ] Set up Ingress for external access
- [ ] Add authentication middleware
- [ ] Configure TLS for WebSocket
- [ ] Test real-time updates end-to-end
- [ ] Add health checks for UI availability
- [ ] Set up monitoring for WebSocket connections