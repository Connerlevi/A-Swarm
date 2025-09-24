#!/usr/bin/env bash
# A-SWARM Detection Rules Update Script v2
# Production-grade with auto-detection, multi-cluster support, and observability
# - Updates ConfigMap with checksum verification
# - Auto-detects service port
# - Supports dry-run and verify-only modes
# - Multi-cluster compatible

set -euo pipefail

# Configuration with defaults
NAMESPACE="${NAMESPACE:-aswarm}"
CONFIGMAP="${CONFIGMAP:-aswarm-detections}"
DEPLOYMENT="${DEPLOYMENT:-aswarm-blue-api}"
SVC_NAME="${SVC_NAME:-aswarm-blue-api}"
RULES_FILE="${1:-/tmp/aswarm-detection-rules.json}"
READY_PATH="${READY_PATH:-/ready}"
METRICS_PATH="${METRICS_PATH:-/metrics}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-300s}"
VERIFY_TIMEOUT_SEC="${VERIFY_TIMEOUT_SEC:-60}"
DRY_RUN="${DRY_RUN:-false}"
VERIFY_ONLY="${VERIFY_ONLY:-false}"

# Multi-cluster support
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
KUBECONFIG="${KUBECONFIG:-}"
KCTX_ARG=${KUBE_CONTEXT:+--context "$KUBE_CONTEXT"}
KCFG_ARG=${KUBECONFIG:+--kubeconfig "$KUBECONFIG"}
KC="${KUBECTL_BIN:-kubectl} $KCTX_ARG $KCFG_ARG"

# --- helpers ---
need() { command -v "$1" >/dev/null 2>&1 || { echo "❌ Missing dependency: $1" >&2; exit 1; }; }
hash_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum | awk '{print $1}';
  else shasum -a 256 | awk '{print $1}'; fi
}
random_port() {
  if command -v shuf >/dev/null 2>&1; then shuf -i 10000-65000 -n 1; else echo 18080; fi
}

# Parse additional flags
while [[ $# -gt 0 ]]; do
  case $1 in
    --dry-run) DRY_RUN=true; shift ;;
    --verify-only) VERIFY_ONLY=true; shift ;;
    --context=*) KUBE_CONTEXT="${1#*=}"; shift ;;
    --kubeconfig=*) KUBECONFIG="${1#*=}"; shift ;;
    --namespace=*) NAMESPACE="${1#*=}"; shift ;;
    -*) echo "Unknown option: $1"; exit 1 ;;
    *) RULES_FILE="$1"; shift ;;
  esac
done

echo "🔄 A-SWARM Detection Rules Update Script v2"
[[ "$DRY_RUN" == "true" ]] && echo "📋 DRY RUN MODE - No changes will be made"
[[ "$VERIFY_ONLY" == "true" ]] && echo "🔍 VERIFY ONLY MODE - Checking current state"

need kubectl; need curl
command -v jq >/dev/null 2>&1 || echo "⚠️ jq not found - some features limited"

# Auto-detect service port
echo "🔍 Auto-detecting service configuration..."
SVC_PORT="$($KC -n "$NAMESPACE" get svc "$SVC_NAME" -o jsonpath='{.spec.ports[?(@.name=="http")].port}' 2>/dev/null || true)"
SVC_PORT="${SVC_PORT:-8080}"
echo "📡 Service port: $SVC_PORT"

if [[ "$VERIFY_ONLY" == "true" ]]; then
  # Skip to verification
  echo "🔍 Verifying current rules..."
