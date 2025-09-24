# A-SWARM Pilot Deployment Guide

## 🎯 **Objective**: Deploy A-SWARM in Under 2 Hours

This package contains everything needed to deploy A-SWARM's autonomous cyber defense system in a pilot environment. The deployment is designed to be **safe-by-default** with autonomous features disabled until explicitly enabled.

---

## 📋 **Prerequisites**

### **Hardware Requirements**
- **Minimum**: 4 vCPU, 8 GB RAM, 50 GB disk
- **Recommended**: 8 vCPU, 16 GB RAM, 100 GB disk
- **Network**: Internet access for initial setup, ports 80/443/3000/8000/9090 available

### **Operating System**
- **Supported**: Ubuntu 22.04+, Debian 11+, RHEL 8+, CentOS 8+
- **Architecture**: x86_64/amd64
- **User**: Non-root user with sudo privileges

### **Software Dependencies**
*(Auto-installed by setup script)*
- Docker 20.10+
- Docker Compose 2.0+
- curl, jq, openssl

---

## 🚀 **Quick Start (Automated)**

### **Option A: One-Line Remote Install**
```bash
# Download and run installer
curl -fsSL https://raw.githubusercontent.com/Connerlevi/A-Swarm/main/customer-deployment/install/install.sh | bash
```

### **Option B: Local Install**
```bash
# Extract deployment package
tar -xzf aswarm-pilot-v1.0.tar.gz
cd aswarm-pilot-v1.0/install

# Run installer
./install.sh
```

### **Expected Output**
```
╔═══════════════════════════════════════════╗
║              A-SWARM PILOT                ║
║         Autonomous Cyber Defense          ║
║          Two-Hour Installation            ║
╚═══════════════════════════════════════════╝

[INFO] Checking Docker installation...
[SUCCESS] Docker 24.0.7 detected
[INFO] Checking Docker Compose installation...
[SUCCESS] Docker Compose 2.21.0 detected
[INFO] Starting A-SWARM services...
[SUCCESS] All services started and healthy

🎉 A-SWARM Pilot deployment complete!

Access URLs:
  🏠 Control Center:    http://localhost
  📊 Grafana Dashboard: http://localhost:3000
  📈 Prometheus:        http://localhost:9090
  🔗 API Endpoint:      http://localhost:8000

Default Credentials:
  Grafana: admin / [generated-password]

⚠️  IMPORTANT SAFETY NOTES:
  • Circuit breaker is ENABLED by default (autonomous evolution disabled)
  • Click 'Enable Autonomy' in Control Center when ready
  • Use 'Emergency Stop' to halt all autonomous operations
```

---

## 🔧 **Manual Installation Steps**

### **Step 1: Environment Preparation**
```bash
# Create deployment directory
mkdir -p /opt/aswarm-pilot
cd /opt/aswarm-pilot

# Copy deployment files
cp -r /path/to/customer-deployment/install/* .

# Verify structure
ls -la
# Expected: docker-compose.yml, .env.template, install.sh, provisioning/, assets/
```

### **Step 2: Configuration**
```bash
# Create environment file
cp .env.template .env

# Customize settings (optional)
nano .env
```

**Key Configuration Options:**
```bash
# Environment
ENV=dev                              # dev|staging|prod
CLUSTER=pilot                        # Unique cluster identifier

# Security (CHANGE IN PRODUCTION)
JWT_SECRET=changeme-generate-random-secret
GRAFANA_PASSWORD=admin
TLS_SELF_SIGNED=true                 # Auto-generate certs for demo

# Autonomy Control (SAFETY FIRST)
EVOLUTION_CIRCUIT_BREAKER=true       # TRUE = evolution disabled
FITNESS_THRESHOLD=0.70               # Minimum fitness for promotion
PROMOTE_MAX_CANARY_PCT=5            # Max % in canary simultaneously

# Observability
LOG_LEVEL=info                       # debug|info|warn|error
PROMETHEUS_RETENTION=15d             # Metrics retention period
```

### **Step 3: Dependency Installation**
```bash
# Install Docker (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker

# Verify installation
docker --version
docker compose version
```

### **Step 4: Service Deployment**
```bash
# Generate secrets (if using defaults)
openssl rand -hex 32  # Use for JWT_SECRET
openssl rand -base64 16  # Use for GRAFANA_PASSWORD

# Start services
docker compose --profile full up -d

# Monitor startup
docker compose ps
docker compose logs -f
```

### **Step 5: Health Verification**
```bash
# API Health Check
curl -f http://localhost:8000/api/health
# Expected: {"status":"healthy","timestamp":"..."}

# Prometheus Health
curl -f http://localhost:9090/-/healthy
# Expected: Prometheus is Healthy.

# Grafana Access
curl -f http://localhost:3000/login
# Expected: 200 OK (login page)

# Metrics Verification
curl -s http://localhost:9090/api/v1/label/__name__/values | jq '.data[] | select(test("^aswarm_"))' | wc -l
# Expected: ≥10 A-SWARM metrics
```

---

## 📊 **Service Overview**

### **Core Services**
| Service | Port | Description | Health Check |
|---------|------|-------------|--------------|
| **API** | 8000 | Main API & Control Center | `/api/health` |
| **Evolution** | 50051 | gRPC evolution engine | TCP check |
| **Federation** | 9443 | Cross-cluster communication | TCP check |
| **UDP Listener** | 8089/udp | Fast-path event ingestion | Process check |

### **Observability Stack**
| Service | Port | Description | Credentials |
|---------|------|-------------|-------------|
| **Grafana** | 3000 | Metrics dashboards | admin / [generated] |
| **Prometheus** | 9090 | Metrics collection | None |
| **NGINX** | 80/443 | Reverse proxy & landing page | None |

