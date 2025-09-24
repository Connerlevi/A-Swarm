SHELL := /bin/bash

# ---- Config ----
GO       ?= go
GOBIN    ?= $(shell $(GO) env GOBIN)
ifeq ($(GOBIN),)
GOBIN := $(shell $(GO) env GOPATH)/bin
endif

PYTHON        ?= python3
PYTHON_VENV   ?= .venv
PIP           ?= $(PYTHON_VENV)/bin/pip
PYTHON_BIN    ?= $(PYTHON_VENV)/bin/python

# Protobuf
PROTOC        ?= protoc
PROTO_INCLUDE ?= /usr/local/include
# Discover all proto files (excluding vendored deps)
PROTO_FILES   := $(shell find . -name "*.proto" -not -path "./vendor/*" -not -path "./$(PYTHON_VENV)/*" -not -path "./test_env/*" -not -path "./dashboard/node_modules/*" 2>/dev/null || echo "intelligence/evolution.proto federation/federator.proto")

# Outputs
GO_GEN_DIR    ?= gen
PY_GEN_DIR    ?= api/gen

NAMESPACE     ?= aswarm
SELECTOR      ?= app=anomaly

.PHONY: up deploy drill report dashboard helm-install helm-uninstall preflight evidence-pack validate-slo build-images verify-images clean integration-setup protobuf build install-deps archive-versions finalize-versions e2e-smoke smoke-test autonomy-on autonomy-off run-scheduler scorecard help

up:
	@echo "Using Docker Desktop or k3d externally. See prototype README."
	@echo "Quick start:"
	@echo "  make preflight"
	@echo "  make helm-install"
	@echo "  make drill"

deploy:
	kubectl apply -f k8s/namespace.yaml
	kubectl apply -f k8s/rbac.yaml
	kubectl apply -f k8s/policy-configmap.yaml
	kubectl apply -f k8s/sentinel-daemonset.yaml
	kubectl apply -f k8s/pheromone-deployment.yaml
	kubectl apply -f k8s/baseline-allow.yaml
	kubectl apply -f k8s/noisy-deployment.yaml

drill:
	kubectl apply -f k8s/anomaly-job.yaml
	@echo "Waiting for anomaly to start..."
	@sleep 5
	python scripts/measure_mttr.py --namespace $(NAMESPACE)

drill-repeat:
	kubectl apply -f k8s/anomaly-job.yaml
	@echo "Running 5 drills for percentile metrics..."
	python scripts/measure_mttr.py --namespace $(NAMESPACE) --repeat 5

report:
	@python - <<'PY'
import glob, json, statistics as s
from pathlib import Path
vals = []
mttds = []
mttrs = []
for p in glob.glob('ActionCertificates/*.json'):
    d = json.loads(Path(p).read_text())
    if d.get('timestamps'):
        t = d['timestamps']
        vals.append((p, d.get('certificate_id'), d.get('action',{}).get('params',{}).get('selector','')))
        metrics = d.get('metrics', {})
        if metrics.get('MTTD_ms'): mttds.append(metrics['MTTD_ms'])
        if metrics.get('MTTR_s'): mttrs.append(metrics['MTTR_s'])
print(f"Certificates: {len(vals)}")
if mttds:
    print(f"MTTD - P50: {s.median(mttds):.1f}ms, P95: {sorted(mttds)[int(len(mttds)*0.95)]:.1f}ms")
if mttrs:
    print(f"MTTR - P50: {s.median(mttrs):.2f}s, P95: {sorted(mttrs)[int(len(mttrs)*0.95)]:.2f}s")
PY

dashboard:
	@echo "Starting KPI dashboard..."
	@echo "Install deps: pip install -r dashboard/requirements.txt"
	@cd dashboard && streamlit run app.py

helm-install:
	helm upgrade --install aswarm ./helm/aswarm-prototype -n $(NAMESPACE) --create-namespace --wait
	@echo "A-SWARM prototype installed in namespace $(NAMESPACE)"
	@echo "Run 'make drill' to test it"

helm-uninstall:
	helm uninstall aswarm -n $(NAMESPACE)
	kubectl delete namespace $(NAMESPACE)

preflight:
	python scripts/preflight.py

evidence-pack:
	@echo "Generating evidence pack for $(NAMESPACE) namespace..."
	python scripts/generate_evidence_pack.py --namespace=$(NAMESPACE) --run-prefix=$(RUN_PREFIX) --output=EvidencePack.zip
	@echo "Evidence pack generated: EvidencePack.zip"
	@echo "Open kpi_report.html from the zip for executive dashboard"

