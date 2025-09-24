# A-SWARM Security Documentation

## Image Security

### Image Signing

All A-SWARM container images are signed using [cosign](https://docs.sigstore.dev/cosign/overview) with keyless OIDC signatures from GitHub Actions.

#### Verification

```bash
# Verify a specific component
cosign verify ghcr.io/anthropics/aswarm-sentinel:latest \
  --certificate-identity-regexp="https://github.com/anthropics/aswarm" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com"

# Verify all components
./scripts/verify_images.sh --version latest
```

#### Policy Enforcement

Deploy the cosign admission controller to enforce signature verification:

```bash
# Install sigstore policy controller
helm repo add sigstore https://sigstore.github.io/helm-charts
helm install policy-controller sigstore/policy-controller \
  --namespace cosign-system \
  --create-namespace

# Apply A-SWARM policy
kubectl apply -f security/cosign-policy.yaml
```

### Software Bill of Materials (SBOM)

Each image includes comprehensive SBOMs in multiple formats:
- **SPDX 2.3** - Standard format for compliance
- **CycloneDX 1.4** - Security-focused format with vulnerability data
- **Syft Native** - Detailed package metadata

#### SBOM Verification

```bash
# Download SBOM attestation
cosign download attestation ghcr.io/anthropics/aswarm-sentinel:latest \
  --predicate-type=https://spdx.dev/Document \
  | jq -r .payload | base64 -d | jq .predicate > sentinel-sbom.json

# Analyze SBOM
python scripts/analyze_sbom.py sentinel-sbom.json --compliance-check
```

## Runtime Security

### Pod Security Standards

A-SWARM enforces the **restricted** Pod Security Standard by default:

- Containers run as non-root (UID 65532)
- Read-only root filesystem
- No privileged escalation
- Minimal capabilities
- Security contexts enforced

### RBAC (Role-Based Access Control)

Each component has minimal required permissions:

#### Sentinel DaemonSet
- **Leases**: Create, update, patch, delete (for telemetry)
- **Nodes**: Get, list (for topology awareness)
- **Events**: Create (for audit trail)

#### Pheromone Deployment  
- **Leases**: Get, list, watch (for quorum detection)
- **ConfigMaps**: Create, update, patch (for elevation artifacts)
- **Events**: Create (for audit trail)

#### MicroAct Executor
- **NetworkPolicies**: Create, delete (for isolation actions)
- **ConfigMaps**: Get, list, create, update (for policies and evidence)
- **Pods**: Get, list, update (for labeling)
- **Events**: Create (for audit trail)

### Network Security

#### Network Policies

When `networkPolicies.enabled=true`, each component is restricted:

- **Ingress**: Only metrics scraping from monitoring namespaces
- **Egress**: DNS, Kubernetes API, and component-specific communication
- **Default**: Deny all other traffic

#### TLS/mTLS

- All Kubernetes API communication uses TLS
- Optional: Enable mTLS between components with service mesh
- Webhook endpoints use TLS with proper certificate validation

### Secrets Management

#### Kill Switch Keys

```bash
# Generate HMAC key for kill switch
openssl rand -hex 32 > killswitch.key

# Create secret
kubectl create secret generic aswarm-killswitch-key \
  --from-file=key=killswitch.key \
  --namespace=aswarm
```

#### Fast Path Keys (if enabled)

```bash
# Generate UDP fast path HMAC key
openssl rand -hex 32 > fastpath.key

# Create secret
kubectl create secret generic aswarm-fastpath-key \
  --from-file=key.hex=fastpath.key \
  --namespace=aswarm
```

## Supply Chain Security

### SLSA Compliance

A-SWARM images meet SLSA Build Level 3 requirements:

- ✅ **Build service**: GitHub Actions (non-falsifiable)
- ✅ **Source integrity**: Git SHA verified
- ✅ **Build integrity**: Provenance attestations
- ✅ **Metadata completeness**: Full build metadata included

### Vulnerability Management

#### Continuous Scanning

- **Trivy**: Scans for OS and language vulnerabilities
- **GitHub Security Advisory**: Integration for dependency alerts
- **Dependabot**: Automatic dependency updates

#### Vulnerability Response

1. **Critical**: Patch within 24 hours
2. **High**: Patch within 7 days  
3. **Medium**: Patch within 30 days
4. **Low**: Next regular release

#### Security Updates

Subscribe to security notifications:
- GitHub repository watch (security alerts)
- Release notes include security fixes
- Security advisories published for critical issues

## Operational Security

### Kill Switch

Global emergency shutdown capability:

```bash
# Emergency disable (all enforcement stopped)
kubectl patch configmap aswarm-killswitch \
  -n aswarm \
  --type merge -p '{"data":{"state":"disabled"}}'

# Per-component disable
kubectl patch configmap aswarm-killswitch \
  -n aswarm \
  --type merge -p '{"data":{"pheromone.enabled":"false"}}'

# Per-ring disable (stop Ring 3+ actions)
kubectl patch configmap aswarm-killswitch \
  -n aswarm \
  --type merge -p '{"data":{"ring3.enabled":"false"}}'
```

### Audit Logging

All enforcement actions generate audit events:

```bash
# View enforcement audit log
kubectl get events -n aswarm --field-selector reason=AswarmlEvation

# View action certificates
kubectl get configmaps -n aswarm -l type=action-certificate

# Export audit data
kubectl get events --all-namespaces -o json \
  | jq '.items[] | select(.reason | startswith("Aswarm"))' \
  > aswarm-audit.json
```

### Monitoring & Alerting

#### Security Metrics

- `aswarm_kill_switch_changes_total` - Kill switch state changes
- `aswarm_rbac_denials_total` - RBAC access denials
- `aswarm_image_pulls_total{verified="true|false"}` - Signed vs unsigned pulls
- `aswarm_security_violations_total` - Policy violations

#### Critical Alerts

Set up monitoring for:
- Kill switch state changes
- Unexpected RBAC denials
- Failed image signature verifications
- Abnormal action execution rates

## Compliance & Attestations

### SOC 2 Type II

A-SWARM supports SOC 2 compliance requirements:

- **CC6.1**: Logical access security - RBAC and authentication
- **CC6.6**: Management of system vulnerabilities - Continuous scanning  
- **CC6.7**: Data transmission and disposal - TLS encryption
- **CC7.1**: System boundaries and data flow - Network policies

### Supply Chain Attestations

For compliance audits, the following attestations are available:

1. **Build Provenance** - SLSA attestations proving build integrity
2. **Vulnerability Scans** - Trivy reports for all images
3. **License Compliance** - SBOM analysis with license inventory
4. **Source Code Integrity** - Git commit signatures

#### Generating Compliance Package

```bash
# Download all attestations for a release
./scripts/download_attestations.sh --version v1.0.0 --output compliance-v1.0.0/

# Generate compliance report
./scripts/analyze_sbom.py compliance-v1.0.0/sboms/ \
  --compliance-check \
  --output compliance-report.json
```

## Incident Response

### Security Incident Playbook

1. **Immediate Response**
   ```bash
   # Emergency kill switch
   kubectl patch configmap aswarm-killswitch -n aswarm \
     --type merge -p '{"data":{"state":"disabled"}}'
   ```

2. **Evidence Collection**
   ```bash
   # Generate evidence pack
   make evidence-pack
   
   # Collect logs
   kubectl logs -n aswarm --all-containers --previous > incident-logs.txt
   ```

3. **Investigation**
   - Review action certificates for unauthorized actions
   - Check RBAC audit logs for privilege escalation
   - Analyze network policies for lateral movement

4. **Recovery**
   - Patch identified vulnerabilities
   - Rotate compromised secrets
   - Update RBAC policies if needed
   - Re-enable with enhanced monitoring

### Contact Information

- **Security Team**: security@aswarm.ai
- **On-call**: +1-555-ASWARM (24/7)
- **PGP Key**: Available at keybase.io/aswarm

## Security Assessment

### Threat Model

A-SWARM defends against:
- ✅ Lateral movement attacks
- ✅ Data exfiltration
- ✅ Privilege escalation
- ✅ Resource exhaustion
- ✅ Supply chain attacks

### Known Limitations

- **Physical access**: No protection against direct hardware access
- **Kernel exploits**: Limited protection against kernel-level compromises  
- **Admin credentials**: Cannot defend against cluster admin abuse
- **Time-based attacks**: ~1.5s detection window allows brief exploitation

### Regular Security Reviews

- **Code reviews**: All changes require security review
- **Penetration testing**: Quarterly red team exercises
- **Dependency audits**: Monthly vulnerability scans
- **RBAC reviews**: Quarterly permission audits

## Secure Development

### Development Security

- **Signed commits**: All commits signed with GPG
- **Branch protection**: Main branch requires reviews + status checks
- **Secret scanning**: GitHub secret scanning enabled
- **Dependency scanning**: Dependabot and Trivy in CI

### Build Security

- **Reproducible builds**: Deterministic Dockerfiles
- **Multi-stage builds**: Minimal attack surface
- **Base image pinning**: SHA-pinned base images
- **Layer optimization**: Minimal layers, no secrets

Last Updated: 2025-08-29