### **Data Persistence**
- **Volumes**: `prometheus-data`, `grafana-data`, `api-data`, `evolution-data`, `federation-data`, `events-wal`
- **Backup Command**: `docker run --rm -v aswarm-pilot_prometheus-data:/data -v $(pwd):/backup alpine tar czf /backup/prometheus-backup.tar.gz /data`

---

## 🎮 **Using A-SWARM**

### **Access Control Center**
1. Navigate to **http://localhost** (or server IP)
2. Verify system status shows "Healthy ✅"
3. Confirm circuit breaker shows "TRIPPED (Enabled)" (safe mode)

### **View Metrics Dashboard**
1. Access **http://localhost:3000**
2. Login with `admin / [generated-password]`
3. Navigate to "A-SWARM Pilot Overview" dashboard
4. Monitor system availability, learning events, service health

### **Enable Autonomous Mode** ⚠️
**ONLY AFTER FULL VALIDATION**
1. In Control Center, click **"Enable Autonomy"**
2. Confirm system shows "Circuit Breaker: OK (Disabled)"
3. Monitor Grafana for learning events and evolution cycles
4. **Emergency Stop**: Click "🚨 Emergency Stop" if needed

### **Command Line Operations**
```bash
# Service management
docker compose ps                    # Check status
docker compose logs -f api          # View logs
docker compose restart evolution    # Restart service

# Autonomy control
curl -X POST http://localhost:8000/api/autonomy/enable
curl -X POST http://localhost:8000/api/autonomy/disable

# Metrics queries
curl -s http://localhost:8000/api/metrics | grep aswarm_

# Health monitoring
watch -n 5 'curl -s http://localhost:8000/api/health | jq'
```

---

## 🔍 **Validation Checklist**

### **✅ Pre-Deployment**
- [ ] Server meets minimum requirements (4 vCPU, 8 GB RAM)
- [ ] Required ports available (80, 443, 3000, 8000, 8089, 9090, 9443, 50051)
- [ ] Docker and Docker Compose installed
- [ ] User has sudo privileges and is in docker group

### **✅ Post-Deployment**
- [ ] All 7 services show "Up" status in `docker compose ps`
- [ ] Control Center accessible at http://localhost
- [ ] Grafana dashboard loads with data
- [ ] Prometheus shows ≥10 aswarm_* metrics
- [ ] API health endpoint returns 200 OK
- [ ] Circuit breaker shows "Enabled" (safe mode)

### **✅ Autonomous Operation** (Optional)
- [ ] Enable autonomy via Control Center
- [ ] Learning events appear in metrics (rate > 0)
- [ ] Evolution cycles begin (visible in Grafana)
- [ ] Event queue processing functional
- [ ] Emergency stop responds immediately

### **✅ Production Readiness** (If Applicable)
- [ ] JWT_SECRET and GRAFANA_PASSWORD changed from defaults
- [ ] TLS_SELF_SIGNED=false with real certificates
- [ ] Log levels appropriate for environment
- [ ] Backup procedures tested
- [ ] Monitoring alerts configured

---

## 🚨 **Emergency Procedures**

### **Immediate Stop**
```bash
# Stop all autonomous operations
curl -X POST http://localhost:8000/api/autonomy/disable

# Or via Control Center
# Click "🚨 Emergency Stop" button
```

### **Service Shutdown**
```bash
# Graceful shutdown
docker compose down

# Force stop (if needed)
docker compose kill
docker compose down
```

### **Complete Reset**
```bash
# ⚠️ DESTROYS ALL DATA
docker compose down -v --remove-orphans
docker system prune -f
```

### **Recovery from Issues**
```bash
# View logs
docker compose logs --tail=100 [service]

# Restart specific service
docker compose restart [service]

# Check resource usage
docker stats

# Validate configuration
docker compose config
```

---

## 📞 **Support & Troubleshooting**

### **Common Issues**
1. **Port conflicts**: Use `ss -tlnp | grep :8000` to identify conflicts
2. **Permission denied**: Ensure user is in docker group: `groups $USER`
3. **Out of memory**: Check `docker stats`, increase swap if needed
4. **Slow startup**: Services can take 2-3 minutes to fully initialize

### **Log Locations**
- **Container logs**: `docker compose logs [service]`
- **System logs**: `/var/log/syslog` or `journalctl -u docker`
- **Application logs**: Mounted in service data volumes

### **Diagnostic Commands**
```bash
# Full system health
./validation/health-check.sh

# Performance metrics
./validation/performance-check.sh

# Network connectivity
./validation/network-check.sh

# Generate support bundle
./scripts/collect-support-info.sh
```

### **Getting Help**
- **Documentation**: `docs/` directory
- **Runbooks**: `docs/runbooks/` directory
- **Support**: Include output from `./scripts/collect-support-info.sh`

---

## 🔒 **Security Considerations**

### **Default Security Posture**
- **Circuit breaker ENABLED**: No autonomous operations until explicitly enabled
- **Self-signed certificates**: HTTPS enabled but not production-ready
- **Generated secrets**: JWT and Grafana passwords auto-generated
- **Network isolation**: Services communicate via internal Docker network
- **Non-root containers**: All services run as non-root users

### **Production Hardening** (Required for Production Use)
1. **Replace self-signed certificates** with CA-signed certificates
2. **Change default passwords** and use strong, unique secrets
3. **Enable additional authentication** (LDAP, SAML, etc.)
4. **Configure firewall rules** to restrict access
5. **Enable audit logging** and integrate with SIEM
6. **Set up backup and recovery procedures**
7. **Configure monitoring and alerting**

---

**🛡️ A-SWARM: Autonomous Cyber Defense for Critical Infrastructure**

*For additional support and advanced configurations, refer to the complete documentation in the `docs/` directory.*