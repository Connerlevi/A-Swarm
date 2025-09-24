#!/bin/bash
set -euo pipefail

# A-SWARM Pilot Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/.../install.sh | bash
# Or: ./install.sh

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_NAME="A-SWARM Pilot"
readonly MIN_DOCKER_VERSION="20.10"
readonly MIN_COMPOSE_VERSION="2.0"

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Banner
print_banner() {
    echo -e "${BLUE}"
    cat << 'EOF'
    ╔═══════════════════════════════════════════╗
    ║              A-SWARM PILOT                ║
    ║         Autonomous Cyber Defense          ║
    ║          Two-Hour Installation            ║
    ╚═══════════════════════════════════════════╝
EOF
    echo -e "${NC}"
}

# Dependency checks
check_command() {
    if ! command -v "$1" &> /dev/null; then
        return 1
    fi
    return 0
}

check_dependencies() {
    log_info "Checking required dependencies..."

    local need_bins=(curl jq openssl)
    local missing=0

    for bin in "${need_bins[@]}"; do
        if ! check_command "$bin"; then
            log_error "Missing required command: $bin"
            missing=1
        fi
    done

    if [[ $missing -eq 1 ]]; then
        log_error "Installing missing dependencies..."
        install_dependencies
    fi

    log_success "All dependencies available"
}

install_dependencies() {
    # Detect OS
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        OS=$ID
    else
        log_error "Cannot detect OS. Please install curl, jq, and openssl manually."
        exit 1
    fi

    case $OS in
        ubuntu|debian)
            sudo apt-get update
            sudo apt-get install -y curl jq openssl
            ;;
        centos|rhel|fedora)
            sudo yum install -y curl jq openssl
            ;;
        *)
            log_error "Unsupported OS: $OS. Please install curl, jq, and openssl manually."
            exit 1
            ;;
    esac
}

version_ge() {
    printf '%s\n%s\n' "$2" "$1" | sort -V -C
}

check_docker() {
    log_info "Checking Docker installation..."

    if ! check_command docker; then
        log_error "Docker not found. Installing Docker..."
        install_docker
    fi

    local docker_version
    docker_version=$(docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)

    if ! version_ge "$docker_version" "$MIN_DOCKER_VERSION"; then
        log_error "Docker version $docker_version is too old. Required: $MIN_DOCKER_VERSION+"
        exit 1
    fi

    log_success "Docker $docker_version detected"

    # Check if docker daemon is running
    if ! docker info &> /dev/null; then
        log_error "Docker daemon is not running. Please start Docker and try again."
        exit 1
    fi
}

check_compose() {
    log_info "Checking Docker Compose installation..."

    if ! check_command "docker-compose" && ! docker compose version &> /dev/null; then
        log_error "Docker Compose not found. Installing..."
        install_compose
    fi

    local compose_version
    if docker compose version &> /dev/null; then
        compose_version=$(docker compose version --short 2>/dev/null || docker compose version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    else
        compose_version=$(docker-compose --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    fi

    if ! version_ge "$compose_version" "$MIN_COMPOSE_VERSION"; then
        log_error "Docker Compose version $compose_version is too old. Required: $MIN_COMPOSE_VERSION+"
        exit 1
    fi

    log_success "Docker Compose $compose_version detected"
}

install_docker() {
    log_info "Installing Docker..."

    # Detect OS
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        OS=$ID
        VERSION=$VERSION_ID
    else
        log_error "Cannot detect OS. Please install Docker manually."
        exit 1
    fi

    case $OS in
        ubuntu|debian)
            sudo apt-get update
            sudo apt-get install -y ca-certificates curl gnupg lsb-release
            curl -fsSL https://download.docker.com/linux/$OS/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/$OS $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
            sudo apt-get update
            sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
            ;;
        centos|rhel|fedora)
            sudo yum install -y yum-utils
            sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
            sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
            sudo systemctl start docker
            sudo systemctl enable docker
            ;;
        *)
            log_error "Unsupported OS: $OS. Please install Docker manually."
            exit 1
            ;;
    esac

    # Add user to docker group
    if groups "$USER" | grep -q '\bdocker\b'; then
        log_info "User already in docker group"
    else
        log_info "Adding user to docker group..."
        sudo usermod -aG docker "$USER"
        log_warn "Please log out and back in for docker group changes to take effect"
        log_warn "Or run: newgrp docker"
    fi
}

