NAMESPACE ?= aswarm
SELECTOR ?= app=anomaly

.PHONY: up deploy drill report dashboard helm-install helm-uninstall preflight evidence-pack validate-slo build-images verify-images clean

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

security-scan:
	@echo "Running security analysis on SBOMs..."
	@if [ -d "./sboms" ]; then \
		python scripts/analyze_sbom.py ./sboms --compliance-check; \
	else \
		echo "No SBOMs found. Run 'make build-images-prod' first"; \
	fi

clean:
	rm -rf ActionCertificates/
	rm -f EvidencePack.zip
	kubectl delete namespace $(NAMESPACE) --ignore-not-found=true

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
	@echo "Cleanup:"
	@echo "  make clean         # Remove certificates & namespace"
	@echo "  make helm-uninstall # Remove Helm deployment"