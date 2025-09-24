# A-SWARM GitOps Makefile
# Production-ready automation for GitOps operations

.DEFAULT_GOAL := help
SHELL := /bin/bash

# Configurable variables
NAMESPACE ?= aswarm
PORT ?= 8080
# Use wrapper script for Kustomize builds
KUSTOMIZE ?= ./kustomize-build.sh
ARGOCD ?= argocd
CI ?= false

# Required tools
TOOLS := python3 kubectl yamllint

.PHONY: help
help: ## Show this help message
	@echo "A-SWARM GitOps Operations"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*##"; printf "\033[36m%-20s\033[0m %s\n", "Target", "Description"} /^[a-zA-Z_-]+:.*?##/ { printf "\033[36m%-20s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Prerequisites

.PHONY: check-tools
check-tools: ## Check for required tools
	@echo "Checking required tools..."
	@for tool in $(TOOLS); do \
		command -v $$tool >/dev/null 2>&1 || { echo "❌ Missing tool: $$tool"; exit 1; }; \
	done
	@echo "✅ All required tools present"

##@ Validation

.PHONY: validate
validate: check-tools validate-yaml validate-rules validate-kustomize ## Run all validation checks

.PHONY: validate-yaml
validate-yaml: ## Validate YAML syntax
	@echo "Validating YAML files..."
	@find . -type d \( -name .git -o -name .argocd-cache -o -name vendor \) -prune -false -o \
		-type f \( -name '*.yaml' -o -name '*.yml' \) -print0 | \
		xargs -0 yamllint -f parsable -s || { echo "❌ YAML validation failed"; exit 1; }
	@echo "✅ YAML validation passed"

.PHONY: validate-rules
validate-rules: ## Validate detection rules
	@echo "Validating detection rules..."
	@python3 detection-rules/validate.py \
		--strict \
		--warnings-as-errors \
		--schema detection-rules/schema.json \
		--summary \
		detection-rules/detection-rules.json
	@echo "✅ Detection rules valid"

.PHONY: validate-kustomize
validate-kustomize: ## Validate Kustomize builds
	@echo "Validating base Kustomize build..."
	@$(KUSTOMIZE) build base >/dev/null || { echo "❌ Base build failed"; exit 1; }
	@echo "✅ Base configuration valid"
	@echo "Validating development overlay..."
	@$(KUSTOMIZE) build overlays/development >/dev/null || { echo "❌ Dev overlay failed"; exit 1; }
	@echo "✅ Development overlay valid"
	@echo "Validating production overlay..."
	@$(KUSTOMIZE) build overlays/production >/dev/null || { echo "❌ Prod overlay failed"; exit 1; }
	@echo "✅ Production overlay valid"

##@ Development

.PHONY: dev-preview
dev-preview: check-tools ## Preview development Kustomize output
	@$(KUSTOMIZE) build overlays/development

.PHONY: dev-diff
dev-diff: check-tools ## Show diff for development environment
	@kubectl diff -k overlays/development --field-manager=argo --context=5 || true

.PHONY: dev-apply
dev-apply: validate ## Apply development configuration (dry-run by default)
	@echo "Applying development configuration (dry-run)..."
	@kubectl apply -k overlays/development --dry-run=server
	@echo ""
	@echo "To actually apply, run: kubectl apply -k overlays/development"

##@ Production

.PHONY: prod-preview
prod-preview: check-tools ## Preview production Kustomize output
	@$(KUSTOMIZE) build overlays/production

.PHONY: prod-diff
prod-diff: check-tools ## Show diff for production environment
	@kubectl diff -k overlays/production --field-manager=argo --context=5 || true

.PHONY: prod-plan
prod-plan: validate ## Plan production changes (dry-run)
	@echo "Planning production changes..."
	@kubectl apply -k overlays/production --dry-run=server

##@ Detection Rules (GitOps)

.PHONY: rules-test
rules-test: ## Test detection rules with schema
	@echo "Testing detection rules..."
	@python3 detection-rules/validate.py \
		--schema detection-rules/schema.json \
		detection-rules/detection-rules.json

.PHONY: rules-commit
rules-commit: validate-rules ## Stage and commit detection rules changes
	@echo "Staging detection rules changes..."
	@git add detection-rules/detection-rules.json
	@git status --short detection-rules/
	@echo ""
	@echo "Commit and push changes to trigger GitOps sync"
	@echo "Example: git commit -m 'Update detection rules' && git push"

# DEPRECATED: Direct cluster mutation violates GitOps
.PHONY: rules-update
rules-update: ## [DEPRECATED] Use rules-commit instead
	@echo "❌ GitOps mode: Direct cluster mutation not allowed"
	@echo "Use 'make rules-commit' to stage changes for GitOps"
	@exit 1

##@ Argo CD

