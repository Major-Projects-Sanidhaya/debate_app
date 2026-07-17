COMPOSE ?= docker compose

# The Mac's LAN IP, baked into livekit.yaml as node_ip. Auto-detected from
# Wi-Fi (en0, falling back to en1); override with `LAN_IP=1.2.3.4 make up`.
LAN_IP ?= $(shell ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 127.0.0.1)

.DEFAULT_GOAL := help
.PHONY: help up down logs psql redis-cli monitoring lan-ip render

help: ## List available targets
	@grep -E '^[a-zA-Z-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "  %-12s %s\n", $$1, $$2}'

render: ## Render livekit.yaml from livekit.yaml.template using LAN_IP
	@sed 's/$${LAN_IP}/$(LAN_IP)/g' livekit.yaml.template > livekit.yaml
	@echo "rendered livekit.yaml (node_ip: $(LAN_IP))"
	@if [ "$(LAN_IP)" = "127.0.0.1" ]; then \
		echo "WARNING: no LAN IP detected — simulator will work, physical devices won't get media."; \
		echo "         Re-run with LAN_IP=<your-ip> make up once you're on Wi-Fi."; \
	fi

up: render ## Start postgres, redis, livekit and wait until all are healthy
	$(COMPOSE) up -d --wait

down: ## Stop and remove all containers, monitoring included (data volumes kept)
	$(COMPOSE) --profile monitoring down

logs: ## Tail logs of all running services
	$(COMPOSE) --profile monitoring logs -f --tail=100

psql: ## Open a psql shell in the postgres container
	$(COMPOSE) exec postgres psql -U debate -d debate

redis-cli: ## Open a redis-cli shell in the redis container
	$(COMPOSE) exec redis redis-cli

monitoring: render ## Start everything plus prometheus + grafana
	$(COMPOSE) --profile monitoring up -d --wait

lan-ip: ## Print this Mac's LAN IP (Wi-Fi / en0)
	@ipconfig getifaddr en0 || { echo "no IPv4 on en0 — try: ipconfig getifaddr en1" >&2; exit 1; }
