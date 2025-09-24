# A-SWARM Pilot Operations Makefile
# Quick commands for deployment and validation

.PHONY: help install up down restart health perf logs status clean reset
.PHONY: enable-autonomy disable-autonomy collect-support
.PHONY: pilot-up pilot-preflight pilot-dashboard pilot-package
.PHONY: grafana-provision grafana-import landing

# ---- Config (override via env or CLI, e.g. `make API_BASE=http://aswarm:8000 health`) ----
API_BASE        ?= http://localhost:8000
PROM_BASE       ?= http://localhost:9090
GRAFANA_BASE    ?= http://localhost:3000
COMPOSE_DIR     ?= install
COMPOSE_FILE    ?= docker-compose.yml
COMPOSE_PROJECT ?= aswarm-pilot

# Auto-detect docker compose v2 vs v1
DC := $(shell if docker compose version >/dev/null 2>&1; then echo "docker compose"; else echo "docker-compose"; fi)

# Export .env (if present) so recipes inherit service credentials/ports
-include $(COMPOSE_DIR)/.env
export

# Default target
help:
	@echo "A-SWARM Pilot Operations"
	@echo "========================"
	@echo ""
	@echo "Deployment Commands:"
	@echo "  make install           - Run complete installation (Docker, Compose, A-SWARM)"
	@echo "  make up                - Start all A-SWARM services"
	@echo "  make down              - Stop all A-SWARM services"
	@echo "  make restart           - Restart all services"
	@echo ""
	@echo "Validation Commands:"
	@echo "  make health            - Run comprehensive health check"
	@echo "  make perf              - Run performance validation"
	@echo "  make status            - Quick status check"
	@echo "  make logs [svc=name]   - Tail service logs (all or specific)"
	@echo ""
	@echo "Management Commands:"
	@echo "  make enable-autonomy   - Enable autonomous evolution (CAUTION)"
	@echo "  make disable-autonomy  - Disable autonomous evolution (emergency stop)"
	@echo "  make clean             - Stop services and clean up"
	@echo "  make reset             - Complete reset (DESTROYS ALL DATA)"
	@echo ""
	@echo "Dashboard Commands:"
	@echo "  make landing           - Open Control Center landing page"
	@echo "  make pilot-dashboard   - Open all dashboards"
	@echo "  make grafana-import    - Push bundled Grafana dashboards to server"
	@echo "  make grafana-provision - Info on Grafana provisioning"
	@echo ""
	@echo "Support Commands:"
	@echo "  make collect-support   - Create support bundle for troubleshooting"
	@echo "  make pilot-package     - Create tarball of dashboards, validators, and compose"
	@echo "  make pilot-preflight   - Run all preflight checks"
	@echo ""

# Installation
install:
	@echo "Starting A-SWARM installation..."
	@cd $(COMPOSE_DIR) && ./install.sh

# Service management
up:
	@echo "Starting A-SWARM services..."
	@cd $(COMPOSE_DIR) && $(DC) -f $(COMPOSE_FILE) -p $(COMPOSE_PROJECT) --profile full up -d
	@echo "Services starting. Run 'make status' to check."

down:
	@echo "Stopping A-SWARM services..."
	@cd $(COMPOSE_DIR) && $(DC) -f $(COMPOSE_FILE) -p $(COMPOSE_PROJECT) down
	@echo "Services stopped."

restart:
	@echo "Restarting A-SWARM services..."
	@cd $(COMPOSE_DIR) && $(DC) -f $(COMPOSE_FILE) -p $(COMPOSE_PROJECT) restart
	@echo "Services restarted. Run 'make health' to verify."

# Validation
health:
	@echo "Running health check..."
	@cd validation && \
		API_BASE=$(API_BASE) PROM_BASE=$(PROM_BASE) GRAFANA_BASE=$(GRAFANA_BASE) \
		COMPOSE_DIR=../$(COMPOSE_DIR) COMPOSE_FILE=$(COMPOSE_FILE) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) \
		./health-check.sh

perf:
	@echo "Running performance check..."
	@cd validation && \
		API_BASE=$(API_BASE) PROM_BASE=$(PROM_BASE) GRAFANA_BASE=$(GRAFANA_BASE) \
		COMPOSE_DIR=../$(COMPOSE_DIR) COMPOSE_FILE=$(COMPOSE_FILE) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) \
		./performance-check.sh

status:
	@echo "Checking A-SWARM status..."
	@cd $(COMPOSE_DIR) && $(DC) -f $(COMPOSE_FILE) -p $(COMPOSE_PROJECT) ps
	@echo ""
	@echo "API Health:"
	@curl -fsS $(API_BASE)/api/health 2>/dev/null | jq '.' || echo "  API not responding"
	@echo ""
	@echo "Prometheus Health:"
	@curl -fsS $(PROM_BASE)/-/healthy 2>/dev/null || echo "  Prometheus not responding"
	@echo ""

logs:
	@echo "Tailing service logs (Ctrl+C to exit)..."
	@cd $(COMPOSE_DIR) && $(DC) -f $(COMPOSE_FILE) -p $(COMPOSE_PROJECT) logs -f --tail=50 $(svc)

# Autonomy control
enable-autonomy:
	@echo "⚠️  WARNING: Enabling autonomous evolution"
	@echo "This will allow A-SWARM to evolve antibodies automatically."
	@read -p "Are you sure? Type 'yes' to confirm: " confirm && \
	if [ "$$confirm" = "yes" ]; then \
		curl -fsS -X POST $(API_BASE)/api/autonomy/enable >/dev/null && \
		echo "✅ Autonomous evolution ENABLED"; \
	else \
		echo "❌ Cancelled"; \
	fi

