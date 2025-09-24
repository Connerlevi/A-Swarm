#!/bin/bash
set -euo pipefail

# A-SWARM Performance Check Script
# Validates system meets performance requirements

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
readonly ARTIFACTS_DIR="$SCRIPT_DIR/artifacts/$TIMESTAMP"

# Colors
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

# Configuration (override via env or flags)
API_BASE="${API_BASE:-http://localhost:8000}"
PROM_BASE="${PROM_BASE:-http://localhost:9090}"
GRAFANA_BASE="${GRAFANA_BASE:-http://localhost:3000}"
EVOLUTION_GRPC_PORT="${EVOLUTION_GRPC_PORT:-50051}"
FEDERATION_PORT="${FEDERATION_PORT:-9443}"

COMPOSE_DIR="${COMPOSE_DIR:-../install}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-aswarm-pilot}"

# Performance thresholds
readonly API_LATENCY_P95_MS=100
readonly EVENT_QUEUE_AGE_P95_S=5
readonly PROMETHEUS_SCRAPE_DURATION_S=2
readonly MIN_FREE_MEMORY_GB=2
readonly MIN_FREE_DISK_GB=10

# Test counters
TESTS_TOTAL=0
TESTS_PASSED=0
TESTS_FAILED=0

# Logging
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[PASS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[FAIL]${NC} $*"; }

# Docker Compose abstraction
compose() {
    if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
        docker compose -f "$COMPOSE_FILE" -p "$COMPOSE_PROJECT_NAME" "$@"
    else
        docker-compose -f "$COMPOSE_FILE" -p "$COMPOSE_PROJECT_NAME" "$@"
    fi
}

# Float comparison functions (no bc dependency)
float_lt() { awk -v A="$1" -v B="$2" 'BEGIN{exit !(A < B)}'; }
float_le() { awk -v A="$1" -v B="$2" 'BEGIN{exit !(A <= B)}'; }
float_gt() { awk -v A="$1" -v B="$2" 'BEGIN{exit !(A > B)}'; }
float_ge() { awk -v A="$1" -v B="$2" 'BEGIN{exit !(A >= B)}'; }

# Calculate p95 from stdin (one value per line)
calc_p95() {
    sort -n | awk 'NF{a[NR]=$1} END{if(NR==0){print 0; exit} idx=int(0.95*NR); if(idx<1)idx=1; print a[idx]}'
}

# URL-encode for PromQL queries
url_encode() {
    if command -v python3 &>/dev/null; then
        python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$1"
    elif command -v perl &>/dev/null; then
        perl -MURI::Escape -e 'print uri_escape($ARGV[0]);' "$1"
    else
        # Basic encoding for simple queries (not perfect but handles basics)
        echo "$1" | sed 's/ /%20/g; s/\[/%5B/g; s/\]/%5D/g; s/{/%7B/g; s/}/%7D/g'
    fi
}

# Get Prometheus metric value
get_prom_val() {
    local query="$1"
    local encoded_query
    encoded_query=$(url_encode "$query")
    curl -fsS "${PROM_BASE}/api/v1/query?query=${encoded_query}" 2>/dev/null | \
        jq -r '.data.result[0].value[1]' 2>/dev/null || echo ""
}

# Check if Prometheus metric exists
prom_has() {
    local query="$1"
    local encoded_query
    encoded_query=$(url_encode "$query")
    curl -fsS "${PROM_BASE}/api/v1/query?query=${encoded_query}" 2>/dev/null | \
        jq -e '.data.result|length>0' >/dev/null 2>&1
}

# Run test with counting
run_test() {
    local test_name="$1"
    local test_command="$2"

    TESTS_TOTAL=$((TESTS_TOTAL + 1))

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

print_banner() {
    echo -e "${BLUE}"
    cat << 'EOF'
    ╔═══════════════════════════════════════════╗
    ║         A-SWARM PERFORMANCE CHECK         ║
    ║           Latency & Resource Test         ║
    ╚═══════════════════════════════════════════╝
EOF
    echo -e "${NC}"
}

check_api_latency() {
    log_info "Checking API latency (p95 target ${API_LATENCY_P95_MS}ms)..."

    local samples=50
    local tmp
    tmp=$(mktemp)

    # Collect latency samples
    for i in $(seq 1 "$samples"); do
        local t0=$(date +%s%3N)
        curl -fsS "${API_BASE}/api/health" >/dev/null 2>&1 || true
        local t1=$(date +%s%3N)
        echo $((t1 - t0)) >> "$tmp"
        sleep 0.05
    done

    # Calculate p95 and average
    local p95
    p95=$(calc_p95 < "$tmp")
    local avg
    avg=$(awk '{s+=$1} END{if(NR) print int(s/NR); else print 0}' "$tmp")
    rm -f "$tmp"

    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    if [ "$p95" -lt "$API_LATENCY_P95_MS" ]; then
        log_success "API latency p95=${p95}ms (avg=${avg}ms, threshold=${API_LATENCY_P95_MS}ms)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        log_warn "API latency p95=${p95}ms (avg=${avg}ms, threshold=${API_LATENCY_P95_MS}ms)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

check_prometheus_performance() {
    log_info "Checking Prometheus query performance..."

    # Test a complex query
    local query='sum(rate(aswarm_eventbus_events_processed_total[5m])) by (event_type)'
    local encoded_query
    encoded_query=$(url_encode "$query")

    local t0=$(date +%s%3N)
    if curl -fsS "${PROM_BASE}/api/v1/query?query=${encoded_query}" >/dev/null 2>&1; then
        local t1=$(date +%s%3N)
        local ms=$((t1 - t0))

        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        if [[ $ms -lt 1000 ]]; then
            log_success "Prometheus query latency: ${ms}ms"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            log_warn "Prometheus query latency: ${ms}ms (slow)"
            TESTS_FAILED=$((TESTS_FAILED + 1))

            # Collect artifacts on slow queries
            if [[ $ms -gt 2000 ]]; then
                mkdir -p "$ARTIFACTS_DIR"
                curl -fsS "${PROM_BASE}/api/v1/targets" > "$ARTIFACTS_DIR/prometheus-targets.json" 2>&1 || true
                curl -fsS "${PROM_BASE}/api/v1/status/tsdb" > "$ARTIFACTS_DIR/prometheus-tsdb.json" 2>&1 || true
                log_info "Collected Prometheus diagnostics in $ARTIFACTS_DIR"
            fi
        fi
    else
        log_error "Prometheus query failed"
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi

    # Max scrape duration over last 5m
    local sd_query='max_over_time(prometheus_target_scrape_duration_seconds[5m])'
    local sd
    sd=$(get_prom_val "$sd_query")

    if [[ -n "$sd" && "$sd" != "null" ]]; then
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        if float_lt "$sd" "$PROMETHEUS_SCRAPE_DURATION_S"; then
            log_success "Max scrape duration (5m): ${sd}s"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            log_warn "Max scrape duration (5m): ${sd}s (threshold: ${PROMETHEUS_SCRAPE_DURATION_S}s)"
            TESTS_FAILED=$((TESTS_FAILED + 1))
        fi
    else
        log_info "Scrape duration metric not available yet"
    fi
}

check_event_processing() {
    log_info "Checking event processing performance..."

    # Prefer histogram p95 for event queue age
    local p95_query='histogram_quantile(0.95, sum(rate(aswarm_event_queue_age_seconds_bucket[5m])) by (le))'
    local p95
    p95=$(get_prom_val "$p95_query")

    if [[ -n "$p95" && "$p95" != "null" && "$p95" != "" ]]; then
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        if float_lt "$p95" "$EVENT_QUEUE_AGE_P95_S"; then
            log_success "Event queue age p95: ${p95}s (threshold: ${EVENT_QUEUE_AGE_P95_S}s)"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            log_warn "Event queue age p95: ${p95}s (threshold: ${EVENT_QUEUE_AGE_P95_S}s)"
            TESTS_FAILED=$((TESTS_FAILED + 1))
        fi
    else
        # Fallback to simple gauge if histogram not present
        local gauge
        gauge=$(get_prom_val 'aswarm_event_queue_age_seconds')

        if [[ -n "$gauge" && "$gauge" != "null" && "$gauge" != "" ]]; then
            TESTS_TOTAL=$((TESTS_TOTAL + 1))
            if float_lt "$gauge" "$EVENT_QUEUE_AGE_P95_S"; then
                log_success "Event queue age: ${gauge}s (threshold: ${EVENT_QUEUE_AGE_P95_S}s)"
                TESTS_PASSED=$((TESTS_PASSED + 1))
            else
                log_warn "Event queue age: ${gauge}s (threshold: ${EVENT_QUEUE_AGE_P95_S}s)"
                TESTS_FAILED=$((TESTS_FAILED + 1))
            fi
        else
            log_info "Event queue metrics not yet available"
        fi
    fi

    # Event processing rate
    local rate
    rate=$(get_prom_val 'sum(rate(aswarm_eventbus_events_processed_total[1m]))')
    if [[ -n "$rate" && "$rate" != "null" && "$rate" != "" ]]; then
        log_info "Event processing rate: ${rate} events/sec"
    fi

    # Check key metrics for dashboard
    run_test "Evolution errors metric present" "prom_has 'aswarm_evolution_errors_total'"
    run_test "Autonomous promotions present" "prom_has 'aswarm_promotions_triggered_total'"
    run_test "Federation syncs present" "prom_has 'aswarm_federation_syncs_total'"
}

check_network_ports() {
    log_info "Checking service ports..."

    run_test "Evolution gRPC port ${EVOLUTION_GRPC_PORT} open" \
        "timeout 2 bash -c '</dev/tcp/127.0.0.1/${EVOLUTION_GRPC_PORT}' 2>/dev/null"

    run_test "Federation port ${FEDERATION_PORT} open" \
        "timeout 2 bash -c '</dev/tcp/127.0.0.1/${FEDERATION_PORT}' 2>/dev/null"
}

check_resource_availability() {
    log_info "Checking resource availability..."

    # Check memory (in GB using float comparison)
    local free_memory_gb
    free_memory_gb=$(free -m | awk '/^Mem:/{printf "%.2f", $7/1024}')

    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    if float_ge "$free_memory_gb" "$MIN_FREE_MEMORY_GB"; then
        log_success "Free memory: ${free_memory_gb}GB (minimum: ${MIN_FREE_MEMORY_GB}GB)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        log_warn "Free memory: ${free_memory_gb}GB (minimum: ${MIN_FREE_MEMORY_GB}GB)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi

    # Check disk space
    local free_disk_gb
    free_disk_gb=$(df -BG / | awk 'NR==2 {gsub(/G/, "", $4); print $4}')

    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    if [[ $free_disk_gb -ge $MIN_FREE_DISK_GB ]]; then
        log_success "Free disk: ${free_disk_gb}GB (minimum: ${MIN_FREE_DISK_GB}GB)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        log_warn "Free disk: ${free_disk_gb}GB (minimum: ${MIN_FREE_DISK_GB}GB)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi

    # Check CPU load (1-minute average)
    local load_avg
    load_avg=$(uptime | awk -F'load average: ' '{print $2}' | awk -F', ' '{print $1}' | xargs)
    local cpu_count
    cpu_count=$(nproc)

    log_info "Load average (1m): $load_avg (CPUs: $cpu_count)"

    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    if float_lt "$load_avg" "$cpu_count"; then
        log_success "CPU load is reasonable"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        log_warn "CPU load is high (load: $load_avg, CPUs: $cpu_count)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

run_load_test() {
    log_info "Running brief API load test (100 requests, 20 concurrent)..."

    local total=100
    local concurrency=20
    local failures=0

    # Run concurrent requests using xargs -P
    if seq "$total" | xargs -I{} -P "$concurrency" bash -c "curl -fsS '${API_BASE}/api/health' >/dev/null 2>&1 || exit 1"; then
        log_success "Load test: ${total}/${total} requests successful"
    else
        failures=$?
        log_warn "Load test: some requests failed (exit code: $failures)"
    fi
}

write_json_summary() {
    local out="${SCRIPT_DIR}/perf-summary-${TIMESTAMP}.json"

    # Collect all results
    local api_status="unknown"
    if curl -fsS "${API_BASE}/api/health" >/dev/null 2>&1; then
        api_status="healthy"
    fi

    local prom_status="unknown"
    if curl -fsS "${PROM_BASE}/-/healthy" >/dev/null 2>&1; then
        prom_status="healthy"
    fi

    # Write JSON summary
    jq -n \
        --arg timestamp "$(date -Iseconds)" \
        --arg api_base "${API_BASE}" \
        --arg prom_base "${PROM_BASE}" \
        --arg grafana_base "${GRAFANA_BASE}" \
        --arg api_status "$api_status" \
        --arg prom_status "$prom_status" \
        --argjson tests_total "$TESTS_TOTAL" \
        --argjson tests_passed "$TESTS_PASSED" \
        --argjson tests_failed "$TESTS_FAILED" \
        '{
            timestamp: $timestamp,
            endpoints: {
                api: $api_base,
                prometheus: $prom_base,
                grafana: $grafana_base
            },
            status: {
                api: $api_status,
                prometheus: $prom_status
            },
            tests: {
                total: $tests_total,
                passed: $tests_passed,
                failed: $tests_failed
            }
        }' > "$out"

    log_info "Performance summary saved: $out"
}

generate_report() {
    echo
    echo -e "${BLUE}╔═══════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║         PERFORMANCE CHECK COMPLETE        ║${NC}"
    echo -e "${BLUE}╚═══════════════════════════════════════════╝${NC}"
    echo
    echo "Performance Summary:"
    echo "  • Tests Passed: ${TESTS_PASSED}/${TESTS_TOTAL}"

    if [[ $TESTS_FAILED -eq 0 ]]; then
        echo -e "  ${GREEN}✓ All performance tests passed${NC}"
    else
        echo -e "  ${RED}✗ Some performance tests failed${NC}"
    fi

    echo
    echo "Key Metrics:"
    echo "  • API Response: Sub-100ms p95 target"
    echo "  • Prometheus Queries: <1s target"
    echo "  • Event Queue: <5s p95 target"
    echo "  • Resource Availability: Checked"

    if [[ -d "$ARTIFACTS_DIR" ]]; then
        echo
        echo "Diagnostic artifacts collected in:"
        echo "  $ARTIFACTS_DIR"
    fi

    echo
    echo "Recommendations:"
    echo "  • Monitor event queue age during load"
    echo "  • Set up alerting for high latency"
    echo "  • Consider scaling if load increases"
    echo "  • Review any warnings above"
    echo

    if [[ $TESTS_FAILED -gt 0 ]]; then
        return 1
    fi
    return 0
}

main() {
    print_banner

    # Core performance checks
    check_api_latency
    check_prometheus_performance
    check_event_processing
    check_network_ports
    check_resource_availability

    # Load test
    run_load_test

    # Save results
    write_json_summary

    # Final report
    generate_report
}

main "$@"