# A-SWARM Troubleshooting Guide

## Quick Diagnostics

### 🔍 **First Steps for Any Issue**

```bash
# 1. Check overall status
make status

# 2. Run health check
make health

# 3. Check logs for errors
make logs | grep -i error

# 4. Collect support bundle
make collect-support
```

---

## Common Issues & Solutions

### 🔴 **Services Won't Start**

#### **Symptom**: `docker compose up` fails or containers exit immediately

**Diagnosis**:
```bash
# Check container status
docker compose ps -a

# Check specific service logs
docker compose logs [service-name]

# Verify port availability
ss -tlnp | grep -E ":(8000|3000|9090|9443|50051)"
```

**Solutions**:

1. **Port conflicts**:
   - Stop conflicting services
   - Or modify ports in `.env` file

2. **Resource limits**:
   ```bash
   # Check available resources
   free -h
   df -h /var/lib/docker

   # Increase Docker resources if needed
   ```

3. **Docker daemon issues**:
   ```bash
   # Restart Docker
   sudo systemctl restart docker
   # Or on Mac/Windows: restart Docker Desktop
   ```

---

### 🟡 **API Not Responding**

#### **Symptom**: Health check fails, API returns connection refused

**Diagnosis**:
```bash
# Test API directly
curl -v http://localhost:8000/api/health

# Check API container
docker compose logs api | tail -50

# Verify networking
docker compose exec api ping evolution
```

**Solutions**:

1. **Container not ready**:
   - Wait 30-60 seconds after startup
   - Check health status: `docker compose ps`

2. **Configuration issues**:
   - Verify JWT_SECRET is set in `.env`
   - Check EVOLUTION_SERVER and FEDERATION_SERVER addresses

3. **Restart API service**:
   ```bash
   docker compose restart api
   ```

---

### 🟠 **Prometheus/Grafana Issues**

#### **Symptom**: No metrics, dashboards empty, scrape failures

**Diagnosis**:
```bash
# Check Prometheus targets
curl http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job:.job, health:.health}'

# Verify metrics are being exposed
curl http://localhost:9000/metrics | grep aswarm_

# Check Grafana datasource
curl -u admin:admin http://localhost:3000/api/datasources
```

**Solutions**:

1. **Prometheus can't scrape targets**:
   - Verify services expose metrics on port 9000
   - Check network connectivity between containers
   - Restart Prometheus: `docker compose restart prometheus`

2. **Grafana dashboard empty**:
   - Import dashboards: `make grafana-import`
   - Verify Prometheus datasource is configured
   - Check time range (last 30m vs last 24h)

3. **Metrics missing**:
   - Services need 2-3 minutes to start exporting
   - Enable autonomy to generate events: `make enable-autonomy`
   - Send test traffic to generate metrics

---

### 🔵 **Evolution Not Working**

#### **Symptom**: No learning events, evolution cycles stuck at 0

**Diagnosis**:
```bash
# Check circuit breaker status
curl http://localhost:9090/api/v1/query?query=aswarm_evolution_circuit_breaker_active | jq

# Verify gRPC connectivity
docker compose exec api nc -zv evolution 50051

# Check evolution logs
docker compose logs evolution | grep -i "error\|warn"
```

**Solutions**:

1. **Circuit breaker enabled** (default):
   ```bash
   # Enable autonomous mode
   make enable-autonomy
   ```

2. **gRPC connection issues**:
   - Restart evolution service: `docker compose restart evolution`
   - Check firewall rules for port 50051

3. **No events to process**:
   - Generate test events via seed-traffic
   - Lower LEARNING_THRESHOLD in `.env`

---

### ⚫ **High Resource Usage**

#### **Symptom**: System slow, containers using excessive CPU/memory

**Diagnosis**:
```bash
# Check resource usage
docker stats --no-stream

# Find memory leaks
docker compose exec [service] cat /proc/1/status | grep -E "VmRSS|VmSwap"

# Check event queue size
curl http://localhost:9090/api/v1/query?query=aswarm_event_queue_size
```

**Solutions**:

1. **Reduce load**:
   - Disable autonomy temporarily: `make disable-autonomy`
   - Increase PROMETHEUS_SCRAPE_INTERVAL in `.env`
   - Reduce EVENT_QUEUE_MAX_SIZE

2. **Resource limits**:
   ```yaml
   # Add to docker-compose.yml services:
   deploy:
     resources:
       limits:
         cpus: '1'
         memory: 512M
   ```

3. **Restart services**:
   ```bash
   make restart
   ```

---

## Advanced Troubleshooting

### 📊 **Performance Issues**

Run performance diagnostics:
```bash
# Full performance check
make perf

# Check specific metrics
curl http://localhost:9090/api/v1/query?query=histogram_quantile\(0.95,aswarm_event_queue_age_seconds_bucket\)
```

### 🔐 **Security/Auth Issues**

Check security posture:
```bash
# Verify circuit breaker
curl http://localhost:8000/api/autonomy/status

# Check certificates
docker compose exec api ls -la /certs/

# Verify JWT token (if using auth)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/health
```

### 🌐 **Network Connectivity**

Test inter-service communication:
```bash
# From API to Evolution
docker compose exec api nc -zv evolution 50051

# From API to Federation
docker compose exec api nc -zv federation 9443

# DNS resolution
docker compose exec api nslookup evolution
```

---

## Recovery Procedures

### 🔄 **Soft Reset** (Preserve Data)
```bash
# Stop services
make down

# Clear temporary issues
docker system prune -f

# Restart
make up
make health
```

### 💣 **Hard Reset** (Delete Everything)
```bash
# WARNING: Destroys all data
make reset

# Fresh install
make install
```

### 📦 **Collect Diagnostics for Support**
```bash
# Create support bundle
make collect-support

# The bundle includes:
# - Container status
# - Recent logs (last 500 lines)
# - Sanitized configuration
# - Prometheus targets
# - API health status

# Send the generated .tar.gz file for support
```

---

## Health Check Error Codes

| Error | Meaning | Solution |
|-------|---------|----------|
| `Docker not found` | Docker not installed | Run installer or install Docker manually |
| `Port already in use` | Service conflict | Stop conflicting service or change ports |
| `No healthy targets` | Services not running | Check logs, restart services |
| `Circuit breaker ENABLED` | Safe mode active | Normal - enable when ready |
| `Insufficient metrics` | Services just started | Wait 2-3 minutes |
| `High CPU usage` | Resource exhaustion | Check load, add resources |

---

## Getting Help

1. **Run diagnostics**:
   ```bash
   make health
   make perf
   ```

2. **Collect information**:
   ```bash
   make collect-support
   ```

3. **Check documentation**:
   - `README.md` - Deployment guide
   - `docs/TROUBLESHOOTING.md` - This document
   - `validation/*.sh` - Health check scripts

4. **Support channels**:
   - Include support bundle when reporting issues
   - Provide output from `make status` and `make health`
   - Describe what changed before the issue started

---

## Prevention Tips

✅ **Before deployment**:
- Verify prerequisites: 4 CPU, 8GB RAM, 50GB disk
- Check port availability
- Review `.env` configuration

✅ **During operation**:
- Monitor dashboards regularly
- Keep circuit breaker enabled until validated
- Set up resource alerts
- Regular health checks: `make health`

✅ **Maintenance**:
- Weekly restart: `make restart`
- Monitor disk usage
- Review logs for warnings
- Keep support bundles for comparison