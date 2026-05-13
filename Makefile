.PHONY: build build-gpu up up-gpu down logs ps poly-start poly-stop setup clean help

# ── Build ─────────────────────────────────────────────────────────────────────

build:
	docker compose build

build-gpu:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml build resolver

# ── Lifecycle ─────────────────────────────────────────────────────────────────

up: build
	docker compose up -d
	@echo ""
	@echo "Services running (excluding executor-polymarket)."
	@echo "Launch Polymarket executor with: make poly-start"

up-gpu:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
	@echo ""
	@echo "Services running with GPU-enabled resolver (excluding executor-polymarket)."
	@echo "Launch Polymarket executor with: make poly-start"

down:
	docker compose down
	@make poly-stop 2>/dev/null || true

# ── Executor Polymarket (VPN namespace) ───────────────────────────────────────

poly-start:
	bash infra/scripts/start-poly-executor.sh

poly-stop:
	docker stop arb-executor-polymarket 2>/dev/null || true
	docker rm   arb-executor-polymarket 2>/dev/null || true

# ── Full system ───────────────────────────────────────────────────────────────

start:
	bash infra/scripts/start.sh

# ── Logs ──────────────────────────────────────────────────────────────────────

logs:
	docker compose logs -f

logs-collector:
	docker compose logs -f collector

logs-resolver:
	docker compose logs -f resolver

logs-strategy:
	docker compose logs -f strategy

logs-kalshi:
	docker compose logs -f executor-kalshi

logs-poly:
	docker logs -f arb-executor-polymarket

# ── Status ────────────────────────────────────────────────────────────────────

ps:
	docker compose ps
	@echo ""
	@echo "Polymarket executor:"
	@docker ps --filter name=arb-executor-polymarket --format "  {{.Names}}\t{{.Status}}" 2>/dev/null || echo "  not running"

# ── Setup ─────────────────────────────────────────────────────────────────────

setup:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env from .env.example — fill in your credentials"; \
	else \
		echo ".env already exists"; \
	fi
	bash infra/scripts/setup-veth.sh

# ── Cleanup ───────────────────────────────────────────────────────────────────

clean:
	docker compose down -v
	docker image rm arb-collector arb-resolver arb-strategy \
	    arb-executor-kalshi arb-executor-polymarket 2>/dev/null || true

# ── Redis CLI shortcut ────────────────────────────────────────────────────────

redis-cli:
	docker compose exec redis redis-cli

# ── Help ──────────────────────────────────────────────────────────────────────

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@echo "  setup          Copy .env.example → .env, create veth bridge"
	@echo "  build          Build all Docker images"
	@echo "  build-gpu      Build resolver with CUDA torch wheel"
	@echo "  up             Build + start all services (except poly executor)"
	@echo "  up-gpu         Build + start services with GPU-enabled resolver"
	@echo "  down           Stop all services"
	@echo "  start          Full system start (VPN + services + poly executor)"
	@echo "  poly-start     Launch executor-polymarket in VPN namespace"
	@echo "  poly-stop      Stop executor-polymarket"
	@echo "  logs           Tail all logs"
	@echo "  logs-strategy  Tail strategy logs"
	@echo "  ps             Show service status"
	@echo "  redis-cli      Open Redis CLI"
	@echo "  clean          Destroy containers and volumes"
