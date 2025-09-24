#!/bin/bash
# A-SWARM Security Headers and TLS Validation Script
# Validates security hardening configurations and TLS setup

set -euo pipefail

# Configuration
DOMAIN="${DOMAIN:-aswarm.local}"
API_URL="${API_URL:-https://$DOMAIN}"
HTTP_URL="${HTTP_URL:-http://localhost:8000}"
NGINX_CONFIG="${NGINX_CONFIG:-nginx/aswarm.conf}"

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

# Function to test HTTP header
test_header() {
    local url="$1"
    local header_name="$2"
    local expected_pattern="$3"
    local description="$4"

    log "Testing $description..."

    local response
    response=$(curl -s -I "$url" 2>/dev/null || echo "")

    if [ -z "$response" ]; then
        error "$description: Unable to connect to $url"
        return 1
    fi

    local header_value
    header_value=$(echo "$response" | grep -i "^$header_name:" | cut -d' ' -f2- | tr -d '\r\n' || echo "")

    if [ -z "$header_value" ]; then
        error "$description: Header '$header_name' not found"
        return 1
    fi

    if echo "$header_value" | grep -qE "$expected_pattern"; then
        success "$description: $header_value"
        return 0
    else
        error "$description: Header value '$header_value' does not match expected pattern '$expected_pattern'"
        return 1
    fi
}

# Function to test TLS certificate
test_tls_certificate() {
    local domain="$1"
    local port="${2:-443}"

    log "Testing TLS certificate for $domain:$port..."

    # Check if we can connect
    if ! timeout 5 openssl s_client -connect "$domain:$port" -servername "$domain" </dev/null >/dev/null 2>&1; then
        error "TLS: Cannot connect to $domain:$port"
        return 1
    fi

    # Get certificate information
    local cert_info
    cert_info=$(timeout 5 openssl s_client -connect "$domain:$port" -servername "$domain" </dev/null 2>/dev/null | openssl x509 -noout -text 2>/dev/null || echo "")

    if [ -z "$cert_info" ]; then
        error "TLS: Cannot retrieve certificate information"
        return 1
    fi

    # Check certificate validity
    local not_before not_after
    not_before=$(echo "$cert_info" | grep "Not Before" | cut -d':' -f2- | xargs)
    not_after=$(echo "$cert_info" | grep "Not After" | cut -d':' -f2- | xargs)

    success "TLS Certificate valid from: $not_before"
    success "TLS Certificate valid until: $not_after"

    # Check if certificate is about to expire (within 30 days)
    local expiry_epoch
    expiry_epoch=$(date -d "$not_after" +%s 2>/dev/null || echo "0")
    local current_epoch
    current_epoch=$(date +%s)
    local days_until_expiry
    days_until_expiry=$(( (expiry_epoch - current_epoch) / 86400 ))

    if [ "$days_until_expiry" -lt 30 ]; then
        warning "TLS Certificate expires in $days_until_expiry days"
    else
        success "TLS Certificate expires in $days_until_expiry days"
    fi

    # Check certificate algorithm
    local key_algorithm
    key_algorithm=$(echo "$cert_info" | grep "Public Key Algorithm" | cut -d':' -f2 | xargs)

    if echo "$key_algorithm" | grep -q "rsaEncryption"; then
        local key_size
        key_size=$(echo "$cert_info" | grep "RSA Public-Key" | grep -o '[0-9]\+' | head -1)
        if [ "${key_size:-0}" -ge 2048 ]; then
            success "TLS Key Algorithm: RSA $key_size-bit (secure)"
        else
            warning "TLS Key Algorithm: RSA $key_size-bit (consider upgrading to 2048+ bit)"
        fi
    elif echo "$key_algorithm" | grep -q "id-ecPublicKey"; then
        success "TLS Key Algorithm: ECDSA (secure)"
    else
        warning "TLS Key Algorithm: $key_algorithm (verify security)"
    fi

    # Check certificate chain
    local cert_chain
    cert_chain=$(timeout 5 openssl s_client -connect "$domain:$port" -servername "$domain" -showcerts </dev/null 2>/dev/null | grep -c "BEGIN CERTIFICATE" || echo "0")

    if [ "$cert_chain" -gt 1 ]; then
        success "TLS Certificate chain complete ($cert_chain certificates)"
    else
        warning "TLS Certificate chain incomplete ($cert_chain certificates)"
    fi

    return 0
}

