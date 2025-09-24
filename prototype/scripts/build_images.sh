#!/bin/bash
# Build A-SWARM images locally with signing and SBOM generation

set -euo pipefail

# Configuration
REGISTRY="${REGISTRY:-localhost:5000}"
REPOSITORY="${REPOSITORY:-aswarm}"
VERSION="${VERSION:-dev-$(git rev-parse --short HEAD 2>/dev/null || echo 'local')}"
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
VCS_REF=$(git rev-parse HEAD 2>/dev/null || echo 'unknown')
COMPONENTS=("sentinel" "pheromone" "microact")
SIGN_IMAGES="${SIGN_IMAGES:-true}"
GENERATE_SBOM="${GENERATE_SBOM:-true}"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${BLUE}[BUILD]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }

check_tools() {
    log "Checking required tools..."
    
    if ! command -v docker &> /dev/null; then
        echo "Error: docker is required"
        exit 1
    fi
    
    if [[ "$SIGN_IMAGES" == "true" ]] && ! command -v cosign &> /dev/null; then
        warning "cosign not found, skipping image signing"
        SIGN_IMAGES="false"
    fi
    
    if [[ "$GENERATE_SBOM" == "true" ]] && ! command -v syft &> /dev/null; then
        warning "syft not found, skipping SBOM generation"
        GENERATE_SBOM="false"
    fi
    
    success "Tool check completed"
}

build_component() {
    local component="$1"
    local image_tag="${REGISTRY}/${REPOSITORY}-${component}:${VERSION}"
    
    log "Building ${component} image..."
    log "Tag: ${image_tag}"
    
    # Build the image
    docker build \
        --file "docker/Dockerfile.${component}" \
        --tag "${image_tag}" \
        --build-arg BUILD_DATE="${BUILD_DATE}" \
        --build-arg VCS_REF="${VCS_REF}" \
        --build-arg VERSION="${VERSION}" \
        --label "org.opencontainers.image.created=${BUILD_DATE}" \
        --label "org.opencontainers.image.revision=${VCS_REF}" \
        --label "org.opencontainers.image.version=${VERSION}" \
        --label "org.opencontainers.image.title=A-SWARM ${component^}" \
        --label "org.opencontainers.image.description=Autonomic defense ${component}" \
        --label "org.opencontainers.image.vendor=A-SWARM Project" \
        --label "org.opencontainers.image.licenses=MIT" \
        --label "org.opencontainers.image.source=https://github.com/anthropics/aswarm" \
        .
    
    success "Built ${component} image: ${image_tag}"
    
    # Generate SBOM if requested
    if [[ "$GENERATE_SBOM" == "true" ]]; then
        log "Generating SBOM for ${component}..."
        mkdir -p "./sboms"
        
        # Generate multiple SBOM formats
        syft "${image_tag}" -o spdx-json --file "./sboms/${component}-sbom-spdx.json"
        syft "${image_tag}" -o cyclonedx-json --file "./sboms/${component}-sbom-cyclonedx.json"
        syft "${image_tag}" -o syft-json --file "./sboms/${component}-sbom-syft.json"
        
        success "Generated SBOMs for ${component}"
    fi
    
    # Sign image if requested and tools available
    if [[ "$SIGN_IMAGES" == "true" ]]; then
        log "Signing ${component} image..."
        
        # Check if we have signing keys or use keyless
        if [[ -f "${HOME}/.cosign/cosign.key" ]]; then
            # Use local key
            cosign sign --key "${HOME}/.cosign/cosign.key" "${image_tag}"
        else
            # Use keyless signing (requires OIDC)
            warning "No local cosign key found. Use 'cosign generate-key-pair' to create one."
            log "Skipping signing for ${component} (use CI/CD for keyless OIDC signing)"
        fi
    fi
    
    echo "${image_tag}" >> "./build-output.txt"
}

push_images() {
    log "Pushing images to registry..."
    
    while IFS= read -r image_tag; do
        log "Pushing ${image_tag}..."
        if docker push "${image_tag}"; then
            success "Pushed ${image_tag}"
        else
            warning "Failed to push ${image_tag} (registry may not be accessible)"
        fi
    done < "./build-output.txt"
}

generate_build_manifest() {
    log "Generating build manifest..."
    
    cat << EOF > "build-manifest.json"
{
  "build_info": {
    "timestamp": "${BUILD_DATE}",
    "version": "${VERSION}",
    "vcs_ref": "${VCS_REF}",
    "registry": "${REGISTRY}",
    "repository": "${REPOSITORY}"
  },
  "images": [
$(while IFS= read -r image_tag; do
    echo "    \"${image_tag}\""
    [[ $(wc -l < "./build-output.txt") -gt 1 ]] && echo ","
done < "./build-output.txt" | sed '$s/,$//')
  ],
  "security": {
    "signed": ${SIGN_IMAGES},
    "sbom_generated": ${GENERATE_SBOM},
    "vulnerability_scanned": false
  }
}
EOF
    
    success "Build manifest: build-manifest.json"
}

main() {
    log "A-SWARM Image Builder"
    log "===================="
    log ""
    log "Registry: ${REGISTRY}"
    log "Repository: ${REPOSITORY}"
    log "Version: ${VERSION}"
    log "Sign images: ${SIGN_IMAGES}"
    log "Generate SBOMs: ${GENERATE_SBOM}"
    log ""
    
    check_tools
    
    # Clear previous build output
    rm -f "./build-output.txt"
    
    # Build all components
    for component in "${COMPONENTS[@]}"; do
        build_component "${component}"
        log ""
    done
    
    # Generate manifest
    generate_build_manifest
    
    # Optionally push images
    if [[ "${PUSH_IMAGES:-false}" == "true" ]]; then
        push_images
    else
        log "Images built locally. Set PUSH_IMAGES=true to push to registry."
    fi
    
    success "Build completed! 🎉"
    log ""
    log "Next steps:"
    log "1. Test images: docker run ${REGISTRY}/${REPOSITORY}-sentinel:${VERSION} --help"
    log "2. Deploy with Helm: helm install aswarm ./helm/aswarm --set global.imageRegistry=${REGISTRY}"
    log "3. Verify: ./scripts/verify_images.sh --version ${VERSION} --registry ${REGISTRY}"
}

# Handle command line arguments
case "${1:-}" in
    --help|-h)
        cat << EOF
A-SWARM Image Builder

Usage: $0 [OPTIONS]

Options:
  --help, -h          Show this help message
  --push              Push images after building
  --no-sign           Skip image signing
  --no-sbom           Skip SBOM generation
  --version VERSION   Set image version (default: dev-<git-sha>)

Environment Variables:
  REGISTRY            Container registry (default: localhost:5000)
  REPOSITORY          Repository name (default: aswarm) 
  VERSION             Image version (default: dev-<git-sha>)
  SIGN_IMAGES         Sign images with cosign (default: true)
  GENERATE_SBOM       Generate SBOMs with syft (default: true)
  PUSH_IMAGES         Push to registry (default: false)

Examples:
  $0                                    # Build all images locally
  $0 --push                            # Build and push to registry
  REGISTRY=ghcr.io/myorg $0 --push     # Build for specific registry
  VERSION=v1.0.0 $0 --no-sign          # Build without signing
EOF
        exit 0
        ;;
    --push)
        export PUSH_IMAGES=true
        ;;
    --no-sign)
        export SIGN_IMAGES=false
        ;;
    --no-sbom)
        export GENERATE_SBOM=false
        ;;
    --version)
        export VERSION="$2"
        shift
        ;;
esac

main "$@"