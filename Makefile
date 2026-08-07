.PHONY: help doctor up down logs build restart ps

help:          ## Show this help
	@echo "TLDR Radio — turn the TLDR newsletters into a daily radio show."
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "First time here? Run 'make doctor', then 'make up'."

doctor:        ## Check this machine can run it, before pulling ~5 GB
	@./scripts/doctor.sh

up:            ## Build + start both services in the background
	docker compose up -d --build

down:          ## Stop and remove the containers
	docker compose down

logs:          ## Tail logs from both services
	docker compose logs -f

build:         ## Rebuild the app image
	docker compose build

restart:       ## Restart just the app (after a code change)
	docker compose up -d --build app

ps:            ## Show container status
	docker compose ps