# Function to test TLS configuration
test_tls_configuration() {
    local domain="$1"
    local port="${2:-443}"

    log "Testing TLS configuration for $domain:$port..."

    # Test TLS versions
    local tls_versions=("ssl3" "tls1" "tls1_1" "tls1_2" "tls1_3")
    local supported_versions=()
    local secure_versions=()

    for version in "${tls_versions[@]}"; do
        if timeout 3 openssl s_client -connect "$domain:$port" -"$version" </dev/null >/dev/null 2>&1; then
            supported_versions+=("$version")

            # TLS 1.2 and 1.3 are considered secure
            if [[ "$version" == "tls1_2" || "$version" == "tls1_3" ]]; then
                secure_versions+=("$version")
            fi
        fi
    done

    if [ ${#secure_versions[@]} -gt 0 ]; then
        success "TLS Secure versions supported: ${secure_versions[*]}"
    else
        error "TLS No secure versions (TLS 1.2/1.3) supported"
        return 1
    fi

    # Check for insecure versions
    local insecure_versions=()
    for version in "${supported_versions[@]}"; do
        if [[ "$version" != "tls1_2" && "$version" != "tls1_3" ]]; then
            insecure_versions+=("$version")
        fi
    done

    if [ ${#insecure_versions[@]} -gt 0 ]; then
        warning "TLS Insecure versions enabled: ${insecure_versions[*]} (consider disabling)"
    else
        success "TLS No insecure versions enabled"
    fi

    # Test cipher suites
    local cipher_info
    cipher_info=$(timeout 5 openssl s_client -connect "$domain:$port" -cipher 'HIGH:!aNULL:!eNULL:!EXPORT:!DES:!RC4:!MD5:!PSK:!SRP:!CAMELLIA' </dev/null 2>/dev/null | grep "Cipher" | head -1 || echo "")

    if [ -n "$cipher_info" ]; then
        success "TLS Strong cipher suite negotiated: $cipher_info"
    else
        warning "TLS Could not verify cipher suite strength"
    fi

    return 0
}

# Function to test security headers
test_security_headers() {
    local url="$1"

    log "Testing security headers for $url..."

    local headers_passed=0
    local headers_total=8

    # Content Security Policy (CSP)
    if test_header "$url" "content-security-policy" "." "Content Security Policy"; then
        headers_passed=$((headers_passed + 1))
    fi

    # HTTP Strict Transport Security (HSTS)
    if test_header "$url" "strict-transport-security" "max-age=" "HTTP Strict Transport Security"; then
        headers_passed=$((headers_passed + 1))
    fi

    # X-Frame-Options
    if test_header "$url" "x-frame-options" "(DENY|SAMEORIGIN)" "X-Frame-Options"; then
        headers_passed=$((headers_passed + 1))
    fi

    # X-Content-Type-Options
    if test_header "$url" "x-content-type-options" "nosniff" "X-Content-Type-Options"; then
        headers_passed=$((headers_passed + 1))
    fi

    # X-XSS-Protection
    if test_header "$url" "x-xss-protection" "1" "X-XSS-Protection"; then
        headers_passed=$((headers_passed + 1))
    fi

    # Referrer-Policy
    if test_header "$url" "referrer-policy" "." "Referrer Policy"; then
        headers_passed=$((headers_passed + 1))
    fi

    # Permissions-Policy (formerly Feature-Policy)
    if test_header "$url" "permissions-policy" "." "Permissions Policy"; then
        headers_passed=$((headers_passed + 1))
    fi

    # Server header (should be minimal or absent)
    local server_header
    server_header=$(curl -s -I "$url" 2>/dev/null | grep -i "^server:" | cut -d' ' -f2- | tr -d '\r\n' || echo "")

    if [ -z "$server_header" ]; then
        success "Server header: Not disclosed (good)"
        headers_passed=$((headers_passed + 1))
    elif echo "$server_header" | grep -qE "^(nginx|Apache)$"; then
        success "Server header: Minimal disclosure ($server_header)"
        headers_passed=$((headers_passed + 1))
    else
        warning "Server header: Full disclosure ($server_header) - consider minimizing"
    fi

    log "Security headers score: $headers_passed/$headers_total"

    if [ $headers_passed -ge 6 ]; then
        success "Security headers validation passed ($headers_passed/$headers_total)"
        return 0
    else
        error "Security headers validation failed ($headers_passed/$headers_total) - minimum 6 required"
        return 1
    fi
}

# Function to test API security
test_api_security() {
    local api_url="$1"

    log "Testing API security for $api_url..."

    # Test CORS headers
    local cors_response
    cors_response=$(curl -s -H "Origin: https://evil.com" -H "Access-Control-Request-Method: POST" -X OPTIONS "$api_url/api/health" 2>/dev/null || echo "")

    if echo "$cors_response" | grep -q "Access-Control-Allow-Origin: \*"; then
        warning "CORS: Wildcard origin allowed - verify this is intentional"
    elif echo "$cors_response" | grep -q "Access-Control-Allow-Origin:"; then
        success "CORS: Specific origins configured"
    else
        success "CORS: No permissive CORS headers found"
    fi

    # Test authentication requirement
    local unauth_response
    unauth_response=$(curl -s -o /dev/null -w "%{http_code}" "$api_url/api/actions/run" 2>/dev/null || echo "000")

    if [ "$unauth_response" = "401" ] || [ "$unauth_response" = "403" ]; then
        success "API Authentication: Protected endpoints require authentication ($unauth_response)"
    else
        warning "API Authentication: Protected endpoint returned $unauth_response (expected 401/403)"
    fi

    # Test for sensitive information disclosure
    local health_response
    health_response=$(curl -s "$api_url/api/health" 2>/dev/null || echo "")

    if echo "$health_response" | grep -qE "(password|secret|key|token)" -i; then
        error "API Security: Health endpoint may be disclosing sensitive information"
        return 1
    else
        success "API Security: No sensitive information disclosed in health endpoint"
    fi

    return 0
}

# Function to validate NGINX configuration
validate_nginx_config() {
    local config_file="$1"

    log "Validating NGINX configuration: $config_file..."

    if [ ! -f "$config_file" ]; then
        error "NGINX config file not found: $config_file"
        return 1
    fi

    local config_issues=0

    # Check for SSL configuration
    if grep -q "ssl_certificate" "$config_file" && grep -q "ssl_certificate_key" "$config_file"; then
        success "NGINX: SSL certificates configured"
    else
        error "NGINX: SSL certificates not configured"
        config_issues=$((config_issues + 1))
    fi

    # Check for security headers
    local security_headers=("add_header X-Frame-Options" "add_header X-Content-Type-Options" "add_header X-XSS-Protection")

    for header in "${security_headers[@]}"; do
        if grep -q "$header" "$config_file"; then
            success "NGINX: $header configured"
        else
            warning "NGINX: $header not configured"
            config_issues=$((config_issues + 1))
        fi
    done

    # Check for HSTS
    if grep -q "add_header Strict-Transport-Security" "$config_file"; then
        success "NGINX: HSTS configured"
    else
        warning "NGINX: HSTS not configured"
        config_issues=$((config_issues + 1))
    fi

    # Check for secure TLS configuration
    if grep -q "ssl_protocols.*TLSv1.2\|ssl_protocols.*TLSv1.3" "$config_file"; then
        success "NGINX: Secure TLS protocols configured"
    else
        warning "NGINX: TLS protocol configuration not found or insecure"
        config_issues=$((config_issues + 1))
    fi

    # Check for cipher configuration
    if grep -q "ssl_ciphers" "$config_file"; then
        success "NGINX: SSL cipher suite configured"
    else
        warning "NGINX: SSL cipher suite not explicitly configured"
        config_issues=$((config_issues + 1))
    fi

    if [ $config_issues -eq 0 ]; then
        success "NGINX configuration validation passed"
        return 0
    elif [ $config_issues -le 2 ]; then
        warning "NGINX configuration has minor issues ($config_issues)"
        return 0
    else
        error "NGINX configuration has significant issues ($config_issues)"
        return 1
    fi
}

# Function to generate security report
generate_security_report() {
    local domain="$1"
    local api_url="$2"
    local report_file="security_validation_report_$(date +%Y%m%d_%H%M%S).json"

    log "Generating security report: $report_file..."

    cat > "$report_file" << EOF
{
    "security_validation_report": {
        "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)",
        "domain": "$domain",
        "api_url": "$api_url",
        "tests_performed": {
            "tls_certificate": "$(test_tls_certificate "$domain" 443 >/dev/null 2>&1 && echo "PASS" || echo "FAIL")",
            "tls_configuration": "$(test_tls_configuration "$domain" 443 >/dev/null 2>&1 && echo "PASS" || echo "FAIL")",
            "security_headers": "$(test_security_headers "$api_url" >/dev/null 2>&1 && echo "PASS" || echo "FAIL")",
            "api_security": "$(test_api_security "$api_url" >/dev/null 2>&1 && echo "PASS" || echo "FAIL")",
            "nginx_config": "$(validate_nginx_config "$NGINX_CONFIG" >/dev/null 2>&1 && echo "PASS" || echo "FAIL")"
        },
        "certificate_info": {
            "algorithm": "$(timeout 5 openssl s_client -connect "$domain:443" -servername "$domain" </dev/null 2>/dev/null | openssl x509 -noout -text 2>/dev/null | grep "Public Key Algorithm" | cut -d':' -f2 | xargs || echo "unknown")",
            "expiry": "$(timeout 5 openssl s_client -connect "$domain:443" -servername "$domain" </dev/null 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d'=' -f2 || echo "unknown")"
        },
        "recommendations": [
            "Ensure all security headers are configured",
            "Verify TLS 1.2+ is enforced",
            "Monitor certificate expiry dates",
            "Regular security scans recommended",
            "Keep NGINX and OpenSSL updated"
        ]
    }
}
EOF

    success "Security report generated: $report_file"
    echo "$report_file"
}

# Main validation function
main() {
    echo "==========================================="
    echo "A-SWARM Security Headers and TLS Validation"
    echo "Started: $(date)"
    echo "==========================================="

    local overall_success=true

    # Check prerequisites
    log "Checking prerequisites..."

    if ! command -v curl >/dev/null 2>&1; then
        error "curl is required but not installed"
        exit 1
    fi

    if ! command -v openssl >/dev/null 2>&1; then
        error "openssl is required but not installed"
        exit 1
    fi

    success "Prerequisites check passed"

    # Phase 1: NGINX Configuration Validation
    log "Phase 1: NGINX Configuration Validation"
    if ! validate_nginx_config "$NGINX_CONFIG"; then
        overall_success=false
    fi
    echo ""

    # Phase 2: TLS Certificate Testing
    log "Phase 2: TLS Certificate Testing"
    if ! test_tls_certificate "$(echo "$DOMAIN" | cut -d':' -f1)" 443; then
        overall_success=false
    fi
    echo ""

    # Phase 3: TLS Configuration Testing
    log "Phase 3: TLS Configuration Testing"
    if ! test_tls_configuration "$(echo "$DOMAIN" | cut -d':' -f1)" 443; then
        overall_success=false
    fi
    echo ""

    # Phase 4: Security Headers Testing
    log "Phase 4: Security Headers Testing"
    if ! test_security_headers "$API_URL"; then
        overall_success=false
    fi
    echo ""

    # Phase 5: API Security Testing
    log "Phase 5: API Security Testing"
    if ! test_api_security "$API_URL"; then
        overall_success=false
    fi
    echo ""

    # Generate report
    log "Phase 6: Generating Security Report"
    local report_file
    report_file=$(generate_security_report "$DOMAIN" "$API_URL")
    echo ""

    # Final summary
    echo "==========================================="
    if [ "$overall_success" = true ]; then
        success "Security Validation PASSED"
        echo "🔒 A-SWARM security configuration is properly hardened"
    else
        error "Security Validation FAILED"
        echo "🚨 A-SWARM security configuration needs attention"
    fi
    echo "==========================================="
    echo ""
    log "Detailed report saved to: $report_file"

    if [ "$overall_success" = true ]; then
        exit 0
    else
        exit 1
    fi
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --domain)
            DOMAIN="$2"
            shift 2
            ;;
        --api-url)
            API_URL="$2"
            shift 2
            ;;
        --nginx-config)
            NGINX_CONFIG="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --domain DOMAIN         Domain to test (default: aswarm.local)"
            echo "  --api-url URL           API URL to test (default: https://DOMAIN)"
            echo "  --nginx-config FILE     NGINX config file (default: nginx/aswarm.conf)"
            echo "  --help                  Show this help message"
            echo ""
            echo "Environment variables:"
            echo "  DOMAIN                  Same as --domain"
            echo "  API_URL                 Same as --api-url"
            echo "  NGINX_CONFIG           Same as --nginx-config"
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Update API_URL if only domain was provided
if [[ "$API_URL" == "https://aswarm.local" && "$DOMAIN" != "aswarm.local" ]]; then
    API_URL="https://$DOMAIN"
fi

# Script execution
if [ "${BASH_SOURCE[0]}" == "${0}" ]; then
    main "$@"
fi