#!/bin/bash
# A-SWARM Image Verification Script
# Verifies cosign signatures and SBOMs for all A-SWARM images

set -euo pipefail

# Configuration
REGISTRY="${REGISTRY:-ghcr.io}"
REPOSITORY="${REPOSITORY:-anthropics/aswarm}"
VERSION="${VERSION:-latest}"
COMPONENTS=("sentinel" "pheromone" "microact")

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Certificate identity patterns for verification
CERT_IDENTITY_REGEXP="https://github.com/${REPOSITORY#*/}"
CERT_OIDC_ISSUER="https://token.actions.githubusercontent.com"

log() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_dependencies() {
    log "Checking dependencies..."
    
    if ! command -v cosign &> /dev/null; then
        error "cosign is required but not installed. Install from: https://docs.sigstore.dev/cosign/installation/"
        exit 1
    fi
    
    if ! command -v docker &> /dev/null && ! command -v podman &> /dev/null; then
        warning "Neither docker nor podman found. Image pulling may fail."
    fi
    
    success "Dependencies check passed"
}

verify_image_signature() {
    local image_ref="$1"
    local component="$2"
    
    log "Verifying signature for ${component}..."
    
    if cosign verify "${image_ref}" \
        --certificate-identity-regexp="${CERT_IDENTITY_REGEXP}" \
        --certificate-oidc-issuer="${CERT_OIDC_ISSUER}" \
        --output text > /tmp/cosign_verify_${component}.out 2>&1; then
        success "✅ Signature verified for ${component}"
        return 0
    else
        error "❌ Signature verification failed for ${component}"
        cat /tmp/cosign_verify_${component}.out
        return 1
    fi
}

verify_sbom_attestation() {
    local image_ref="$1"
    local component="$2"
    
    log "Verifying SBOM attestation for ${component}..."
    
    # Verify SPDX SBOM attestation
    if cosign verify-attestation "${image_ref}" \
        --type spdxjson \
        --certificate-identity-regexp="${CERT_IDENTITY_REGEXP}" \
        --certificate-oidc-issuer="${CERT_OIDC_ISSUER}" \
        --output text > /tmp/cosign_sbom_${component}.out 2>&1; then
        success "✅ SPDX SBOM attestation verified for ${component}"
    else
        warning "⚠️  SPDX SBOM attestation verification failed for ${component}"
        cat /tmp/cosign_sbom_${component}.out
    fi
    
    # Verify CycloneDX SBOM attestation
    if cosign verify-attestation "${image_ref}" \
        --type cyclonedx \
        --certificate-identity-regexp="${CERT_IDENTITY_REGEXP}" \
        --certificate-oidc-issuer="${CERT_OIDC_ISSUER}" \
        --output text > /tmp/cosign_cyclonedx_${component}.out 2>&1; then
        success "✅ CycloneDX SBOM attestation verified for ${component}"
    else
        warning "⚠️  CycloneDX SBOM attestation verification failed for ${component}"
    fi
}

download_sbom() {
    local image_ref="$1"
    local component="$2"
    local output_dir="${3:-./sboms}"
    
    log "Downloading SBOM for ${component}..."
    
    mkdir -p "${output_dir}"
    
    # Download SPDX SBOM
    if cosign download attestation "${image_ref}" \
        --predicate-type=https://spdx.dev/Document \
        --output-file="${output_dir}/${component}-sbom-spdx.json" 2>/dev/null; then
        success "Downloaded SPDX SBOM: ${output_dir}/${component}-sbom-spdx.json"
    else
        warning "Failed to download SPDX SBOM for ${component}"
    fi
    
    # Download CycloneDX SBOM
    if cosign download attestation "${image_ref}" \
        --predicate-type=https://cyclonedx.org/bom \
        --output-file="${output_dir}/${component}-sbom-cyclonedx.json" 2>/dev/null; then
        success "Downloaded CycloneDX SBOM: ${output_dir}/${component}-sbom-cyclonedx.json"
    else
        warning "Failed to download CycloneDX SBOM for ${component}"
    fi
}