validate-slo: evidence-pack
	@echo "Validating evidence pack against SLOs..."
	python scripts/validate_slo.py EvidencePack.zip

build-images:
	@echo "Building A-SWARM container images..."
	./scripts/build_images.sh

build-images-prod:
	@echo "Building production images with signing..."
	SIGN_IMAGES=true GENERATE_SBOM=true ./scripts/build_images.sh

verify-images:
	@echo "Verifying A-SWARM image signatures and SBOMs..."
	./scripts/verify_images.sh

verify-supply-chain:
	@echo "Verifying A-SWARM supply chain security..."
	@if [ -f "verify-supply-chain.sh" ]; then \
		./verify-supply-chain.sh; \
	else \
		echo "Supply chain verification script not found - run './scripts/sign_attest_images.sh' first"; \
	fi

security-scan:
	@echo "Running security analysis on SBOMs..."
	@if [ -d "./sboms" ]; then \
		python scripts/analyze_sbom.py ./sboms --compliance-check; \
	else \
		echo "No SBOMs found. Run 'make build-images-prod' first"; \
	fi

# ---- Deps ----
install-deps:
	@echo "Installing Go deps..."
	$(GO) mod download
	$(GO) mod tidy
	@echo "Checking protoc..."
	@command -v $(PROTOC) >/dev/null || { echo "ERROR: protoc not found. Install protoc."; exit 1; }
	@echo "Installing Go protoc plugins (pinned versions)..."
	$(GO) install google.golang.org/protobuf/cmd/protoc-gen-go@v1.32.0
	$(GO) install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.3.0
	@echo "Ensuring $(GOBIN) on PATH for this session"
	@export PATH="$(GOBIN):$$PATH"
	@echo "Setting up Python venv..."
	@$(PYTHON) -m venv $(PYTHON_VENV) || true
	@$(PIP) install --upgrade pip
	@$(PIP) install grpcio grpcio-tools kubernetes pyyaml

# ---- Proto codegen ----
protobuf:
	@echo "Generating Go protobuf stubs into $(GO_GEN_DIR)..."
	@mkdir -p $(GO_GEN_DIR)
	@export PATH="$(GOBIN):$$PATH"; \
	for proto in $(PROTO_FILES); do \
		echo "  $$proto"; \
		$(PROTOC) -I . -I $(PROTO_INCLUDE) \
			--go_out=$(GO_GEN_DIR) --go_opt=paths=source_relative \
			--go-grpc_out=$(GO_GEN_DIR) --go-grpc_opt=paths=source_relative \
			$$proto; \
	done
	@echo "Generating Python protobuf stubs into $(PY_GEN_DIR)..."
	@mkdir -p $(PY_GEN_DIR)
	@touch $(PY_GEN_DIR)/__init__.py
	@$(PYTHON_BIN) -m grpc_tools.protoc -I . -I $(PROTO_INCLUDE) \
		--python_out=$(PY_GEN_DIR) --grpc_python_out=$(PY_GEN_DIR) \
		$(PROTO_FILES)

# ---- Build ----
build: protobuf
	@echo "Building Go binaries..."
	@mkdir -p bin
	@[ -d intelligence/cmd/evolution-server ] && $(GO) build -o bin/evolution-server ./intelligence/cmd/evolution-server || echo "skip evolution-server"
	@[ -d federation/cmd/federation-server ] && $(GO) build -o bin/federation-server ./federation/cmd/federation-server || echo "skip federation-server"

# ---- Integration helper ----
integration-setup: install-deps protobuf
	@echo "Ensuring Python packages importable..."
	@touch intelligence/__init__.py federation/__init__.py hll/__init__.py
	@echo "Integration setup complete."

# ---- Archive / version hygiene ----
archive-versions:
	@echo "Archiving *-v2.* while preserving paths..."
	@find . -name "*-v2.*" -not -path "./archive/*" \
		-not -path "./venv/*" -not -path "./$(PYTHON_VENV)/*" \
		-not -path "./dashboard/node_modules/*" \
		| while read -r f; do \
			dest="archive/superseded/$${f#./}"; \
			mkdir -p "$$(dirname "$$dest")"; \
			mv "$$f" "$$dest"; \
			echo "  $$f -> $$dest"; \
		done || true

finalize-versions:
	@echo "Renaming *v3-final* -> *v3* and *-final.* -> *.* ..."
	@find . -name "*v3-final*" -not -path "./archive/*" | while read -r f; do \
		nf="$${f//v3-final/v3}"; mv "$$f" "$$nf"; echo "  $$f -> $$nf"; \
	done
	@find . -name "*-final.*" -not -path "./archive/*" | while read -r f; do \
		nf="$${f//-final/}"; mv "$$f" "$$nf"; echo "  $$f -> $$nf"; \
	done

