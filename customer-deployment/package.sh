#!/bin/bash
set -euo pipefail

# A-SWARM Customer Deployment Package Creator
# Creates a complete, ready-to-deploy package for customers

# ---- Config --------------------------------------------------------------------
VERSION="${VERSION:-1.0.0}"
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
PACKAGE_NAME="aswarm-pilot-v${VERSION}-${TIMESTAMP}"

# Compute repo root (script may be invoked from anywhere)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Inputs (override via env)
WITH_IMAGES="${WITH_IMAGES:-false}"     # true|false for air-gap support
GPG_SIGN="${GPG_SIGN:-false}"           # true|false for signing
GPG_KEY="${GPG_KEY:-}"                  # GPG key ID or empty

echo "Creating A-SWARM Pilot Deployment Package v${VERSION}"
echo "================================================"
echo "Repository root: ${REPO_ROOT}"
[[ "${WITH_IMAGES}" == "true" ]] && echo "Mode: Including Docker images (air-gap)"
[[ "${GPG_SIGN}" == "true" ]] && echo "Mode: GPG signing enabled"

# ---- Sanity checks -------------------------------------------------------------
must_exist() {
    if [[ ! -e "$1" ]]; then
        echo "ERROR: Missing required file/directory: $1"
        exit 1
    fi
}

echo ""
echo "Verifying required files..."

must_exist "${REPO_ROOT}/customer-deployment/install"
must_exist "${REPO_ROOT}/customer-deployment/validation"
must_exist "${REPO_ROOT}/customer-deployment/Makefile"
must_exist "${REPO_ROOT}/customer-deployment/README.md"

# Optional but recommended
[[ -f "${REPO_ROOT}/index.html" ]] || echo "Note: index.html not found at repo root (skipping)"
[[ -d "${REPO_ROOT}/observability/grafana-dashboards" ]] || echo "Note: grafana-dashboards not found (skipping)"

# ---- Staging -------------------------------------------------------------------
rm -rf "${PACKAGE_NAME}"
mkdir -p "${PACKAGE_NAME}"

echo ""
echo "Collecting files..."

# Core deployment files
echo "  • Core deployment files"
cp -r "${REPO_ROOT}/customer-deployment/install"      "${PACKAGE_NAME}/"
cp -r "${REPO_ROOT}/customer-deployment/validation"   "${PACKAGE_NAME}/"
cp    "${REPO_ROOT}/customer-deployment/Makefile"    "${PACKAGE_NAME}/"
cp    "${REPO_ROOT}/customer-deployment/README.md"    "${PACKAGE_NAME}/"

