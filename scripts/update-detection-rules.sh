#!/usr/bin/env bash
# A-SWARM Detection Rules Update Script
# - Updates ConfigMap
# - Computes checksum from cluster copy
# - Patches Deployment annotation to trigger rollout
# - Verifies rules via /ready

set -euo pipefail

NAMESPACE="${NAMESPACE:-aswarm}"
CONFIGMAP="${CONFIGMAP:-aswarm-detections}"
DEPLOYMENT="${DEPLOYMENT:-aswarm-blue-api}"
SVC_NAME="${SVC_NAME:-aswarm-blue-api}"
RULES_FILE="${1:-/tmp/aswarm-detection-rules.json}"
READY_PATH="${READY_PATH:-/ready}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-300s}"
VERIFY_TIMEOUT_SEC="${VERIFY_TIMEOUT_SEC:-60}"

# --- helpers ---
need() { command -v "$1" >/dev/null 2>&1 || { echo "❌ Missing dependency: $1" >&2; exit 1; }; }
hash_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum | awk '{print $1}';
  else shasum -a 256 | awk '{print $1}'; fi
}
random_port() {
  # portable fallback if shuf not available
  if command -v shuf >/dev/null 2>&1; then shuf -i 10000-65000 -n 1; else echo 18080; fi
}

echo "🔄 Updating A-SWARM detection rules..."
need kubectl; need curl
# jq is optional but recommended
command -v jq >/dev/null 2>&1 || echo "⚠️ jq not found - some features limited"

# Validate rules file
if [[ ! -f "$RULES_FILE" ]]; then echo "❌ Rules file not found: $RULES_FILE"; exit 1; fi
if command -v jq >/dev/null 2>&1; then
  jq . "$RULES_FILE" >/dev/null || { echo "❌ Invalid JSON: $RULES_FILE"; exit 1; }
else
  # Fallback: use Python if available
  python3 -m json.tool "$RULES_FILE" >/dev/null 2>&1 || { echo "⚠️ Cannot validate JSON (install jq)"; }
fi
echo "✅ Rules file validated: $RULES_FILE"

# Upsert ConfigMap
echo "📝 Applying ConfigMap $CONFIGMAP..."
kubectl create configmap "$CONFIGMAP" \
  --from-file=detection-rules.json="$RULES_FILE" \
  -n "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# Compute checksum from stored CM data (strip CR in case of CRLF)
echo "🔢 Calculating checksum from stored ConfigMap..."
CHKSUM="$(kubectl get cm -n "$NAMESPACE" "$CONFIGMAP" -o jsonpath='{.data.detection-rules\.json}' \
        | tr -d '\r' | hash_stdin)"
echo "📌 New checksum: sha256:$CHKSUM"

# Skip rollout if unchanged
OLD="$(kubectl get deploy -n "$NAMESPACE" "$DEPLOYMENT" \
      -o jsonpath='{.spec.template.metadata.annotations.aswarm\.ai/content-checksum}' 2>/dev/null || true)"
if [[ "sha256:$CHKSUM" == "$OLD" ]]; then
  echo "ℹ️ Checksum unchanged; no rollout required."
else
  # Annotate both Deployment (to trigger rollout) and ConfigMap (for traceability)
  echo "🚀 Triggering rollout..."
  kubectl -n "$NAMESPACE" patch deploy "$DEPLOYMENT" \
    -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{
      \"aswarm.ai/content-checksum\":\"sha256:${CHKSUM}\",
      \"aswarm.ai/last-rules-update\":\"$(date -Iseconds)\"
    }}}}}"

  kubectl -n "$NAMESPACE" annotate cm "$CONFIGMAP" \
    "aswarm.ai/content-checksum=sha256:${CHKSUM}" --overwrite
fi

# Wait for rollout
echo "⏳ Waiting for rollout to complete..."
kubectl rollout status deploy/"$DEPLOYMENT" -n "$NAMESPACE" --timeout="$ROLLOUT_TIMEOUT"

# Ensure service has endpoints before curling
echo "⏳ Waiting for service endpoints..."
kubectl -n "$NAMESPACE" wait --for=jsonpath='{.subsets[0].addresses[0].ip}' \
  "endpoints/${SVC_NAME}" --timeout=60s >/dev/null 2>&1 || true

# Temporary port-forward for verification
PORT="$(random_port)"
echo "🔎 Verifying via $SVC_NAME on localhost:$PORT$READY_PATH ..."
kubectl -n "$NAMESPACE" port-forward "svc/$SVC_NAME" "$PORT:8080" --address 127.0.0.1 >/dev/null 2>&1 &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null || true' EXIT

# Poll readiness for rules count
deadline=$(( $(date +%s) + VERIFY_TIMEOUT_SEC ))
RULES_COUNT=0
while [[ $(date +%s) -lt $deadline ]]; do
  if out="$(curl -fsS "http://127.0.0.1:$PORT$READY_PATH" 2>/dev/null)"; then
    if command -v jq >/dev/null 2>&1; then
      RULES_COUNT="$(printf '%s' "$out" | jq -r '.rules_loaded // 0' 2>/dev/null || echo 0)"
    else
      # Fallback: grep for rules_loaded
      RULES_COUNT="$(printf '%s' "$out" | grep -o '"rules_loaded":[0-9]*' | grep -o '[0-9]*$' || echo 0)"
    fi
    [[ "$RULES_COUNT" =~ ^[0-9]+$ ]] || RULES_COUNT=0
    if (( RULES_COUNT >= 0 )); then break; fi
  fi
  sleep 2
done

kill $PF_PID 2>/dev/null || true
trap - EXIT

if (( RULES_COUNT > 0 )); then
  echo "✅ Detection rules update complete: $RULES_COUNT rules loaded"
else
  echo "⚠️ Could not verify rules count (API might still be initializing)."
fi

echo "🎉 A-SWARM detection rules successfully updated!"