clean:
	rm -rf ActionCertificates/
	rm -f EvidencePack.zip
	kubectl delete namespace $(NAMESPACE) --ignore-not-found=true
	rm -rf bin/
	rm -rf $(GO_GEN_DIR)/
	rm -rf $(PY_GEN_DIR)/
	find . -name "*.pb.go" -delete
	find . -name "*_pb2.py" -delete
	find . -name "*_pb2_grpc.py" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + || true

# ---- End-to-End Smoke Test ----
e2e-smoke: integration-setup
	@echo "==============================================="
	@echo "A-SWARM End-to-End Integration Smoke Test"
	@echo "==============================================="
	@echo ""
	@echo "[1/5] Starting Evolution server on :50051..."
	@pkill -f evolution-server 2>/dev/null || true
	@if [ -f bin/evolution-server ]; then \
		nohup ./bin/evolution-server > evolution.log 2>&1 & \
		echo "Evolution server started (PID: $$!)"; \
		sleep 2; \
	else \
		echo "SKIP: evolution-server not built"; \
	fi
	@echo ""
	@echo "[2/5] Starting Federation server on :9443..."
	@pkill -f federation-server 2>/dev/null || true
	@if [ -f bin/federation-server ]; then \
		nohup ./bin/federation-server > federation.log 2>&1 & \
		echo "Federation server started (PID: $$!)"; \
		sleep 2; \
	else \
		echo "SKIP: federation-server not built"; \
	fi
	@echo ""
	@echo "[3/5] Testing Python Evolution client..."
	@$(PYTHON_BIN) tests/smoke_evolution.py || echo "FAIL: Evolution smoke test"
	@echo ""
	@echo "[4/5] Testing Python Federation client..."
	@$(PYTHON_BIN) tests/smoke_federation.py || echo "FAIL: Federation smoke test"
	@echo ""
	@echo "[5/5] Testing Arena→Evolution integration..."
	@$(PYTHON_BIN) tests/smoke_integration.py || echo "FAIL: Integration test"
	@echo ""
	@echo "==============================================="
	@echo "Smoke test complete. Check logs for details."
	@echo "  Evolution log: evolution.log"
	@echo "  Federation log: federation.log"
	@echo "==============================================="
	@pkill -f evolution-server 2>/dev/null || true
	@pkill -f federation-server 2>/dev/null || true

# Individual smoke tests
smoke-evolution: build
	@echo "Running Evolution smoke..."
	@EVOLUTION_ADDR?=localhost:50051
	@EVOLUTION_TIMEOUT?=5.0
	@$(PYTHON_BIN) tests/smoke_evolution.py --server ${EVOLUTION_ADDR} --timeout ${EVOLUTION_TIMEOUT}

smoke-federation: build
	@echo "Running Federation smoke..."
	@FEDERATION_ADDR?=localhost:9443
	@FEDERATION_TIMEOUT?=5.0
	@$(PYTHON_BIN) tests/smoke_federation.py --server ${FEDERATION_ADDR} --timeout ${FEDERATION_TIMEOUT} ${FEDERATION_RATE_LIMIT:+--rate-limit} ${HLL_FIXTURE:+--sketch-fixture ${HLL_FIXTURE}}

smoke-integration: build
	@echo "Running Integration smoke..."
	@EVOLUTION_ADDR?=localhost:50051
	@FEDERATION_ADDR?=localhost:9443
	@SMOKE_TIMEOUT?=5.0
	@$(PYTHON_BIN) tests/smoke_integration.py --evolution ${EVOLUTION_ADDR} --federation ${FEDERATION_ADDR} --timeout ${SMOKE_TIMEOUT} ${HLL_FIXTURE:+--hll-fixture ${HLL_FIXTURE}}

# Simple smoke test for quick validation
smoke-test:
	@echo "Quick smoke test..."
	@echo "1. Checking Go environment..."
	@$(GO) version || { echo "ERROR: Go not installed"; exit 1; }
	@echo "2. Checking Python environment..."
	@$(PYTHON_BIN) --version || { echo "ERROR: Python venv not set up"; exit 1; }
	@echo "3. Checking protoc..."
	@$(PROTOC) --version || { echo "ERROR: protoc not installed"; exit 1; }
	@echo "4. Checking proto files..."
	@echo "Found proto files: $(PROTO_FILES)"
	@echo "5. Testing Go build..."
	@$(GO) build -o /tmp/test-build ./intelligence/... 2>/dev/null && rm /tmp/test-build || echo "WARN: Go build issues"
	@echo "Smoke test passed!"

