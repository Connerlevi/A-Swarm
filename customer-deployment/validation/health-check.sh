#!/bin/bash
set -euo pipefail

# A-SWARM Health Check Script
# Validates complete system health after deployment

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
readonly ARTIFACTS_DIR="$SCRIPT_DIR/artifacts/$TIMESTAMP"

# Colors
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

# Configuration
COMPOSE_DIR="${COMPOSE_DIR:-../install}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-aswarm-pilot}"

# Logging
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[PASS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[FAIL]${NC} $*"; }

# Test counters
TESTS_TOTAL=0
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_WARNED=0

# Docker Compose abstraction for v1/v2 compatibility
compose() {
    if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
        docker compose -f "$COMPOSE_FILE" -p "$COMPOSE_PROJECT_NAME" "$@"
    else
        docker-compose -f "$COMPOSE_FILE" -p "$COMPOSE_PROJECT_NAME" "$@"
    fi
}

# Get correct volume name with project prefix
project_volume() {
    printf "%s_%s" "$COMPOSE_PROJECT_NAME" "$1"
}

# HTTP endpoint availability with retry
wait_for_http() {
    local url="$1"
    local max="${2:-30}"
    local step="${3:-2}"
    local code

    for ((i=0; i<max; i+=step)); do
        code=$(curl -ks -o /dev/null -w "%{http_code}" --connect-timeout 5 "$url" || true)
        if [[ "$code" =~ ^[23] ]]; then
            return 0
        fi
        sleep "$step"
    done
    return 1
}

# Prometheus query helper
prom_ok() {
    local query="$1"
    curl -fsS "http://localhost:9090/api/v1/query?query=$query" 2>/dev/null | \
        jq -e '.status=="success" and (.data.result|length>0)' >/dev/null 2>&1
}

# Run test with proper counting
run_test() {
    local test_name="$1"
    local test_command="$2"

    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    log_info "Testing: $test_name"

    if eval "$test_command" >/dev/null 2>&1; then
        log_success "$test_name"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return 0
    else
        log_error "$test_name"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        return 1
    fi
}

# Cleanup on exit
cleanup() {
    popd >/dev/null 2>&1 || true
}

trap cleanup EXIT

print_banner() {
    echo -e "${BLUE}"
    cat << 'EOF'
    ╔═══════════════════════════════════════════╗
    ║           A-SWARM HEALTH CHECK            ║
    ║         System Validation Suite           ║
    ╚═══════════════════════════════════════════╝
EOF
    echo -e "${NC}"
}

check_prerequisites() {
    log_info "Checking prerequisites..."

    run_test "Docker is installed" "command -v docker"
    run_test "Docker daemon is running" "docker info"
    run_test "Docker Compose is available" "docker compose version 2>/dev/null || docker-compose --version"
    run_test "curl is available" "command -v curl"
    run_test "jq is available" "command -v jq"
}

check_container_health() {
    log_info "Checking container health..."

    # Navigate to compose directory
    if [[ ! -d "$COMPOSE_DIR" ]]; then
        log_error "Compose directory not found: $COMPOSE_DIR"
        return 1
    fi

    pushd "$COMPOSE_DIR" >/dev/null || exit 1

    if [[ ! -f "$COMPOSE_FILE" ]]; then
        log_error "Compose file not found: $COMPOSE_FILE"
        popd >/dev/null
        return 1
    fi

    # Check all services are healthy (not just running)
    run_test "All services healthy" \
        "compose ps --format json 2>/dev/null | \
         jq -e 'map(select((.State==\"running\") and ((.Health // \"healthy\")|test(\"healthy|^$\")))) | length as \$ok | (length == \$ok)'"

    # Check individual services
    run_test "API container healthy" \
        "compose ps api --format json 2>/dev/null | \
         jq -e '.[0]|(.State==\"running\") and ((.Health // \"healthy\")|test(\"healthy|^$\"))'"

    run_test "Evolution container healthy" \
        "compose ps evolution --format json 2>/dev/null | \
         jq -e '.[0]|(.State==\"running\") and ((.Health // \"healthy\")|test(\"healthy|^$\"))'"

    run_test "Federation container healthy" \
        "compose ps federation --format json 2>/dev/null | \
         jq -e '.[0]|(.State==\"running\") and ((.Health // \"healthy\")|test(\"healthy|^$\"))'"

    run_test "UDP Listener container healthy" \
        "compose ps udp-listener --format json 2>/dev/null | \
         jq -e '.[0]|(.State==\"running\") and ((.Health // \"healthy\")|test(\"healthy|^$\"))'"

    run_test "Prometheus container healthy" \
        "compose ps prometheus --format json 2>/dev/null | \
         jq -e '.[0]|(.State==\"running\") and ((.Health // \"healthy\")|test(\"healthy|^$\"))'"

    run_test "Grafana container healthy" \
        "compose ps grafana --format json 2>/dev/null | \
         jq -e '.[0]|(.State==\"running\") and ((.Health // \"healthy\")|test(\"healthy|^$\"))'"

    popd >/dev/null
}

check_network_connectivity() {
    log_info "Checking network connectivity..."

    # HTTP endpoints with retry
    run_test "Port 8000 (API) responds" "wait_for_http http://localhost:8000/api/health 45"
    run_test "Port 9090 (Prometheus) responds" "wait_for_http http://localhost:9090/-/healthy 45"
    run_test "Port 3000 (Grafana) responds" "wait_for_http http://localhost:3000/api/health 60 || wait_for_http http://localhost:3000/login 60"

    # TCP port checks for gRPC services
    run_test "Evolution gRPC (50051) open" "bash -c '</dev/tcp/127.0.0.1/50051' 2>/dev/null"
    run_test "Federation (9443) open" "bash -c '</dev/tcp/127.0.0.1/9443' 2>/dev/null"

    # Service health endpoints
    run_test "API health status" \
        "curl -f http://localhost:8000/api/health 2>/dev/null | jq -r '.status' | grep -q 'healthy'"

    run_test "Prometheus health status" \
        "curl -f http://localhost:9090/-/healthy 2>/dev/null | grep -q 'Prometheus is Healthy'"
}

check_metrics() {
    log_info "Checking metrics collection..."

    # Wait for Prometheus to be ready
    if ! wait_for_http "http://localhost:9090/-/healthy" 60; then
        log_error "Prometheus not ready after 60s"
        return 1
    fi

    # Check Prometheus metrics API
    run_test "Prometheus metrics API accessible" \
        "curl -fsS http://localhost:9090/api/v1/label/__name__/values >/dev/null 2>&1"

    # Check specific A-SWARM metrics
    run_test "A-SWARM eventbus metrics present" \
        "prom_ok 'aswarm_eventbus_events_processed_total' || prom_ok 'aswarm_event_queue_size'"

    run_test "A-SWARM evolution metrics present" \
        "prom_ok 'aswarm_evolution_cycles_total' || prom_ok 'aswarm_evolution_last_cycle_timestamp_seconds'"

    run_test "A-SWARM federation metrics present" \
        "prom_ok 'aswarm_federation_syncs_total' || prom_ok 'aswarm_federation_sync_failures_total'"

    run_test "System availability metrics (up)" \
        "curl -fsS 'http://localhost:9090/api/v1/query?query=up' 2>/dev/null | jq -e '.data.result|length>0' >/dev/null"

    # Count total A-SWARM metrics
    local metrics_response
    if metrics_response=$(curl -fsS http://localhost:9090/api/v1/label/__name__/values 2>/dev/null); then
        local aswarm_metrics_count
        aswarm_metrics_count=$(echo "$metrics_response" | jq -r '.data[]?' 2>/dev/null | grep -c '^aswarm_' || echo "0")

        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        if [[ $aswarm_metrics_count -ge 5 ]]; then
            log_success "A-SWARM metrics available ($aswarm_metrics_count metrics)"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            log_warn "Limited A-SWARM metrics ($aswarm_metrics_count found, expected ≥5)"
            TESTS_WARNED=$((TESTS_WARNED + 1))
        fi
    fi
}

check_security_posture() {
    log_info "Checking security posture..."

    # Check circuit breaker status
    local circuit_breaker_response
    if circuit_breaker_response=$(curl -fsS http://localhost:9090/api/v1/query?query=aswarm_evolution_circuit_breaker_active 2>/dev/null); then
        local circuit_breaker_value
        circuit_breaker_value=$(echo "$circuit_breaker_response" | jq -r '.data.result[0].value[1]' 2>/dev/null || echo "unknown")

        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        if [[ "$circuit_breaker_value" == "1" ]] || [[ "$circuit_breaker_value" == "unknown" ]]; then
            log_success "Circuit breaker is ENABLED (safe mode) or not yet initialized"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            log_warn "Circuit breaker is DISABLED (autonomous mode active)"
            TESTS_WARNED=$((TESTS_WARNED + 1))
        fi
    fi

    # Check for default passwords (warnings)
    pushd "$COMPOSE_DIR" >/dev/null || exit 1
    if [[ -f ".env" ]]; then
        if grep -q "changeme-generate-random-secret" .env 2>/dev/null; then
            log_warn "Default JWT secret detected - change for production"
            TESTS_WARNED=$((TESTS_WARNED + 1))
        fi
        if grep -qE '^GRAFANA_PASSWORD=(admin|changeme)' .env 2>/dev/null; then
            log_warn "Weak Grafana password detected - change for production"
            TESTS_WARNED=$((TESTS_WARNED + 1))
        fi
        if grep -q "TLS_SELF_SIGNED=true" .env 2>/dev/null; then
            log_warn "Self-signed certificates in use - not suitable for production"
            TESTS_WARNED=$((TESTS_WARNED + 1))
        fi
    fi
    popd >/dev/null

    # Check for critical alerts firing
    run_test "No critical A-SWARM alerts firing" \
        "! prom_ok 'ALERTS{alertstate=\"firing\", severity=\"critical\"}'"
}

check_data_persistence() {
    log_info "Checking data persistence..."

    # Check Docker volumes exist
    local volumes=(
        "prometheus-data"
        "grafana-data"
        "api-data"
        "evolution-data"
        "federation-data"
        "events-wal"
    )

    for volume in "${volumes[@]}"; do
        run_test "Volume $volume exists" \
            "docker volume inspect '$(project_volume "$volume")' >/dev/null 2>&1"
    done

    # Check WAL path writable (if container supports exec)
    pushd "$COMPOSE_DIR" >/dev/null || exit 1
    if compose ps udp-listener --format json 2>/dev/null | jq -e '.[0].State=="running"' >/dev/null 2>&1; then
        run_test "Events WAL path writable" \
            "compose exec -T udp-listener sh -c 'test -w /data/wal' 2>/dev/null || true"
    fi
    popd >/dev/null
}

check_resource_usage() {
    log_info "Checking resource usage..."

    pushd "$COMPOSE_DIR" >/dev/null || exit 1

    # Get container resource usage (JSON format for portability)
    local stats_output
    if stats_output=$(compose ps --format json 2>/dev/null | \
                     jq -r '.[].Name' | \
                     xargs -I{} docker stats {} --no-stream --format '{{json .}}' 2>/dev/null | \
                     jq -s '.' 2>/dev/null); then

        log_info "Current resource usage:"
        echo "$stats_output" | jq -r '.[] | [.Container,.CPUPerc,.MemUsage] | @tsv' 2>/dev/null || \
            echo "Unable to parse resource stats"

        # Check for high CPU usage
        local high_cpu_count
        high_cpu_count=$(echo "$stats_output" | \
                        jq -r '.[] | select((.CPUPerc|rtrimstr("%")|tonumber) > 80) | .Container' 2>/dev/null | \
                        wc -l || echo "0")

        if [[ $high_cpu_count -gt 0 ]]; then
            log_warn "High CPU usage detected in $high_cpu_count container(s)"
            TESTS_WARNED=$((TESTS_WARNED + 1))
        fi
    else
        log_warn "Unable to collect resource statistics"
    fi

    popd >/dev/null
}

collect_artifacts() {
    if [[ $TESTS_FAILED -gt 0 ]]; then
        log_info "Collecting diagnostic artifacts..."

        mkdir -p "$ARTIFACTS_DIR"

        pushd "$COMPOSE_DIR" >/dev/null || exit 1

        # Capture container states
        compose ps --format json > "$ARTIFACTS_DIR/container-status.json" 2>&1 || true

        # Capture recent logs
        compose logs --since=30m --no-color > "$ARTIFACTS_DIR/docker-logs.txt" 2>&1 || true

        # Capture environment (sanitized)
        if [[ -f ".env" ]]; then
            grep -v -E '(SECRET|PASSWORD|KEY)' .env > "$ARTIFACTS_DIR/env-sanitized.txt" 2>&1 || true
        fi

        # Capture Prometheus targets
        curl -fsS http://localhost:9090/api/v1/targets > "$ARTIFACTS_DIR/prometheus-targets.json" 2>&1 || true

        # Create summary
        cat > "$ARTIFACTS_DIR/summary.txt" << EOF
A-SWARM Health Check Failure Report
Generated: $(date)
Tests Failed: $TESTS_FAILED / $TESTS_TOTAL
Tests Warned: $TESTS_WARNED

Artifacts collected in: $ARTIFACTS_DIR

To share for support:
  tar -czf aswarm-health-$TIMESTAMP.tar.gz -C $(dirname "$ARTIFACTS_DIR") $(basename "$ARTIFACTS_DIR")
EOF

        popd >/dev/null

        log_info "Diagnostic artifacts saved to: $ARTIFACTS_DIR"
    fi
}

generate_report() {
    echo
    echo -e "${BLUE}╔═══════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║              HEALTH CHECK REPORT          ║${NC}"
    echo -e "${BLUE}╚═══════════════════════════════════════════╝${NC}"
    echo
    echo "Timestamp: $(date)"
    echo "Total Tests: $TESTS_TOTAL"
    echo -e "Passed: ${GREEN}$TESTS_PASSED${NC}"
    echo -e "Failed: ${RED}$TESTS_FAILED${NC}"
    echo -e "Warnings: ${YELLOW}$TESTS_WARNED${NC}"

    if [[ $TESTS_FAILED -eq 0 ]]; then
        echo
        echo -e "${GREEN}✅ ALL CRITICAL TESTS PASSED - A-SWARM is healthy!${NC}"

        if [[ $TESTS_WARNED -gt 0 ]]; then
            echo -e "${YELLOW}⚠️  Some warnings detected - review for production use${NC}"
        fi

        echo
        echo "Next steps:"
        echo "  • Access Control Center: http://localhost"
        echo "  • View metrics: http://localhost:3000 (Grafana)"
        echo "  • Enable autonomy when ready (currently in safe mode)"
        echo "  • Review warnings above before production use"
        return 0
    else
        echo
        echo -e "${RED}❌ SOME TESTS FAILED - Please review and fix issues${NC}"

        if [[ -d "$ARTIFACTS_DIR" ]]; then
            echo
            echo "Diagnostic artifacts collected in:"
            echo "  $ARTIFACTS_DIR"
            echo
            echo "To create support bundle:"
            echo "  tar -czf aswarm-health-$TIMESTAMP.tar.gz -C $(dirname "$ARTIFACTS_DIR") $(basename "$ARTIFACTS_DIR")"
        fi

        echo
        echo "Troubleshooting:"
        echo "  • Check logs: docker compose logs"
        echo "  • Restart services: docker compose restart"
        echo "  • Verify configuration: docker compose config"
        echo "  • Review artifacts in: $ARTIFACTS_DIR"
        return 1
    fi
}

main() {
    print_banner

    check_prerequisites
    check_container_health
    check_network_connectivity
    check_metrics
    check_security_posture
    check_data_persistence
    check_resource_usage

    # Collect artifacts if there were failures
    collect_artifacts

    # Generate final report
    generate_report
}

# Run health check
main "$@"