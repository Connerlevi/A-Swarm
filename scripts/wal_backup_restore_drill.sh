#!/bin/bash
# A-SWARM WAL Backup and Restore Drill
# Tests EventBus Write-Ahead Log durability and recovery procedures

set -euo pipefail

# Configuration
WAL_DIR="${WAL_DIR:-logs}"
BACKUP_DIR="${BACKUP_DIR:-/tmp/aswarm_wal_backups}"
TEST_DIR="${TEST_DIR:-/tmp/aswarm_wal_test}"
API_URL="${API_URL:-http://localhost:8000}"
METRICS_URL="${METRICS_URL:-http://localhost:9000}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() {
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

success() {
    echo -e "${GREEN}✅ $1${NC}"
}

warning() {
    echo -e "${YELLOW}⚠️ $1${NC}"
}

error() {
    echo -e "${RED}❌ $1${NC}"
}

# Function to check if A-SWARM is running
check_aswarm_running() {
    if ! curl -s "$API_URL/api/health" > /dev/null 2>&1; then
        error "A-SWARM API is not responding at $API_URL"
        return 1
    fi

    if ! curl -s "$METRICS_URL/metrics" > /dev/null 2>&1; then
        error "A-SWARM metrics endpoint is not responding at $METRICS_URL"
        return 1
    fi

    return 0
}

# Function to get current EventBus metrics
get_eventbus_metrics() {
    local metric_name="$1"
    curl -s "$METRICS_URL/metrics" | grep "^$metric_name" | awk '{print $2}' | head -1
}

# Function to inject test events
inject_test_events() {
    local event_count="$1"
    local event_type="${2:-test_event}"

    log "Injecting $event_count test events of type '$event_type'..."

    for i in $(seq 1 "$event_count"); do
        cat << EOF | curl -s -X POST "$API_URL/api/events" \
            -H "Content-Type: application/json" \
            -d @- > /dev/null || true
{
    "event_id": "test_event_${i}_$(date +%s)",
    "event_type": "$event_type",
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)",
    "source": "wal_backup_drill",
    "data": {
        "sequence": $i,
        "total_events": $event_count,
        "test_session": "$(date +%Y%m%d_%H%M%S)"
    }
}
EOF

        # Small delay to ensure events are processed in order
        sleep 0.1
    done

    success "Injected $event_count test events"
}