# Documentation (if present)
echo "  • Documentation"
mkdir -p "${PACKAGE_NAME}/docs"
if [[ -d "${REPO_ROOT}/customer-deployment/docs" ]]; then
    cp -r "${REPO_ROOT}/customer-deployment/docs"/* "${PACKAGE_NAME}/docs/" 2>/dev/null || true
fi

# Top-level docs if present
for f in SYSTEM_TEST_PLAN.md AUTONOMY_IMPLEMENTATION.md PILOT_ACTION_PLAN.md CONTEXT.md WORKING_NOTES.md LICENSE CHANGELOG.md; do
    if [[ -f "${REPO_ROOT}/${f}" ]]; then
        cp "${REPO_ROOT}/${f}" "${PACKAGE_NAME}/docs/" 2>/dev/null || echo "    - ${f} not found (skipping)"
    fi
done

# Landing page
if [[ -f "${REPO_ROOT}/index.html" ]]; then
    echo "  • Landing page"
    mkdir -p "${PACKAGE_NAME}/assets/landing"
    cp "${REPO_ROOT}/index.html" "${PACKAGE_NAME}/assets/landing/"
fi

# Observability assets (Grafana dashboards)
if [[ -d "${REPO_ROOT}/observability/grafana-dashboards" ]]; then
    echo "  • Grafana dashboards"
    mkdir -p "${PACKAGE_NAME}/observability/grafana-dashboards"
    cp -r "${REPO_ROOT}/observability/grafana-dashboards"/*.json "${PACKAGE_NAME}/observability/grafana-dashboards/" 2>/dev/null || true
fi

# Include .env.template if not already copied
if [[ ! -f "${PACKAGE_NAME}/install/.env.template" ]] && [[ -f "${REPO_ROOT}/customer-deployment/install/.env.template" ]]; then
    echo "  • Environment template"
    cp "${REPO_ROOT}/customer-deployment/install/.env.template" "${PACKAGE_NAME}/install/"
fi

# VERSION file with provenance
echo "  • Version and provenance"
GIT_DESC="$(git -C "${REPO_ROOT}" describe --tags --always 2>/dev/null || echo 'unknown')"
GIT_SHA="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
GIT_BRANCH="$(git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')"

cat > "${PACKAGE_NAME}/VERSION" << EOF
A-SWARM Pilot Deployment Package
Version: ${VERSION}
Build Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')
Git Branch: ${GIT_BRANCH}
Git Tag/Commit: ${GIT_DESC}
Git SHA: ${GIT_SHA}
Build Host: $(hostname)
Build User: ${USER}
EOF

# Package notes for verification
cat > "${PACKAGE_NAME}/PACKAGE_NOTES.md" << 'EOF'
# A-SWARM Pilot Package Notes

## Prerequisites
- Ubuntu 22.04+ / Debian 11+ / RHEL 8+
- 4 vCPU, 8 GB RAM minimum
- 50 GB available disk space
- Ports: 80, 443, 3000, 8000, 8089/udp, 9090, 9443, 50051

## Verify Package Integrity
```bash
# Verify checksum
sha256sum -c aswarm-pilot-*.tar.gz.sha256

# Verify GPG signature (if provided)
gpg --verify aswarm-pilot-*.tar.gz.sha256.sig aswarm-pilot-*.tar.gz.sha256
```

## Quick Installation
```bash
./quickstart.sh
```

## Manual Installation
```bash
cd install && ./install.sh
```

## Air-Gap Installation
If `images/aswarm-images.tar` is present:
```bash
# Load images first
docker load < images/aswarm-images.tar

# Then run installer
./quickstart.sh
```
EOF

# Quick start script
echo "  • Quick start script"
cat > "${PACKAGE_NAME}/quickstart.sh" << 'EOF'
#!/bin/bash
set -euo pipefail

echo "A-SWARM Quick Start"
echo "=================="
echo ""
echo "This will install and start A-SWARM services."
echo "Prerequisites: Ubuntu 22.04+, 4 CPU, 8GB RAM, 50GB disk"
echo ""

# Check for air-gap mode
if [[ -f "images/aswarm-images.tar" ]]; then
    echo "Air-gap mode detected. Loading Docker images..."
    docker load < images/aswarm-images.tar || {
        echo "Failed to load Docker images"
        exit 1
    }
    echo "✅ Docker images loaded"
    echo ""
fi

read -p "Continue with installation? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled"
    exit 1
fi

# Run installer
cd "$(dirname "$0")/install" && ./install.sh

echo ""
echo "✅ Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Access Control Center: http://localhost"
echo "  2. View metrics: http://localhost:3000 (Grafana)"
echo "  3. Run health check: cd .. && make health"
echo "  4. Enable autonomy when ready: make enable-autonomy"
EOF
chmod +x "${PACKAGE_NAME}/quickstart.sh"

# Ensure all scripts are executable
echo "  • Setting permissions"
chmod +x "${PACKAGE_NAME}/install/install.sh" 2>/dev/null || true
chmod +x "${PACKAGE_NAME}/validation"/*.sh 2>/dev/null || true

# ---- Optional: Include Docker images for air-gap -------------------------------
if [[ "${WITH_IMAGES}" == "true" ]]; then
    echo ""
    echo "Including Docker images for air-gap deployment..."

    if ! command -v docker >/dev/null 2>&1; then
        echo "ERROR: Docker not available; cannot export images"
        exit 1
    fi

    mkdir -p "${PACKAGE_NAME}/images"

    # Get list of images from compose file
    pushd "${REPO_ROOT}/customer-deployment/install" >/dev/null

    # Extract image names from docker-compose.yml
    images=$(docker compose config 2>/dev/null | grep 'image:' | awk '{print $2}' | sort -u || true)

    if [[ -z "${images}" ]]; then
        # Fallback to known images
        images="prom/prometheus:v2.45.0 grafana/grafana:10.0.0 nginx:alpine"
        echo "  Warning: Could not extract images from compose, using defaults"
    fi

    echo "${images}" > "${PACKAGE_NAME}/images/image-list.txt"

    # Save images to tar
    echo "  Exporting images (this may take several minutes)..."
    docker save ${images} -o "${PACKAGE_NAME}/images/aswarm-images.tar" || {
        echo "  Warning: Some images could not be saved"
    }

    # Add air-gap instructions
    cat > "${PACKAGE_NAME}/images/README.md" << 'AIRGAP'
# Air-Gap Deployment

This package includes Docker images for offline installation.

## Loading Images
```bash
docker load < aswarm-images.tar
```

## Included Images
See `image-list.txt` for the complete list.

## Verification
After loading, verify with:
```bash
docker images | grep -E "(aswarm|prom|grafana|nginx)"
```
AIRGAP

    popd >/dev/null
    echo "  ✅ Docker images included ($(du -h "${PACKAGE_NAME}/images/aswarm-images.tar" | cut -f1))"
fi

# ---- Optional: Generate SBOM if syft is available ------------------------------
if command -v syft >/dev/null 2>&1; then
    echo ""
    echo "Generating Software Bill of Materials (SBOM)..."
    syft dir:"${PACKAGE_NAME}" -o spdx-json > "${PACKAGE_NAME}/SBOM.spdx.json" 2>/dev/null || {
        echo "  Warning: SBOM generation failed"
    }
fi

# ---- Create reproducible archive -----------------------------------------------
echo ""
echo "Creating archive..."

# Use deterministic tar options for reproducible builds
# This ensures the same checksum across multiple builds
TAR_OPTS=(
    --sort=name
    --owner=0
    --group=0
    --numeric-owner
    --mtime='UTC 2025-01-01 00:00:00'
)

# Check if GNU tar (supports the options we need)
if tar --version | grep -q "GNU tar"; then
    tar czf "${PACKAGE_NAME}.tar.gz" "${TAR_OPTS[@]}" "${PACKAGE_NAME}"
else
    # Fallback for non-GNU tar
    echo "  Note: Non-GNU tar detected, archive may not be reproducible"
    tar czf "${PACKAGE_NAME}.tar.gz" "${PACKAGE_NAME}"
fi

# Clean up staging directory
rm -rf "${PACKAGE_NAME}"

# Generate checksums
echo "Generating checksums..."
sha256sum "${PACKAGE_NAME}.tar.gz" > "${PACKAGE_NAME}.tar.gz.sha256"

# Optional GPG signature
if [[ "${GPG_SIGN}" == "true" && -n "${GPG_KEY}" ]]; then
    if command -v gpg >/dev/null 2>&1; then
        echo "Signing package with GPG key ${GPG_KEY}..."
        gpg --batch --yes --local-user "${GPG_KEY}" \
            --output "${PACKAGE_NAME}.tar.gz.sha256.sig" \
            --detach-sign "${PACKAGE_NAME}.tar.gz.sha256" || {
            echo "  Warning: GPG signing failed"
        }
    else
        echo "  Warning: GPG not available, skipping signature"
    fi
fi

# ---- Final report --------------------------------------------------------------
echo ""
echo "✅ Package created successfully!"
echo ""
echo "Files created:"
echo "  • ${PACKAGE_NAME}.tar.gz ($(du -h "${PACKAGE_NAME}.tar.gz" | cut -f1))"
echo "  • ${PACKAGE_NAME}.tar.gz.sha256"
[[ -f "${PACKAGE_NAME}.tar.gz.sha256.sig" ]] && echo "  • ${PACKAGE_NAME}.tar.gz.sha256.sig (GPG signature)"
[[ -f "${PACKAGE_NAME}/SBOM.spdx.json" ]] && echo "  • SBOM.spdx.json included in package"
echo ""
echo "Package contents:"
echo "  • Complete Docker Compose deployment"
echo "  • One-line installer script with dependency checks"
echo "  • Health & performance validation tools"
echo "  • Grafana dashboards & Prometheus configuration"
echo "  • Operations Makefile with pilot commands"
echo "  • Comprehensive documentation & troubleshooting guide"
[[ "${WITH_IMAGES}" == "true" ]] && echo "  • Docker images for air-gap deployment"
echo ""
echo "Checksum:"
cat "${PACKAGE_NAME}.tar.gz.sha256"
echo ""
echo "To deploy on target server:"
echo "  1. Copy ${PACKAGE_NAME}.tar.gz to target"
echo "  2. Verify: sha256sum -c ${PACKAGE_NAME}.tar.gz.sha256"
echo "  3. Extract: tar -xzf ${PACKAGE_NAME}.tar.gz"
echo "  4. Deploy: cd ${PACKAGE_NAME} && ./quickstart.sh"
echo ""
echo "For air-gap deployment:"
echo "  Build with: WITH_IMAGES=true ./package.sh"
echo ""
echo "For signed packages:"
echo "  Build with: GPG_SIGN=true GPG_KEY=your-key-id ./package.sh"
echo ""