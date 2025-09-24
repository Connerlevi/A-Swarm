# A-SWARM Mission Control Dashboard

Production-ready React dashboard for real-time monitoring and control of A-SWARM autonomous defense systems.

## Features

✅ **Real-time WebSocket Integration** - Live updates from A-SWARM backend
✅ **Kill Switch Governance** - Two-person approval with TTL auto-revert
✅ **Episode Timeline** - Visual attack/defense correlation with status history
✅ **Fleet Monitoring** - Sentinel health across 1000+ nodes
✅ **Detection Rules** - Hot-reloadable content packs with MITRE ATT&CK mapping
✅ **Event Stream** - Real-time telemetry with filtering
✅ **Production Security** - Token auth, origin validation, error boundaries
✅ **Dark Theme** - Professional security operations aesthetic

## Quick Start

### Development Mode (Both Services)
```bash
# From A-SWARM prototype root
./start-mission-control.sh
```

This starts both backend (port 8000) and frontend (port 3000) with hot reload.

### Dashboard Only
```bash
cd dashboard
./setup.sh           # One-time setup
npm run dev          # Development server
npm run build        # Production build
```

## Configuration

### Environment Variables (.env)
```bash
# WebSocket connection
VITE_ASWARM_WS_URL=ws://localhost:8000/ws

# Authentication
VITE_ASWARM_TOKEN=your_jwt_token
VITE_ASWARM_AUTH_MODE=query  # or "protocol"
```

### Authentication Modes

**Query String (Recommended)**
```javascript
// .env
VITE_ASWARM_AUTH_MODE=query
// Connects to: ws://host/ws?access_token=token
```

**WebSocket Subprotocols**
```javascript
// .env  
VITE_ASWARM_AUTH_MODE=protocol
// Uses: Sec-WebSocket-Protocol: bearer, token
```

## Backend Integration

### Expected WebSocket Messages

**Fleet Status**
```json
{
  "type": "fleet_status",
  "total": 12,
  "healthy": 11,
  "avg_cpu": 0.8,
  "avg_memory": 18,
  "nodes": [{"name": "node-1", "status": "healthy", "cpu": 0.7, "memory": 17}]
}
```

**Episode Updates**
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

**Kill Switch Status**
```json
{
  "type": "kill_switch",
  "engaged": false,
  "approvals": [false, true],
  "ttl": 300
}
```

**Events**
```json
{
  "type": "event",
  "level": "warning", 
  "message": "Elevated threat detected on node-3"
}
```

**Detection Rules**
```json
{
  "type": "rules_update",
  "rules": [{
    "id": "r1",
    "name": "Privilege Escalation", 
    "severity": "critical",
    "technique": "T1068",
    "threshold": 0.95,
    "enabled": true,
    "version": "1.2.0"
  }]
}
```

### Commands to Backend
```json
{"command": "reload_rules"}
{"command": "engage_kill_switch"}
{"command": "set_approval", "slot": 0, "value": true}
{"command": "update_rule", "rule_id": "r1", "enabled": false}
```

## Production Deployment

### Build
```bash
npm run build    # Creates dist/ folder
```

### Serve with Backend
The backend automatically serves the built dashboard:
```python
# Backend serves dashboard at /
# Static assets at /assets/*
# API at /api/*
# WebSocket at /ws
```

### Security Checklist
- [ ] Set `ASWARM_ALLOWED_ORIGINS` (no wildcards)
- [ ] Use HTTPS/WSS in production
- [ ] Configure JWT validation (`ASWARM_JWT_HS256_SECRET`)
- [ ] Set CSP headers via reverse proxy
- [ ] Enable HSTS and security headers
- [ ] Validate all WebSocket origins

## UI Components

Built with:
- **React 18** + TypeScript
- **Tailwind CSS** for styling  
- **Radix UI** for accessible components
- **Lucide React** for icons
- **Framer Motion** for animations
- **Recharts** for data visualization

## Development

### File Structure
```
dashboard/
├── src/
│   ├── components/
│   │   ├── MissionControl.tsx    # Main dashboard component
│   │   └── ui/                   # Reusable UI components
│   ├── lib/utils.ts              # Utility functions
│   ├── App.tsx                   # App wrapper
│   └── main.tsx                  # Entry point
├── public/                       # Static assets
├── dist/                         # Build output
└── package.json                  # Dependencies
```

### Available Scripts
- `npm run dev` - Development server (port 3000)
- `npm run build` - Production build
- `npm run preview` - Preview production build
- `npm run typecheck` - Type checking
- `npm run lint` - ESLint

### Adding Features

1. **New WebSocket Message Type**
   - Add to `handleMsg()` switch statement
   - Update state management
   - Add UI components as needed

2. **New Dashboard Panel**
   - Create component in `components/`
   - Add to main grid layout
   - Wire to WebSocket data

3. **New API Integration**
   - Add REST endpoint calls
   - Update TypeScript interfaces
   - Handle loading/error states

## Troubleshooting

### WebSocket Connection Issues
```bash
# Check backend is running
curl http://localhost:8000/api/health

# Check WebSocket endpoint
wscat -c ws://localhost:8000/ws?access_token=dev123
```

### Build Issues
```bash
# Clear cache and reinstall
rm -rf node_modules package-lock.json
npm install

# Check for TypeScript errors
npm run typecheck
```

### Authentication Issues
- Verify `ASWARM_BEARER` set in backend
- Check browser dev tools for WebSocket errors
- Confirm token format matches backend expectation

## Integration with A-SWARM Components

This dashboard integrates with:
- **Sentinel Agents** - Fleet status and telemetry
- **Pheromone Service** - Event aggregation and WebSocket serving  
- **Red/Blue Harness** - Episode tracking and scoring
- **MicroAct** - Containment action execution
- **Kill Switch** - Emergency halt governance

See the main A-SWARM documentation for component integration details.