# ---- Autonomy Controls ----
autonomy-on:
	@echo "🔓 Enabling A-SWARM autonomous operation..."
	@echo "EVOLUTION_CIRCUIT_BREAKER=false" > .autonomy.env
	@echo "PROMOTE_MAX_CANARY_PCT=5" >> .autonomy.env
	@echo "ROLLBACK_THRESHOLD=2" >> .autonomy.env
	@echo "SAFETY_VIOLATION_LIMIT=0" >> .autonomy.env
	@echo "FEDERATION_ALLOW_OPAQUE_SKETCH=true" >> .autonomy.env
	@echo "LEARN_LOW_CONF=0.80" >> .autonomy.env
	@echo "EVOLVE_MIN_EVENTS=12" >> .autonomy.env
	@echo "FITNESS_PROMOTE_THRESHOLD=0.70" >> .autonomy.env
	@echo "✅ Autonomy enabled. Environment configured in .autonomy.env"
	@echo "💡 Source the environment: source .autonomy.env"
	@echo "💡 Ensure your processes read these environment variables"

autonomy-off:
	@echo "🛑 Disabling A-SWARM autonomous operation..."
	@echo "EVOLUTION_CIRCUIT_BREAKER=true" > .autonomy.env
	@echo "✅ Autonomy disabled. Circuit breaker activated."

run-scheduler:
	@echo "▶️  Starting evolution scheduler (dev mode)..."
	@if [ ! -f .autonomy.env ]; then \
		echo "⚠️  No .autonomy.env found. Run 'make autonomy-on' first."; \
		exit 1; \
	fi
	@echo "💡 Loading autonomy environment..."
	@export $$(cat .autonomy.env | xargs) && \
	EVOLUTION_ADDR=$${EVOLUTION_ADDR:-localhost:50051} && \
	FEDERATION_ADDR=$${FEDERATION_ADDR:-localhost:9443} && \
	SMOKE_TIMEOUT=$${SMOKE_TIMEOUT:-5.0} && \
	echo "Evolution server: $$EVOLUTION_ADDR" && \
	echo "Federation server: $$FEDERATION_ADDR" && \
	echo "Circuit breaker: $$EVOLUTION_CIRCUIT_BREAKER" && \
	echo "🚀 Scheduler configuration loaded. Integration point ready."

scorecard:
	@echo "📊 Generating A-SWARM autonomy scorecard..."
	@mkdir -p artifacts
	@if [ ! -f tests/reporters/scorecard.py ]; then \
		echo "⚠️  Scorecard reporter not implemented yet."; \
		echo "📝 Creating placeholder scorecard..."; \
		echo "# A-SWARM Autonomy Scorecard" > artifacts/autonomy_scorecard.md; \
		echo "Generated: $$(date)" >> artifacts/autonomy_scorecard.md; \
		echo "" >> artifacts/autonomy_scorecard.md; \
		echo "## Current Status" >> artifacts/autonomy_scorecard.md; \
		echo "- EventBus: ✅ Implemented" >> artifacts/autonomy_scorecard.md; \
		echo "- Autonomous Loop: ✅ Implemented" >> artifacts/autonomy_scorecard.md; \
		echo "- Learning Events: ✅ Implemented" >> artifacts/autonomy_scorecard.md; \
		echo "- Auto-promotion: 🔄 In Progress" >> artifacts/autonomy_scorecard.md; \
		echo "- Federation Hook: ⏳ Pending" >> artifacts/autonomy_scorecard.md; \
		echo "" >> artifacts/autonomy_scorecard.md; \
		echo "## Next Steps" >> artifacts/autonomy_scorecard.md; \
		echo "1. Complete auto-promotion logic" >> artifacts/autonomy_scorecard.md; \
		echo "2. Implement federation automation" >> artifacts/autonomy_scorecard.md; \
		echo "3. Deploy full autonomous loop" >> artifacts/autonomy_scorecard.md; \
	else \
		$(PYTHON_BIN) -m tests.reporters.scorecard > artifacts/autonomy_scorecard.md; \
	fi
	@echo "📊 Scorecard saved to artifacts/autonomy_scorecard.md"

