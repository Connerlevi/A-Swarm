#!/usr/bin/env bash
# Generate development certificates for A-SWARM components
# Workaround for Docker Desktop container creation issues
set -euo pipefail

NAMESPACE="${NAMESPACE:-aswarm}"
CERT_DIR="${CERT_DIR:-./certs}"

echo "=== A-SWARM Development Certificate Generation ==="

# Create cert directory
mkdir -p "$CERT_DIR"
cd "$CERT_DIR"

# Check if openssl is available
command -v openssl >/dev/null || {
    echo "ERROR: openssl not found. Please install OpenSSL."
    exit 1
}

# --- CA with proper v3 extensions ---
cat > ca.conf <<'EOF'
[req]
distinguished_name = dn
x509_extensions = v3_ca
prompt = no
[dn]
CN = A-SWARM Dev CA
O = A-SWARM Security
[v3_ca]
basicConstraints = critical, CA:true
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer
EOF

echo "1. Generating CA certificate..."
openssl genrsa -out ca-key.pem 4096
openssl req -new -x509 -days 365 -key ca-key.pem -out ca-cert.pem -config ca.conf

gen_cert() {
    component="$1"
    spiffe_id="spiffe://aswarm.local/ns/${NAMESPACE}/sa/aswarm-${component}"

    cat > "${component}.conf" <<EOF
[req]
distinguished_name = dn
req_extensions = v3_req
prompt = no
[dn]
CN = ${component}.aswarm.svc.cluster.local
O = A-SWARM Security
OU = A-SWARM Protocol V4
[v3_req]
basicConstraints = CA:false
keyUsage = critical, digitalSignature, keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth, clientAuth
subjectAltName = @alt
[alt]
DNS.1 = ${component}
DNS.2 = ${component}.aswarm
DNS.3 = ${component}.aswarm.svc
DNS.4 = ${component}.aswarm.svc.cluster.local
URI.1 = ${spiffe_id}
EOF

    echo "2. Generating leaf certificate for ${component} (${spiffe_id})..."
    openssl genrsa -out ${component}-key.pem 2048
    openssl req -new -key ${component}-key.pem -out ${component}.csr -config ${component}.conf
    openssl x509 -req -days 365 -in ${component}.csr \
        -CA ca-cert.pem -CAkey ca-key.pem -CAcreateserial \
        -out ${component}-cert.pem -extensions v3_req -extfile ${component}.conf

    echo "   ✓ Generated: ${component}-cert.pem, ${component}-key.pem"
}

# Generate certificates for all components
gen_cert "pheromone"
gen_cert "sentinel"
gen_cert "redswarm" 
gen_cert "blueswarm"

echo "3. Creating Kubernetes secrets..."

# Ensure namespace exists
kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || {
    echo "   Creating namespace: $NAMESPACE"
    kubectl create namespace "$NAMESPACE"
}

# Idempotent secret creation
create_secret() {
    local component="$1"
    echo "   Creating secret: ${component}-tls"
    kubectl create secret tls ${component}-tls \
        --cert=${component}-cert.pem --key=${component}-key.pem \
        -n "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
}

create_secret "pheromone"
create_secret "sentinel"
create_secret "redswarm"
create_secret "blueswarm"

# CA secret (for peer validation)
echo "   Creating CA secret: aswarm-ca"
kubectl create secret generic aswarm-ca \
    --from-file=ca.crt=ca-cert.pem -n "$NAMESPACE" \
    --dry-run=client -o yaml | kubectl apply -f -

echo ""
echo "=== Certificate Generation Complete ==="
echo "Generated certificates in: $(pwd)"
echo ""
echo "Secrets created:"
kubectl get secrets -n "$NAMESPACE" | grep -E 'NAME|tls|aswarm-ca'
echo ""
echo "To verify SPIFFE IDs in certificates:"
echo "  openssl x509 -in pheromone-cert.pem -text -noout | grep -A 10 'Subject Alternative Name'"
echo ""
echo "Environment variables for components:"
echo "  ASWARM_CERT_PATH=/etc/aswarm/tls/tls.crt"
echo "  ASWARM_KEY_PATH=/etc/aswarm/tls/tls.key"
echo "  ASWARM_CA_PATH=/etc/aswarm/ca/ca.crt"
echo "  ASWARM_REQUIRE_IDENTITY=true"