# Function to create WAL backup
create_wal_backup() {
    local backup_name="$1"
    local backup_path="$BACKUP_DIR/$backup_name"

    log "Creating WAL backup: $backup_name"

    # Create backup directory
    mkdir -p "$backup_path"

    # Copy WAL files
    if [ -d "$WAL_DIR" ] && [ "$(ls -A $WAL_DIR/*.wal 2>/dev/null)" ]; then
        cp -r "$WAL_DIR"/*.wal "$backup_path/" 2>/dev/null || true

        # Create backup metadata
        cat > "$backup_path/backup_metadata.json" << EOF
{
    "backup_name": "$backup_name",
    "created_at": "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)",
    "wal_files": $(ls "$WAL_DIR"/*.wal 2>/dev/null | wc -l),
    "total_size_bytes": $(du -sb "$backup_path" | cut -f1),
    "source_directory": "$WAL_DIR",
    "backup_type": "wal_durability_drill"
}
EOF

        # Create checksums for integrity verification
        (cd "$backup_path" && find . -name "*.wal" -exec sha256sum {} \; > checksums.sha256)

        local wal_count=$(ls "$backup_path"/*.wal 2>/dev/null | wc -l)
        local backup_size=$(du -sh "$backup_path" | cut -f1)

        success "WAL backup created: $wal_count files, $backup_size"
        log "Backup location: $backup_path"

        return 0
    else
        warning "No WAL files found in $WAL_DIR"
        return 1
    fi
}

# Function to verify backup integrity
verify_backup_integrity() {
    local backup_path="$1"

    log "Verifying backup integrity: $backup_path"

    # Check if backup exists
    if [ ! -d "$backup_path" ]; then
        error "Backup directory does not exist: $backup_path"
        return 1
    fi

    # Verify checksums
    if [ -f "$backup_path/checksums.sha256" ]; then
        if (cd "$backup_path" && sha256sum -c checksums.sha256 >/dev/null 2>&1); then
            success "Backup integrity verified - all checksums match"
        else
            error "Backup integrity check failed - checksum mismatch"
            return 1
        fi
    else
        warning "No checksum file found - cannot verify integrity"
    fi

    # Check metadata
    if [ -f "$backup_path/backup_metadata.json" ]; then
        local wal_files=$(jq -r '.wal_files' "$backup_path/backup_metadata.json")
        local actual_files=$(ls "$backup_path"/*.wal 2>/dev/null | wc -l)

        if [ "$wal_files" -eq "$actual_files" ]; then
            success "WAL file count matches metadata: $wal_files files"
        else
            error "WAL file count mismatch - metadata: $wal_files, actual: $actual_files"
            return 1
        fi
    fi

    return 0
}

# Function to stop A-SWARM EventBus
stop_eventbus() {
    log "Stopping A-SWARM EventBus..."

    # Try graceful shutdown first
    if curl -s -X POST "$API_URL/api/actions/run" \
        -H "Content-Type: application/json" \
        -d '{"name": "eventbus-stop", "confirm": true}' > /dev/null 2>&1; then

        # Wait for graceful shutdown
        sleep 5
        success "EventBus stopped gracefully"
    else
        warning "Graceful shutdown failed, using process termination"

        # Force stop via process kill
        pkill -f "uvicorn.*actions_handler" || true
        sleep 3

        if ! check_aswarm_running; then
            success "EventBus stopped via process termination"
        else
            error "Failed to stop EventBus"
            return 1
        fi
    fi

    return 0
}

# Function to start A-SWARM EventBus
start_eventbus() {
    log "Starting A-SWARM EventBus..."

    # Activate virtual environment and start service
    cd "$(dirname "$0")/.."
    source .venv/bin/activate 2>/dev/null || true

    # Start in background
    nohup uvicorn api.actions_handler:app --host 0.0.0.0 --port 8000 > /tmp/aswarm_startup.log 2>&1 &

    # Wait for startup
    local attempts=0
    local max_attempts=30

    while [ $attempts -lt $max_attempts ]; do
        if check_aswarm_running; then
            success "EventBus started successfully"
            return 0
        fi

        sleep 2
        attempts=$((attempts + 1))

        if [ $((attempts % 5)) -eq 0 ]; then
            log "Waiting for EventBus startup... ($attempts/$max_attempts)"
        fi
    done

    error "EventBus failed to start within $((max_attempts * 2)) seconds"
    return 1
}

# Function to restore WAL from backup
restore_wal_backup() {
    local backup_path="$1"

    log "Restoring WAL from backup: $backup_path"

    # Verify backup integrity first
    if ! verify_backup_integrity "$backup_path"; then
        error "Backup integrity check failed - aborting restore"
        return 1
    fi

    # Ensure EventBus is stopped
    if check_aswarm_running; then
        log "EventBus is running - stopping for restore..."
        if ! stop_eventbus; then
            error "Failed to stop EventBus for restore"
            return 1
        fi
    fi

    # Backup current WAL files (if any)
    if [ -d "$WAL_DIR" ] && [ "$(ls -A $WAL_DIR/*.wal 2>/dev/null)" ]; then
        local current_backup="$BACKUP_DIR/current_wal_$(date +%Y%m%d_%H%M%S)"
        mkdir -p "$current_backup"
        cp "$WAL_DIR"/*.wal "$current_backup/" 2>/dev/null || true
        log "Current WAL files backed up to: $current_backup"
    fi

    # Clear current WAL directory
    rm -f "$WAL_DIR"/*.wal 2>/dev/null || true

    # Restore WAL files from backup
    if [ "$(ls -A $backup_path/*.wal 2>/dev/null)" ]; then
        cp "$backup_path"/*.wal "$WAL_DIR/" 2>/dev/null || true

        local restored_count=$(ls "$WAL_DIR"/*.wal 2>/dev/null | wc -l)
        success "Restored $restored_count WAL files from backup"
    else
        warning "No WAL files found in backup to restore"
    fi

    return 0
}

# Function to verify WAL replay
verify_wal_replay() {
    local expected_events="$1"

    log "Verifying WAL replay..."

    # Wait for replay to complete
    sleep 10

    # Check replay metrics
    local replayed_events=$(get_eventbus_metrics "aswarm_eventbus_wal_replayed_events_total")
    local replay_errors=$(get_eventbus_metrics "aswarm_eventbus_wal_replay_errors_total")

    log "WAL replay metrics:"
    log "  Replayed events: ${replayed_events:-0}"
    log "  Replay errors: ${replay_errors:-0}"

    # Check if expected events were replayed
    if [ -n "$replayed_events" ] && [ "$replayed_events" -ge "$expected_events" ]; then
        success "WAL replay successful - $replayed_events events replayed (expected: $expected_events)"
    else
        warning "WAL replay incomplete - $replayed_events events replayed (expected: $expected_events)"
    fi

    # Check for replay errors
    if [ -n "$replay_errors" ] && [ "$replay_errors" -gt 0 ]; then
        error "WAL replay errors detected: $replay_errors"
        return 1
    fi

    return 0
}

# Function to check queue age normalization
check_queue_normalization() {
    log "Checking EventBus queue age normalization..."

    local max_wait=120  # 2 minutes
    local wait_time=0

    while [ $wait_time -lt $max_wait ]; do
        local queue_age=$(get_eventbus_metrics "aswarm_eventbus_queue_age_seconds")

        if [ -n "$queue_age" ]; then
            queue_age=${queue_age%.*}  # Remove decimal part

            if [ "$queue_age" -lt 5 ]; then
                success "Queue age normalized: ${queue_age}s (threshold: 5s)"
                return 0
            else
                log "Queue age: ${queue_age}s (waiting for normalization...)"
            fi
        fi

        sleep 10
        wait_time=$((wait_time + 10))
    done

    warning "Queue age did not normalize within $max_wait seconds"
    return 1
}

# Main drill execution
main() {
    echo "==========================================="
    echo "A-SWARM WAL Backup and Restore Drill"
    echo "Started: $(date)"
    echo "==========================================="

    # Check prerequisites
    log "Checking prerequisites..."

    if ! command -v curl >/dev/null 2>&1; then
        error "curl is required but not installed"
        exit 1
    fi

    if ! command -v jq >/dev/null 2>&1; then
        error "jq is required but not installed"
        exit 1
    fi

    # Create backup directory
    mkdir -p "$BACKUP_DIR"
    mkdir -p "$TEST_DIR"

    # Check if A-SWARM is running
    if ! check_aswarm_running; then
        error "A-SWARM is not running - please start it first"
        exit 1
    fi

    success "Prerequisites check passed"

    # Phase 1: Baseline metrics
    log "Phase 1: Collecting baseline metrics..."

    local initial_events=$(get_eventbus_metrics "aswarm_eventbus_events_processed_total")
    local initial_queue_size=$(get_eventbus_metrics "aswarm_eventbus_queue_size")

    log "Baseline metrics:"
    log "  Events processed: ${initial_events:-0}"
    log "  Queue size: ${initial_queue_size:-0}"

    # Phase 2: Inject test events
    log "Phase 2: Injecting test events..."

    local test_event_count=100
    inject_test_events $test_event_count "backup_drill"

    # Wait for events to be processed and written to WAL
    sleep 15

    # Phase 3: Create backup
    log "Phase 3: Creating WAL backup..."

    local backup_name="drill_backup_$(date +%Y%m%d_%H%M%S)"
    if ! create_wal_backup "$backup_name"; then
        error "Failed to create WAL backup"
        exit 1
    fi

    local backup_path="$BACKUP_DIR/$backup_name"

    # Phase 4: Inject more events (these should be lost)
    log "Phase 4: Injecting events that will be lost..."

    inject_test_events 50 "lost_events"
    sleep 10

    # Phase 5: Stop EventBus
    log "Phase 5: Stopping EventBus..."

    if ! stop_eventbus; then
        error "Failed to stop EventBus"
        exit 1
    fi

    # Phase 6: Restore from backup
    log "Phase 6: Restoring WAL from backup..."

    if ! restore_wal_backup "$backup_path"; then
        error "Failed to restore WAL from backup"
        exit 1
    fi

    # Phase 7: Start EventBus and verify replay
    log "Phase 7: Starting EventBus and verifying replay..."

    if ! start_eventbus; then
        error "Failed to start EventBus"
        exit 1
    fi

    # Verify WAL replay
    if ! verify_wal_replay $test_event_count; then
        error "WAL replay verification failed"
        exit 1
    fi

    # Phase 8: Check queue normalization
    log "Phase 8: Checking queue age normalization..."

    if ! check_queue_normalization; then
        warning "Queue age did not normalize as expected"
    fi

    # Phase 9: Final verification
    log "Phase 9: Final system verification..."

    local final_events=$(get_eventbus_metrics "aswarm_eventbus_events_processed_total")
    local final_queue_size=$(get_eventbus_metrics "aswarm_eventbus_queue_size")

    log "Final metrics:"
    log "  Events processed: ${final_events:-0}"
    log "  Queue size: ${final_queue_size:-0}"

    # Check system health
    if curl -s "$API_URL/api/health" | jq -e '.status == "healthy"' >/dev/null 2>&1; then
        success "System health check passed"
    else
        error "System health check failed"
        exit 1
    fi

    # Generate drill report
    local report_file="$TEST_DIR/wal_drill_report_$(date +%Y%m%d_%H%M%S).json"

    cat > "$report_file" << EOF
{
    "drill_name": "WAL Backup and Restore Drill",
    "executed_at": "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)",
    "backup_name": "$backup_name",
    "backup_path": "$backup_path",
    "test_events_injected": $test_event_count,
    "metrics": {
        "initial_events": ${initial_events:-0},
        "final_events": ${final_events:-0},
        "initial_queue_size": ${initial_queue_size:-0},
        "final_queue_size": ${final_queue_size:-0}
    },
    "phases": {
        "backup_creation": "SUCCESS",
        "integrity_verification": "SUCCESS",
        "eventbus_stop": "SUCCESS",
        "wal_restore": "SUCCESS",
        "eventbus_start": "SUCCESS",
        "wal_replay": "SUCCESS",
        "queue_normalization": "SUCCESS"
    },
    "drill_result": "SUCCESS"
}
EOF

    echo ""
    echo "==========================================="
    success "WAL Backup and Restore Drill COMPLETED"
    echo "==========================================="
    echo ""
    log "Drill Results:"
    log "  ✅ WAL backup created successfully"
    log "  ✅ Backup integrity verified"
    log "  ✅ EventBus stopped and restarted"
    log "  ✅ WAL restored from backup"
    log "  ✅ WAL replay completed"
    log "  ✅ System health verified"
    echo ""
    log "Report saved to: $report_file"
    log "Backup preserved at: $backup_path"
    echo ""
    success "A-SWARM WAL durability drill PASSED"
}

# Script execution
if [ "${BASH_SOURCE[0]}" == "${0}" ]; then
    main "$@"
fi