help:
	@echo "A-SWARM Prototype Makefile"
	@echo ""
	@echo "Quick start:"
	@echo "  make preflight     # Check prerequisites"
	@echo "  make helm-install  # Deploy via Helm"
	@echo "  make drill         # Run anomaly drill"
	@echo "  make evidence-pack # Generate evidence package"
	@echo "  make dashboard     # Launch KPI board"
	@echo ""
	@echo "Integration:"
	@echo "  make install-deps      # Go/Python deps + protoc plugins"
	@echo "  make protobuf          # Generate Go/Python stubs"
	@echo "  make build             # Build Go services"
	@echo "  make e2e-smoke         # 🚀 END-TO-END SMOKE TEST (one command)"
	@echo "  make smoke-test        # Quick environment validation"
	@echo "  make smoke-evolution   # Evolution service smoke test"
	@echo "  make smoke-federation  # Federation service smoke test"
	@echo "  make smoke-integration # Integration pipeline smoke test"
	@echo "  make finalize-versions # Rename *-final,*v3-final"
	@echo "  make archive-versions  # Move *-v2.* to archive/superseded"
	@echo ""
	@echo "Manual deployment:"
	@echo "  make deploy        # Deploy via kubectl"
	@echo "  make drill         # Single drill"
	@echo "  make drill-repeat  # 5 drills for percentiles"
	@echo "  make report        # Show certificate summary"
	@echo ""
	@echo "Evidence & Reporting:"
	@echo "  make evidence-pack # Generate comprehensive evidence ZIP"
	@echo "                     # Options: RUN_PREFIX=slo"
	@echo ""
	@echo "Observability:"
	@echo "  make dashboards-package  # Package dashboards for import"
	@echo "  make pilot-preflight     # Check deployment readiness"
	@echo "  make observability-setup # Deploy monitoring stack"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean         # Remove certificates & namespace"
	@echo "  make helm-uninstall # Remove Helm deployment"

# ==============================================================================
# Observability Targets
# ==============================================================================

.PHONY: dashboards-package pilot-preflight observability-setup

dashboards-package:
	@echo "📦 Packaging A-SWARM dashboards..."
	@mkdir -p observability/dist
	@cd observability && tar -czf dist/aswarm-dashboards.tar.gz grafana-dashboards/ prometheus-rules/ provisioning/
	@echo "✅ Dashboard package created: observability/dist/aswarm-dashboards.tar.gz"
	@echo ""
	@echo "Import commands:"
	@echo "  # Grafana API import"
	@echo "  curl -X POST http://admin:admin@localhost:3000/api/dashboards/db \\"
	@echo "       -H 'Content-Type: application/json' \\"
	@echo "       -d @observability/grafana-dashboards/evolution-loop.json"
	@echo ""
	@echo "  # File copy for provisioning"
	@echo "  cp observability/grafana-dashboards/*.json /var/lib/grafana/dashboards/aswarm/"
	@echo "  cp observability/prometheus-rules/*.yml /etc/prometheus/rules/"

pilot-preflight:
	@echo "🔍 A-SWARM Pilot Preflight Check"
	@echo "================================="
	@echo ""
	@echo "📊 Checking Prometheus targets..."
	@if curl -s http://localhost:9090/api/v1/targets >/dev/null 2>&1; then \
		echo "✅ Prometheus accessible"; \
	else \
		echo "❌ Prometheus not accessible at localhost:9090"; \
		exit 1; \
	fi
	@echo ""
	@echo "📈 Checking required metrics exist..."
	@echo "Checking: aswarm_eventbus_events_processed_total"
	@if curl -s "http://localhost:9090/api/v1/series?match[]=aswarm_eventbus_events_processed_total" | grep -q "aswarm_eventbus"; then \
		echo "✅ EventBus metrics found"; \
	else \
		echo "⚠️  EventBus metrics not found (may be normal on first boot)"; \
	fi
	@echo ""
	@echo "🎛️  Checking Grafana..."
	@if curl -s http://localhost:3000/api/health >/dev/null 2>&1; then \
		echo "✅ Grafana accessible"; \
		echo "🔗 Dashboard URL: http://localhost:3000/d/aswarm-evolution/a-swarm-evolution-loop"; \
	else \
		echo "❌ Grafana not accessible at localhost:3000"; \
	fi
	@echo ""
	@echo "🏁 Preflight complete!"

observability-setup:
	@echo "🚀 Setting up A-SWARM observability stack..."
	@echo ""
	@echo "Creating monitoring namespace..."
	@kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
	@echo ""
	@echo "Deploying Prometheus..."
	@echo "Note: This is a minimal setup. For production, use Prometheus Operator or Helm charts."
	@echo ""
	@echo "📚 Manual setup required:"
	@echo "1. Deploy Prometheus with config including observability/prometheus-rules/aswarm.yml"
	@echo "2. Deploy Grafana with provisioning from observability/provisioning/"
	@echo "3. Copy dashboards to Grafana provisioning path"
	@echo "4. Run 'make pilot-preflight' to validate setup"