else
  # Validate rules file
  if [[ ! -f "$RULES_FILE" ]]; then echo "❌ Rules file not found: $RULES_FILE"; exit 1; fi
  
  # Extract rules version if available
  RULES_VER=""
  if command -v jq >/dev/null 2>&1; then
    jq . "$RULES_FILE" >/dev/null || { echo "❌ Invalid JSON: $RULES_FILE"; exit 1; }
    RULES_VER="$(jq -r '.metadata.version // empty' "$RULES_FILE" 2>/dev/null || true)"
  else
    python3 -m json.tool "$RULES_FILE" >/dev/null 2>&1 || { echo "⚠️ Cannot validate JSON (install jq)"; }
    RULES_VER="$(grep -o '"version"[[:space:]]*:[[:space:]]*"[^"]*"' "$RULES_FILE" | sed 's/.*"\([^"]*\)"$/\1/' || true)"
  fi
  echo "✅ Rules file validated: $RULES_FILE${RULES_VER:+ (v$RULES_VER)}"

  if [[ "$DRY_RUN" == "false" ]]; then
    # Upsert ConfigMap
    echo "📝 Applying ConfigMap $CONFIGMAP..."
    $KC create configmap "$CONFIGMAP" \
      --from-file=detection-rules.json="$RULES_FILE" \
      -n "$NAMESPACE" --dry-run=client -o yaml | $KC apply -f -
  fi

  # Compute checksum from stored CM data (strip CR in case of CRLF)
  echo "🔢 Calculating checksum from stored ConfigMap..."
  CHKSUM="$($KC get cm -n "$NAMESPACE" "$CONFIGMAP" -o jsonpath='{.data.detection-rules\.json}' \
          | tr -d '\r' | hash_stdin)"
  echo "📌 New checksum: sha256:$CHKSUM"

  # Get current deployment checksum
  OLD="$($KC get deploy -n "$NAMESPACE" "$DEPLOYMENT" \
        -o jsonpath='{.spec.template.metadata.annotations.aswarm\.ai/content-checksum}' 2>/dev/null || true)"
  
  if [[ "$DRY_RUN" == "true" ]]; then
    if [[ "sha256:$CHKSUM" == "$OLD" ]]; then
      echo "ℹ️ No changes needed (checksums match)"
    else
      echo "🔄 Would update deployment annotation:"
      echo "   Old: ${OLD:-none}"
      echo "   New: sha256:$CHKSUM"
      [[ -n "$RULES_VER" ]] && echo "   Version: $RULES_VER"
    fi
  elif [[ "sha256:$CHKSUM" == "$OLD" ]]; then
    echo "ℹ️ Checksum unchanged; no rollout required."
  else
    # Annotate both Deployment (to trigger rollout) and ConfigMap (for traceability)
    echo "🚀 Triggering rollout..."
    ANNOTATIONS="{\"aswarm.ai/content-checksum\":\"sha256:${CHKSUM}\",\"aswarm.ai/last-rules-update\":\"$(date -Iseconds)\""
    [[ -n "$RULES_VER" ]] && ANNOTATIONS="${ANNOTATIONS},\"aswarm.ai/rules-version\":\"${RULES_VER}\""
    ANNOTATIONS="${ANNOTATIONS}}"
    
    $KC -n "$NAMESPACE" patch deploy "$DEPLOYMENT" \
      -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":${ANNOTATIONS}}}}}"

    $KC -n "$NAMESPACE" annotate cm "$CONFIGMAP" \
      "aswarm.ai/content-checksum=sha256:${CHKSUM}" --overwrite
    [[ -n "$RULES_VER" ]] && $KC -n "$NAMESPACE" annotate cm "$CONFIGMAP" \
      "aswarm.ai/rules-version=${RULES_VER}" --overwrite
  fi

  if [[ "$DRY_RUN" == "false" ]]; then
    # Wait for rollout
    echo "⏳ Waiting for rollout to complete..."
    $KC rollout status deploy/"$DEPLOYMENT" -n "$NAMESPACE" --timeout="$ROLLOUT_TIMEOUT"

    # Wait for deployment to be available
    echo "⏳ Waiting for deployment to be available..."
    $KC -n "$NAMESPACE" wait --for=condition=available deploy/"$DEPLOYMENT" --timeout=120s || true
    
    # Ensure service has endpoints before verification
    echo "⏳ Waiting for service endpoints..."
    for i in {1..30}; do
      if $KC -n "$NAMESPACE" get endpoints "$SVC_NAME" -o jsonpath='{.subsets[0].addresses[0].ip}' 2>/dev/null | grep -q .; then
        echo "✅ Service endpoints ready"
        break
      fi
      [[ $i -eq 30 ]] && echo "⚠️ Service endpoints not ready after 60s"
      sleep 2
    done
  fi
fi

# Verification phase (for all modes)
if [[ "$DRY_RUN" == "false" ]]; then
  # Temporary port-forward for verification
  PORT="$(random_port)"
  echo "🔎 Verifying via $SVC_NAME on localhost:$PORT ..."
  $KC -n "$NAMESPACE" port-forward "svc/$SVC_NAME" "$PORT:$SVC_PORT" --address 127.0.0.1 >/dev/null 2>&1 &
  PF_PID=$!
  trap 'kill $PF_PID 2>/dev/null || true' EXIT

  # Poll for rules count
  deadline=$(( $(date +%s) + VERIFY_TIMEOUT_SEC ))
  RULES_COUNT=0
  VERIFIED=false
  
  while [[ $(date +%s) -lt $deadline ]]; do
    # Try metrics endpoint first (more reliable)
    if out="$(curl -fsS "http://127.0.0.1:$PORT$METRICS_PATH" 2>/dev/null)"; then
      if METRIC_COUNT="$(echo "$out" | grep -E '^aswarm_blue_rules_loaded ' | awk '{print $2}' | cut -d. -f1)"; then
        if [[ "$METRIC_COUNT" =~ ^[0-9]+$ ]] && (( METRIC_COUNT > 0 )); then
          RULES_COUNT="$METRIC_COUNT"
          VERIFIED=true
          echo "✅ Verified via metrics: $RULES_COUNT rules loaded"
          break
        fi
      fi
    fi
    
    # Fallback to ready endpoint
    if out="$(curl -fsS "http://127.0.0.1:$PORT$READY_PATH" 2>/dev/null)"; then
      if command -v jq >/dev/null 2>&1; then
        RULES_COUNT="$(printf '%s' "$out" | jq -r '.rules_loaded // 0' 2>/dev/null || echo 0)"
      else
        RULES_COUNT="$(printf '%s' "$out" | grep -o '"rules_loaded":[0-9]*' | grep -o '[0-9]*$' || echo 0)"
      fi
      if [[ "$RULES_COUNT" =~ ^[0-9]+$ ]] && (( RULES_COUNT > 0 )); then
        VERIFIED=true
        echo "✅ Verified via ready: $RULES_COUNT rules loaded"
        break
      fi
    fi
    sleep 2
  done

  kill $PF_PID 2>/dev/null || true
  trap - EXIT

  if [[ "$VERIFIED" == "false" ]]; then
    echo "⚠️ Could not verify rules count (API might still be initializing)"
  fi
fi

# Summary
if [[ "$VERIFY_ONLY" == "true" ]]; then
  echo "🔍 Verification complete"
elif [[ "$DRY_RUN" == "true" ]]; then
  echo "📋 Dry run complete - no changes made"
else
  echo "🎉 A-SWARM detection rules successfully updated!"
fi

# Exit with appropriate code
[[ "${VERIFIED:-false}" == "true" || "$DRY_RUN" == "true" ]] && exit 0 || exit 1