disable-autonomy:
	@echo "🚨 EMERGENCY STOP - Disabling autonomous evolution..."
	@curl -fsS -X POST $(API_BASE)/api/autonomy/disable >/dev/null && \
	echo "✅ Autonomous evolution DISABLED (safe mode)"

# Dashboard management
landing:
	@echo "Opening A-SWARM Control Center landing page..."
	@command -v xdg-open >/dev/null 2>&1 && xdg-open http://localhost || \
	command -v open >/dev/null 2>&1 && open http://localhost || \
	echo "Please open http://localhost in your browser"

pilot-dashboard:
	@echo "Opening A-SWARM dashboards..."
	@command -v xdg-open >/dev/null 2>&1 && xdg-open http://localhost || \
	command -v open >/dev/null 2>&1 && open http://localhost || \
	echo "Control Center: http://localhost"
	@command -v xdg-open >/dev/null 2>&1 && xdg-open $(GRAFANA_BASE) || \
	command -v open >/dev/null 2>&1 && open $(GRAFANA_BASE) || \
	echo "Grafana: $(GRAFANA_BASE)"

grafana-provision:
	@echo "Provisioning Grafana dashboards via API (if enabled)..."
	@echo "Open: $(GRAFANA_BASE)"
	@echo "If authentication is enabled, set GRAFANA_USER/GRAFANA_PASSWORD env vars."

grafana-import:
	@echo "Importing A-SWARM dashboards..."
	@for dash in $(COMPOSE_DIR)/provisioning/grafana/dashboards/*.json; do \
		if [ -f "$$dash" ]; then \
			name=$$(basename "$$dash"); \
			echo "Posting $$name to Grafana..."; \
			curl -fsS -u "$${GRAFANA_USER:-admin}:$${GRAFANA_PASSWORD:-admin}" \
				-H "Content-Type: application/json" \
				-X POST "$(GRAFANA_BASE)/api/dashboards/db" \
				-d "{\"dashboard\":$$(cat "$$dash"),\"overwrite\":true,\"folderId\":0}" \
				>/dev/null && echo "✅ Imported $$name" || echo "⚠️  Could not import $$name"; \
		fi \
	done

# Cleanup
clean:
	@echo "Cleaning up A-SWARM deployment..."
	@cd $(COMPOSE_DIR) && $(DC) -f $(COMPOSE_FILE) -p $(COMPOSE_PROJECT) down
	@echo "Services stopped. Data volumes preserved."
	@echo "Run 'make reset' to remove all data."

reset:
	@echo "⚠️  WARNING: This will DESTROY ALL DATA & remove images created by this project"
	@read -p "Are you sure? Type 'yes' to confirm: " confirm && \
	if [ "$$confirm" = "yes" ]; then \
		cd $(COMPOSE_DIR) && $(DC) -f $(COMPOSE_FILE) -p $(COMPOSE_PROJECT) down -v --remove-orphans && \
		docker system prune -f && \
		echo "✅ Complete reset done"; \
	else \
		echo "❌ Reset cancelled"; \
	fi

# Support
collect-support:
	@echo "Collecting support information..."
	@mkdir -p support-bundles
	@timestamp=$$(date +%Y%m%d_%H%M%S); \
	bundle_dir="support-bundles/aswarm-support-$$timestamp"; \
	mkdir -p "$$bundle_dir"; \
	cd $(COMPOSE_DIR) && $(DC) -f $(COMPOSE_FILE) -p $(COMPOSE_PROJECT) ps --format json > "../$$bundle_dir/container-status.json" 2>&1; \
	cd $(COMPOSE_DIR) && $(DC) -f $(COMPOSE_FILE) -p $(COMPOSE_PROJECT) logs --no-color --tail=500 > "../$$bundle_dir/docker-logs.txt" 2>&1; \
	{ [ -f "$(COMPOSE_DIR)/.env" ] && cat "$(COMPOSE_DIR)/.env" | grep -v -E '(SECRET|PASSWORD|KEY|TOKEN)' > "$$bundle_dir/env-sanitized.txt"; } 2>/dev/null || true; \
	curl -fsS $(PROM_BASE)/api/v1/targets > "$$bundle_dir/prometheus-targets.json" 2>&1 || true; \
	curl -fsS $(API_BASE)/api/health > "$$bundle_dir/api-health.json" 2>&1 || true; \
	tar -czf "support-bundles/aswarm-support-$$timestamp.tar.gz" -C support-bundles "$$(basename $$bundle_dir)" && \
	rm -rf "$$bundle_dir" && \
	echo "✅ Support bundle: support-bundles/aswarm-support-$$timestamp.tar.gz" || \
	echo "❌ Failed to create support bundle"

pilot-package:
	@echo "Creating pilot package tarball..."
	@name="aswarm-pilot-$$(date +%Y%m%d_%H%M%S)"; \
	tar -czf "$$name.tgz" \
		README.md \
		Makefile \
		validation/*.sh \
		$(COMPOSE_DIR)/$(COMPOSE_FILE) \
		$(COMPOSE_DIR)/.env.template \
		$(COMPOSE_DIR)/install.sh \
		$(COMPOSE_DIR)/provisioning \
		$(COMPOSE_DIR)/assets 2>/dev/null || true; \
	echo "✅ Package: $$name.tgz"

# Quick pilot commands (shortcuts)
pilot-up: up
	@sleep 10
	@make health

pilot-preflight: health perf
	@echo ""
	@echo "✅ Preflight checks complete"