install_compose() {
    log_info "Installing Docker Compose..."

    # Try to use docker compose plugin first
    if docker compose version &> /dev/null; then
        log_success "Docker Compose plugin already available"
        return
    fi

    # Fallback to standalone docker-compose
    local compose_version="2.21.0"
    local compose_url="https://github.com/docker/compose/releases/download/v${compose_version}/docker-compose-$(uname -s)-$(uname -m)"

    sudo curl -L "$compose_url" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose

    log_success "Docker Compose installed"
}

check_ports() {
    log_info "Checking port availability..."

    local ports=(80 443 3000 8000 8089 9090 9443 50051)
    local blocked_ports=()

    for port in "${ports[@]}"; do
        # Try ss first, fallback to lsof
        if check_command ss; then
            if ss -tlnp | grep -q ":$port "; then
                blocked_ports+=("$port")
            fi
        elif check_command lsof; then
            if lsof -i -P -n | grep -qE ":$port\b"; then
                blocked_ports+=("$port")
            fi
        else
            log_warn "Cannot check port availability (missing ss and lsof)"
            break
        fi
    done

    if [[ ${#blocked_ports[@]} -gt 0 ]]; then
        log_error "The following ports are already in use: ${blocked_ports[*]}"
        log_error "Please stop the services using these ports or modify the configuration"
        exit 1
    fi

    log_success "All required ports are available"
}

create_directories() {
    log_info "Creating required directories..."

    mkdir -p "$SCRIPT_DIR/provisioning/prometheus/rules" \
             "$SCRIPT_DIR/provisioning/grafana/dashboards" \
             "$SCRIPT_DIR/provisioning/grafana/datasources" \
             "$SCRIPT_DIR/assets/certs" \
             "$SCRIPT_DIR/assets/landing" \
             "$SCRIPT_DIR/data/api" \
             "$SCRIPT_DIR/data/evolution" \
             "$SCRIPT_DIR/data/federation" \
             "$SCRIPT_DIR/data/events" \
             "$SCRIPT_DIR/data/wal"

    log_success "Directories created"
}

generate_secrets() {
    log_info "Generating secure defaults..."

    local env_file="$SCRIPT_DIR/.env"

    # Copy template if .env doesn't exist
    if [[ ! -f "$env_file" ]]; then
        if [[ -f "$SCRIPT_DIR/.env.template" ]]; then
            cp "$SCRIPT_DIR/.env.template" "$env_file"
            log_success "Created .env from template"
        else
            log_error ".env.template not found"
            exit 1
        fi
    fi

    # Generate JWT secret if it's still the default
    if grep -q "changeme-generate-random-secret" "$env_file"; then
        local jwt_secret
        jwt_secret=$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p -c 32)
        sed -i "s/changeme-generate-random-secret/$jwt_secret/" "$env_file"
        log_success "Generated JWT secret"
    fi

    # Generate Grafana password if it's still default
    if grep -q "GRAFANA_PASSWORD=admin" "$env_file"; then
        local grafana_password
        grafana_password=$(openssl rand -base64 16 2>/dev/null || head -c 16 /dev/urandom | base64 | tr -d '\n')
        sed -i "s/GRAFANA_PASSWORD=admin/GRAFANA_PASSWORD=$grafana_password/" "$env_file"
        log_success "Generated Grafana password: $grafana_password"
        echo "$grafana_password" > "$SCRIPT_DIR/.grafana-password"
    fi
}

generate_certs() {
    log_info "Generating self-signed certificates..."

    local cert_dir="$SCRIPT_DIR/assets/certs"
    mkdir -p "$cert_dir"

    if [[ ! -f "$cert_dir/server.crt" ]]; then
        openssl req -x509 -newkey rsa:4096 \
            -keyout "$cert_dir/server.key" -out "$cert_dir/server.crt" \
            -days 365 -nodes \
            -subj "/C=US/ST=CA/L=San Francisco/O=A-SWARM/OU=Pilot/CN=localhost" \
            -addext "subjectAltName=DNS:localhost,DNS:aswarm.local,IP:127.0.0.1" 2>/dev/null

        log_success "Generated self-signed certificate"
        log_warn "⚠️  USING SELF-SIGNED CERTIFICATE - NOT FOR PRODUCTION ⚠️"
    fi
}

setup_configs() {
    log_info "Setting up configuration files..."

    # Render Prometheus config template
    if [[ -f "$SCRIPT_DIR/provisioning/prometheus/prometheus.yml.tmpl" ]]; then
        if check_command envsubst; then
            envsubst < "$SCRIPT_DIR/provisioning/prometheus/prometheus.yml.tmpl" \
                > "$SCRIPT_DIR/provisioning/prometheus/prometheus.yml"
            log_success "Rendered Prometheus configuration"
        else
            log_warn "envsubst not found, copying template as-is"
            cp "$SCRIPT_DIR/provisioning/prometheus/prometheus.yml.tmpl" \
               "$SCRIPT_DIR/provisioning/prometheus/prometheus.yml"
        fi
    fi
}

setup_landing_page() {
    log_info "Setting up landing page..."

    local landing_dir="$SCRIPT_DIR/assets/landing"
    mkdir -p "$landing_dir"

    if [[ ! -f "$landing_dir/index.html" ]]; then
        cat > "$landing_dir/index.html" << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>A-SWARM Pilot Control Center</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
        .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .header { text-align: center; margin-bottom: 30px; }
        .status { display: flex; gap: 20px; margin: 20px 0; }
        .status-card { flex: 1; padding: 15px; background: #f8f9fa; border-radius: 6px; text-align: center; }
        .status-ok { border-left: 4px solid #28a745; }
        .status-warning { border-left: 4px solid #ffc107; }
        .links { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 30px 0; }
        .link-card { padding: 20px; background: #007bff; color: white; text-decoration: none; border-radius: 6px; text-align: center; transition: background 0.3s; }
        .link-card:hover { background: #0056b3; color: white; text-decoration: none; }
        .emergency { background: #dc3545; }
        .emergency:hover { background: #c82333; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🛡️ A-SWARM Pilot Control Center</h1>
            <p>Autonomous Cyber Defense System</p>
        </div>

        <div class="status">
            <div class="status-card status-ok">
                <h3>System Status</h3>
                <p id="system-status">Checking...</p>
            </div>
            <div class="status-card status-warning">
                <h3>Circuit Breaker</h3>
                <p id="circuit-status">Enabled (Safe Mode)</p>
            </div>
        </div>

        <div class="links">
            <a href="http://localhost:8000/api/health" class="link-card">API Health</a>
            <a href="http://localhost:3000" class="link-card">Grafana Dashboard</a>
            <a href="http://localhost:9090" class="link-card">Prometheus Metrics</a>
            <a href="http://localhost:8000/api/autonomy/enable" class="link-card" onclick="return confirm('Enable autonomous evolution?')">Enable Autonomy</a>
            <a href="http://localhost:8000/api/autonomy/disable" class="link-card emergency" onclick="return confirm('Emergency stop all autonomous operations?')">🚨 Emergency Stop</a>
        </div>

        <div style="margin-top: 30px; padding: 15px; background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 6px;">
            <h4>Quick Start:</h4>
            <ol>
                <li>Verify all services are healthy (green status above)</li>
                <li>Access <strong>Grafana Dashboard</strong> to view metrics</li>
                <li>When ready, click <strong>Enable Autonomy</strong> to start evolution</li>
                <li>Use <strong>Emergency Stop</strong> if needed</li>
            </ol>
        </div>
    </div>

    <script>
        // Basic health check
        fetch('http://localhost:8000/api/health')
            .then(r => r.json())
            .then(data => {
                document.getElementById('system-status').textContent = data.status === 'healthy' ? 'Healthy ✅' : 'Degraded ⚠️';
            })
            .catch(() => {
                document.getElementById('system-status').textContent = 'Offline ❌';
            });
    </script>
</body>
</html>
EOF
        log_success "Created landing page"
    fi
}

start_services() {
    log_info "Pre-pulling container images..."
    cd "$SCRIPT_DIR"
    docker compose pull || log_warn "Some images failed to pull, continuing..."

    log_info "Starting A-SWARM services..."

    # Use --wait if available (Compose v2.17+)
    if docker compose up --help | grep -q -- --wait; then
        docker compose up -d --wait
        log_success "All services started and healthy"
    else
        docker compose up -d
        log_info "Services started, checking health..."
        sleep 15  # Give services time to start
    fi
}

run_health_checks() {
    log_info "Running post-deployment health checks..."

    local checks=(
        "http://localhost:8000/api/health:API Health"
        "http://localhost:9090/-/healthy:Prometheus Health"
    )

    # Special handling for Grafana (may need auth)
    if curl -fsSL "http://localhost:3000/api/health" &> /dev/null; then
        checks+=("http://localhost:3000/api/health:Grafana Health")
    elif curl -fsSL "http://localhost:3000/login" &> /dev/null; then
        log_success "Grafana Health: ✅ (login page accessible)"
    else
        log_warn "Grafana Health: ⚠️ (check manually)"
    fi

    local failed_checks=()

    for check in "${checks[@]}"; do
        local url="${check%%:*}"
        local name="${check##*:}"

        if curl -fsSL "$url" &> /dev/null; then
            log_success "$name: ✅"
        else
            log_error "$name: ❌"
            failed_checks+=("$name")
        fi
    done

    # Check metrics
    local metrics_count=0
    if metrics_response=$(curl -fsSL "http://localhost:9090/api/v1/label/__name__/values" 2>/dev/null); then
        metrics_count=$(echo "$metrics_response" | jq -r '.data[]? // empty | select(test("^aswarm_"))' | wc -l || echo "0")
    fi

    if [[ $metrics_count -ge 5 ]]; then
        log_success "A-SWARM Metrics: ✅ ($metrics_count metrics found)"
    else
        log_warn "A-SWARM Metrics: ⚠️ ($metrics_count metrics found, expected ≥5)"
        failed_checks+=("Metrics")
    fi

    if [[ ${#failed_checks[@]} -gt 0 ]]; then
        log_warn "Some health checks failed: ${failed_checks[*]}"
        log_warn "Showing recent logs for troubleshooting..."
        timeout 30s docker compose logs --tail=50 api evolution federation udp-listener prometheus grafana 2>/dev/null || true
        return 1
    fi

    log_success "All health checks passed!"
    return 0
}

show_access_info() {
    echo
    echo -e "${GREEN}🎉 A-SWARM Pilot deployment complete!${NC}"
    echo
    echo -e "${BLUE}Access URLs:${NC}"
    echo "  🏠 Control Center:    http://localhost"
    echo "  📊 Grafana Dashboard: http://localhost:3000"
    echo "  📈 Prometheus:        http://localhost:9090"
    echo "  🔗 API Endpoint:      http://localhost:8000"
    echo
    echo -e "${BLUE}Default Credentials:${NC}"
    if [[ -f "$SCRIPT_DIR/.grafana-password" ]]; then
        echo "  Grafana: admin / $(cat "$SCRIPT_DIR/.grafana-password")"
    else
        echo "  Grafana: admin / admin"
    fi
    echo
    echo -e "${YELLOW}⚠️  IMPORTANT SAFETY NOTES:${NC}"
    echo "  • Circuit breaker is ENABLED by default (autonomous evolution disabled)"
    echo "  • Click 'Enable Autonomy' in Control Center when ready"
    echo "  • Use 'Emergency Stop' to halt all autonomous operations"
    echo
    echo -e "${BLUE}Quick Commands:${NC}"
    echo "  Enable autonomy:   curl -X POST http://localhost:8000/api/autonomy/enable"
    echo "  Emergency stop:    curl -X POST http://localhost:8000/api/autonomy/disable"
    echo "  Open Control:      xdg-open http://localhost || open http://localhost"
    echo
    echo -e "${BLUE}Management Commands:${NC}"
    echo "  Start:      docker compose up -d"
    echo "  Stop:       docker compose down"
    echo "  Reset:      docker compose down -v"
    echo "  Logs:       docker compose logs -f"
    echo "  Health:     docker compose ps"
    echo
}

cleanup_on_error() {
    log_error "Installation failed during setup phase."
    if [[ -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
        read -r -p "Run 'docker compose down' to cleanup? [y/N] " ans
        if [[ "${ans,,}" == "y" ]]; then
            cd "$SCRIPT_DIR" && docker compose down || true
        fi
    fi
    exit 1
}

main() {
    # Set up error handling for setup phase only
    trap cleanup_on_error ERR

    print_banner

    # Preflight checks
    check_dependencies
    check_docker
    check_compose
    check_ports

    # Setup
    create_directories
    generate_secrets
    generate_certs
    setup_configs
    setup_landing_page

    # Disable error trap for deployment phase
    trap - ERR

    # Deploy
    if ! start_services; then
        log_error "Service startup failed"
        exit 1
    fi

    # Verify
    if ! run_health_checks; then
        log_warn "Some health checks failed, but services are running"
        log_info "Check 'docker compose logs' for troubleshooting"
    fi

    # Success
    show_access_info
}

# Run main function
main "$@"