.PHONY: up down logs test dev-backend dev-frontend

up: ## Build and start backend (:8000) and frontend (:3000)
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example - add your LLM_API_KEYS")
	docker compose up -d --build
	@echo "Backend:  http://localhost:8000/health"
	@echo "Frontend: http://localhost:3000"

down: ## Stop the stack (the SQLite volume is kept)
	docker compose down

logs:
	docker compose logs -f

test: ## Run backend tests
	cd backend && uv run pytest -q

dev-backend: ## Run the backend without Docker
	cd backend && DATABASE_URL=sqlite:///./app.db uv run --env-file ../.env uvicorn app.main:app --reload --port 8000

dev-frontend: ## Run the frontend without Docker
	cd frontend && npm install && npm run dev -- --port 3000