.PHONY: argo-check
argo-check: ## Check if Argo CD CLI is available
	@command -v $(ARGOCD) >/dev/null 2>&1 || { echo "❌ Argo CD CLI not found"; exit 1; }
	@echo "✅ Argo CD CLI available"

.PHONY: argo-sync-dev
argo-sync-dev: argo-check ## Sync development application in Argo CD
	@echo "Syncing development application..."
	@$(ARGOCD) app sync aswarm-blue-api-dev --prune --timeout 300

.PHONY: argo-sync-prod
argo-sync-prod: argo-check ## Sync production application in Argo CD (manual)
	@echo "⚠️  Production sync requires manual approval"
	@echo "Run: $(ARGOCD) app sync aswarm-blue-api-prod"

.PHONY: argo-status
argo-status: argo-check ## Show Argo CD application status
	@echo "Development status:"
	@$(ARGOCD) app get aswarm-blue-api-dev --refresh || echo "Dev app not found"
	@echo ""
	@echo "Production status:"
	@$(ARGOCD) app get aswarm-blue-api-prod --refresh || echo "Prod app not found"

.PHONY: argo-install
argo-install: check-tools ## Install Argo CD applications
	@echo "Creating Argo CD project..."
	@kubectl apply -f argocd/aswarm-project.yaml
	@echo "Creating development application..."
	@kubectl apply -f argocd/blue-api-dev.yaml
	@echo "Creating production application..."
	@kubectl apply -f argocd/blue-api-prod.yaml

##@ Monitoring

.PHONY: logs
logs: ## Tail Blue API logs
	@kubectl logs -f -l app.kubernetes.io/name=aswarm-blue-api -n $(NAMESPACE) --tail=100

.PHONY: events
events: ## Show recent events
	@kubectl get events -n $(NAMESPACE) --sort-by='.metadata.creationTimestamp' | tail -20

.PHONY: status
status: ## Show deployment status
	@echo "Deployments:"
	@kubectl get deployments -n $(NAMESPACE) -l app.kubernetes.io/name=aswarm-blue-api
	@echo ""
	@echo "Pods:"
	@kubectl get pods -n $(NAMESPACE) -l app.kubernetes.io/name=aswarm-blue-api
	@echo ""
	@echo "Services:"
	@kubectl get services -n $(NAMESPACE) -l app.kubernetes.io/name=aswarm-blue-api
	@echo ""
	@echo "ConfigMaps:"
	@kubectl get configmaps -n $(NAMESPACE) -l app.kubernetes.io/name=aswarm-blue-api

##@ Utilities

.PHONY: clean
clean: ## Clean up generated files
	@find . -name "*.bak" -delete
	@find . -name "*~" -delete
	@find . -name "*.swp" -delete

.PHONY: install-hooks
install-hooks: ## Install pre-commit hooks
	@command -v pre-commit >/dev/null 2>&1 || { echo "❌ pre-commit not installed"; exit 1; }
	@pre-commit install
	@echo "✅ Pre-commit hooks installed"

.PHONY: run-hooks
run-hooks: ## Run pre-commit hooks manually
	@command -v pre-commit >/dev/null 2>&1 || { echo "❌ pre-commit not installed"; exit 1; }
	@pre-commit run --all-files

.PHONY: port-forward
port-forward: ## Port-forward to Blue API (default: 8080)
	@echo "Port-forwarding Blue API to localhost:$(PORT)..."
	@kubectl port-forward -n $(NAMESPACE) svc/aswarm-blue-api $(PORT):8080

.PHONY: test-api
test-api: ## Test Blue API endpoints
	@echo "Testing Blue API health..."
	@curl -s http://localhost:$(PORT)/health | jq . || echo "API not accessible (is port-forward running?)"
	@echo ""
	@echo "Testing detection rules endpoint..."
	@curl -s http://localhost:$(PORT)/api/v1/rules | jq . || echo "Rules endpoint not accessible"

##@ Advanced Validation

.PHONY: kube-validate
kube-validate: check-tools ## Validate manifests against cluster CRDs
	@command -v kubeconform >/dev/null 2>&1 || { echo "⚠️  kubeconform not installed, skipping"; exit 0; }
	@echo "Validating against cluster CRDs..."
	@$(KUSTOMIZE) build overlays/production | kubeconform -strict -summary -ignore-missing-schemas

.PHONY: dry-run-all
dry-run-all: validate ## Dry-run all environments
	@echo "=== Development Dry Run ==="
	@kubectl apply -k overlays/development --dry-run=server -o name | head -5
	@echo ""
	@echo "=== Production Dry Run ==="
	@kubectl apply -k overlays/production --dry-run=server -o name | head -5

# CI-specific targets
ifeq ($(CI),true)
validate: export TERM=dumb
validate-yaml: export YAMLLINT_CONFIG_FILE=.yamllint.ci
endif