generate_verification_report() {
    local output_file="${1:-verification-report.md}"
    
    log "Generating verification report..."
    
    cat << EOF > "${output_file}"
# A-SWARM Image Verification Report

**Generated**: $(date -u +"%Y-%m-%d %H:%M:%S UTC")  
**Registry**: ${REGISTRY}  
**Repository**: ${REPOSITORY}  
**Version**: ${VERSION}  
**Verified By**: $(whoami)@$(hostname)  

## Verification Results

EOF

    local all_verified=true
    
    for component in "${COMPONENTS[@]}"; do
        local image_ref="${REGISTRY}/${REPOSITORY}-${component}:${VERSION}"
        
        echo "### ${component}" >> "${output_file}"
        echo "" >> "${output_file}"
        echo "**Image**: \`${image_ref}\`" >> "${output_file}"
        echo "" >> "${output_file}"
        
        # Check signature
        if cosign verify "${image_ref}" \
            --certificate-identity-regexp="${CERT_IDENTITY_REGEXP}" \
            --certificate-oidc-issuer="${CERT_OIDC_ISSUER}" \
            --output text &>/dev/null; then
            echo "- ✅ Signature verified" >> "${output_file}"
        else
            echo "- ❌ Signature verification failed" >> "${output_file}"
            all_verified=false
        fi
        
        # Check SBOM
        if cosign verify-attestation "${image_ref}" \
            --type spdxjson \
            --certificate-identity-regexp="${CERT_IDENTITY_REGEXP}" \
            --certificate-oidc-issuer="${CERT_OIDC_ISSUER}" \
            --output text &>/dev/null; then
            echo "- ✅ SBOM attestation verified" >> "${output_file}"
        else
            echo "- ⚠️  SBOM attestation not found or invalid" >> "${output_file}"
        fi
        
        echo "" >> "${output_file}"
    done
    
    echo "## Overall Status" >> "${output_file}"
    echo "" >> "${output_file}"
    
    if [ "$all_verified" = true ]; then
        echo "🎉 **All images successfully verified!**" >> "${output_file}"
        echo "" >> "${output_file}"
        echo "These A-SWARM images can be trusted for deployment." >> "${output_file}"
    else
        echo "⚠️  **Some verifications failed!**" >> "${output_file}"
        echo "" >> "${output_file}"
        echo "Please review the failures above before deploying." >> "${output_file}"
    fi
    
    echo "" >> "${output_file}"
    echo "## Manual Verification Commands" >> "${output_file}"
    echo "" >> "${output_file}"
    echo "\`\`\`bash" >> "${output_file}"
    for component in "${COMPONENTS[@]}"; do
        echo "# Verify ${component}" >> "${output_file}"
        echo "cosign verify ${REGISTRY}/${REPOSITORY}-${component}:${VERSION} \\" >> "${output_file}"
        echo "  --certificate-identity-regexp=\"${CERT_IDENTITY_REGEXP}\" \\" >> "${output_file}"
        echo "  --certificate-oidc-issuer=\"${CERT_OIDC_ISSUER}\"" >> "${output_file}"
        echo "" >> "${output_file}"
    done
    echo "\`\`\`" >> "${output_file}"
    
    success "Verification report generated: ${output_file}"
}

main() {
    log "A-SWARM Image Verification"
    log "=========================="
    log ""
    log "Registry: ${REGISTRY}"
    log "Repository: ${REPOSITORY}"  
    log "Version: ${VERSION}"
    log ""
    
    check_dependencies
    
    local verification_failed=false
    
    for component in "${COMPONENTS[@]}"; do
        local image_ref="${REGISTRY}/${REPOSITORY}-${component}:${VERSION}"
        
        log "Verifying component: ${component}"
        log "Image: ${image_ref}"
        
        if ! verify_image_signature "${image_ref}" "${component}"; then
            verification_failed=true
            continue
        fi
        
        verify_sbom_attestation "${image_ref}" "${component}"
        
        # Download SBOMs if requested
        if [[ "${DOWNLOAD_SBOMS:-false}" == "true" ]]; then
            download_sbom "${image_ref}" "${component}" "${SBOM_OUTPUT_DIR:-./sboms}"
        fi
        
        log ""
    done
    
    # Generate report
    generate_verification_report
    
    if [ "$verification_failed" = true ]; then
        error "Some image verifications failed!"
        exit 1
    else
        success "All A-SWARM images verified successfully! 🎉"
        log ""
        log "These images are signed and can be trusted for deployment."
        log "Use 'helm install aswarm ./helm/aswarm' to deploy."
    fi
}

# Handle command line arguments
case "${1:-}" in
    --help|-h)
        cat << EOF
A-SWARM Image Verification Script

Usage: $0 [OPTIONS]

Options:
  --help, -h          Show this help message
  --download-sboms    Download SBOMs to ./sboms directory
  --version VERSION   Specify image version (default: latest)
  --registry REG      Specify registry (default: ghcr.io)
  --repo REPO         Specify repository (default: anthropics/aswarm)

Environment Variables:
  REGISTRY            Container registry (default: ghcr.io)
  REPOSITORY          Repository name (default: anthropics/aswarm)
  VERSION             Image version (default: latest)
  DOWNLOAD_SBOMS      Set to 'true' to download SBOMs
  SBOM_OUTPUT_DIR     Directory for SBOM downloads (default: ./sboms)

Examples:
  $0                                    # Verify latest images
  $0 --version v1.0.0                 # Verify specific version
  $0 --download-sboms                  # Verify and download SBOMs
  VERSION=v1.0.0 DOWNLOAD_SBOMS=true $0  # Via environment variables
EOF
        exit 0
        ;;
    --download-sboms)
        export DOWNLOAD_SBOMS=true
        ;;
    --version)
        export VERSION="$2"
        shift
        ;;
    --registry)
        export REGISTRY="$2"
        shift
        ;;
    --repo)
        export REPOSITORY="$2"
        shift
        ;;
esac

main "$@"