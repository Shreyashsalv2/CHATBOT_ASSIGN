# Lyftr Chat: LLM chatbot with key failover and response caching

A full-stack chat app with these parts:
- A React UI.
- A FastAPI backend that uses a **LiteLLM Router** over a pool of Gemini API keys, with automatic ordered failover.
- The **LiteLLM in-memory response cache**.
- **SQLite** session history.

Every reply shows its own metadata: resolution source (`LIVE_INFERENCE` or `LLM_CACHE`), model, latency, and input and output tokens.

```
/backend             FastAPI + LiteLLM + SQLite (app/, tests/, Dockerfile)
/frontend            React + Vite + TypeScript (src/, Dockerfile, nginx.conf)
docker-compose.yml   backend :8000, frontend :3000, named volume app_data -> /data
Makefile             make up / make down (+ test, logs, dev-backend, dev-frontend)
.env.example         every environment variable, with placeholders
```

---

## 1. Live URLs

| Tier | URL |
|---|---|
| Frontend (Vercel) | https://chatbot-assign.vercel.app |
| Backend (Render) | https://lyftr-chat-backend-n2oa.onrender.com |
| Health check | https://lyftr-chat-backend-n2oa.onrender.com/health |

> The backend runs on Render's free tier, which normally sleeps after 15 minutes without traffic. To keep it awake, a free **UptimeRobot** monitor pings `GET /health` every 5 minutes. Requests are therefore fast, with no ~50 s cold start. If the monitor is ever paused, the first request after a long idle period can take ~50 s while Render wakes the service up.

---

## 2. Run locally

### With Docker (recommended)

Requirements: Docker (Docker Desktop or OrbStack).

```bash
cp .env.example .env        # then put 2+ Gemini keys in LLM_API_KEYS
make up                     # builds both images and starts the stack
```

- Frontend: http://localhost:3000
- Backend: http://localhost:8000/health

`make down` stops the stack. The SQLite file lives in the named volume `app_data`, mounted at `/data`, so history survives `make down` / `make up` and container restarts.

### Without Docker

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), and Node 22+.

```bash
cp .env.example .env        # add your keys
make dev-backend            # http://localhost:8000 (SQLite file: backend/app.db)
make dev-frontend           # http://localhost:3000 (in a second terminal)
make test                   # backend test suite
```

### Environment variables

| Variable | Meaning |
|---|---|
| `LLM_API_KEYS` | Comma-separated key pool, at least 2 keys. Key ids are `key_1`, `key_2`, … in this order. |
| `DATABASE_URL` | SQLite location, e.g. `sqlite:////data/app.db` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins (the frontend URL) |
| `ENABLE_FAILURE_SIMULATION` | `true` enables the `X-Simulate-Failure` test header |
| `LLM_MODEL` | LiteLLM model name used by every key (default `gemini/gemini-3.5-flash-lite`) |
| `DEFAULT_SYSTEM_PROMPT` | System prompt used when a session has none |
| `LLM_TIMEOUT_S` | Per-call provider timeout in seconds (default 30) |
| `VITE_API_BASE_URL` | Frontend build-time setting: the backend's public URL |

---

## 3. Design decisions

### Request flow

```
React UI ──POST /chat──▶ FastAPI
                           │  1. validate (422 on bad input)
                           │  2. load session history + system prompt from SQLite
                           │  3. messages = [system] + history + new message
                           ▼
                      LiteLLM Router ──cache read──▶ in-memory cache ──hit──▶ LLM_CACHE
                           │ miss
                           ▼
                key_1 ──fail──▶ key_2 ──fail──▶ key_3 ──fail──▶ 503 "all providers unavailable"
                  │ ok            │ ok            │ ok
                  ▼               ▼               ▼
            LIVE_INFERENCE (response written to cache)
                           │
                           ▼
      save user + assistant message in one SQLite transaction (successful requests only)
```

### Key fallback (LiteLLM Router)

- **Each key is its own Router deployment, named after its key id.** `LLM_API_KEYS=k1,k2,k3` becomes three deployments: `key_1`, `key_2` and `key_3`. Every request targets `key_1`, and the Router's `fallbacks={"key_1": ["key_2", "key_3"]}` tries the other keys **in order**. The fallback is LiteLLM's own feature; there's no hand-written retry loop.
- **What triggers a failover:** any provider error on a key, including 429 / quota, 5xx, timeouts, connection errors and an invalid key. If every key fails, the backend returns `503 {"detail": "all providers unavailable"}`, and the request is not saved.
- **`num_retries=0` and `disable_cooldowns=True`.**
  - No silent retries on the same key, so `fallback_attempts` is exact. It is the position of the key that answered: `key_2` means 1 key failed before it.
  - No global cooldowns, so one request's failure (real or simulated) never changes how the next request is routed.
- **`X-Simulate-Failure` is implemented as a LiteLLM `CustomLogger.async_pre_call_deployment_hook`.** The header is parsed into a per-request map that travels in the request's LiteLLM `metadata`. After the Router picks a deployment and before the provider is called, the hook raises the matching LiteLLM exception for that deployment:
  - `429` raises `RateLimitError`
  - `500` raises `InternalServerError`
  - `502` raises `BadGatewayError`
  - `503` raises `ServiceUnavailableError`

  A simulated failure therefore goes through exactly the same fallback path as a real one, and only affects that one request. The header is ignored unless `ENABLE_FAILURE_SIMULATION=true`. Malformed pairs are ignored.
- **Secrets:** logs and responses identify keys only by id (`key_1`, …), never by the key itself.

### Caching

- **LiteLLM's built-in in-memory cache** (`litellm.cache = Cache(type="local")`).
- **Cache key = model + the full message list** (system prompt + history + new message). All keys belong to one Router *caching group*, so a reply produced by `key_2` is a hit for the next request routed to `key_1`. The API key itself is not part of the cache key.
  - The same first message in a new session gives `LLM_CACHE`.
  - A different message, a different history or a different system prompt gives a different key, so it is never served from the cache.
- **On a hit,** the reply text is identical, the tokens are the usage stored with the cached response, `key_id` is `null` and `fallback_attempts` is `0`. A hit takes a few ms against ~0.5–2 s for a live call.
- **Cache reads are skipped while `X-Simulate-Failure` is set.** Otherwise a message that's already cached would return 200 from the cache under `*=429`, contradicting the spec's `*=429 → 503` row. The response is still written to the cache.
- **Two LiteLLM details are handled explicitly:**
  - LiteLLM writes cache entries in a background task. The backend waits for that write before responding, so an immediate repeat is a guaranteed hit.
  - LiteLLM lazily imports some modules on the first cache hit. The backend preloads them at startup, so even the first hit is faster than a live call.

### Token usage and latency

- `input_tokens` and `output_tokens` come from the provider's `usage` for **this request only**: prompt tokens for system + history + new message, and completion tokens for this reply. They are never a running total. In a long conversation `input_tokens` grows because the history grows, not because anything is summed.
- `latency_ms` is server-measured with `time.perf_counter()` around the whole request, as an integer.

### SQLite session storage

- **Tables:**
  - `messages`, exactly the spec's schema, plus an index on `(session_id, id)`.
  - A small extra `sessions` table holding each session's optional system-prompt override.
- **Schema:** created automatically on startup (`CREATE TABLE IF NOT EXISTS`). WAL mode is on.
- **Writes:** the user message and the assistant message (with its metadata JSON) are written **in one transaction, only after a successful reply**. A 422 or 503 never touches history, and a session whose only request failed returns 404.
- **History endpoint:** `GET /sessions/{id}/messages` returns history oldest first, without the system prompt. Assistant messages include their stored metadata.
- **Location:** set by `DATABASE_URL`. In Docker it is `/data/app.db` on the named volume.
- **Render caveat:** Render's free tier has no persistent disk, so the deployed SQLite file resets whenever Render starts a fresh container. Locally, the Docker volume keeps history across restarts.
- **Keep-alive:** a free UptimeRobot HTTP monitor calls `GET /health` every 5 minutes, so the free service never goes to sleep.
  - *Why it matters:* sleeping and waking starts a fresh container, which wipes the database. With the monitor in place, history survives between visits and there are no cold starts.
  - *Why `/health`:* it only runs a `SELECT 1` against SQLite, so the pings use no Gemini quota and add nothing to the cache or history.
  - *Cost:* one always-on free service uses ~720–744 instance hours a month, within Render's 750 free hours.
- **What the keep-alive doesn't cover:** redeploys (every push) and Render's own occasional restarts still start a fresh container. So on the free tier, deployed history is reliable in practice but **not guaranteed**. Attaching a Render disk at `/data` (a paid plan) would make it fully persistent, with no code changes.

### Model choice

The spec's example model, `gemini/gemini-2.0-flash`, has been **retired by Google**; the API returns *"models/gemini-2.0-flash is no longer available"*. `gemini-2.5-flash` and `gemini-2.5-flash-lite` are also closed to new users. The app uses **`gemini/gemini-3.5-flash-lite`**, the replacement Google recommends for the retired flash-lite:
- It's a pinned version, so results are reproducible.
- It answers in ~0.5–2 s.
- It doesn't use thinking tokens, so output-token counts stay meaningful.

`LLM_MODEL` can change the model without any code changes.

### Errors and validation

- **422:** missing or empty `session_id` / `message`, a whitespace-only message, more than 4000 characters after trimming, a `session_id` over 128 characters, a wrong type, or malformed JSON. FastAPI's default shape is used: `{"detail": [...]}`.
- **503:** `{"detail": "all providers unavailable"}`.
- **404:** `{"detail": "session not found"}`.
- **500:** a catch-all handler returns `{"detail": "internal server error"}`, so a stack trace or HTML error page never reaches the client.

---

## 4. Verification

Set `B` to the backend URL: `B=https://lyftr-chat-backend-n2oa.onrender.com` (or `http://localhost:8000` locally).

**Health**
```bash
curl -s $B/health
# {"status":"ok","keys_configured":3,"cache_enabled":true}
```

**Cache hit: the same first message in two new sessions**
```bash
curl -s -X POST $B/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-demo-1","message":"What is the capital of France?"}'
# "resolution_source":"LIVE_INFERENCE", "key_id":"key_1", "latency_ms": ~800

curl -s -X POST $B/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-demo-2","message":"What is the capital of France?"}'
# "resolution_source":"LLM_CACHE", same reply, same tokens, "key_id":null, "latency_ms": ~3
```

**Failover with `X-Simulate-Failure`**
```bash
curl -s -X POST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"s-demo-3","message":"Say hi"}'
# 200, "key_id":"key_2", "fallback_attempts":1

curl -s -X POST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=500' \
  -d '{"session_id":"s-demo-3","message":"Say hello"}'
# 200, "key_id":"key_2", "fallback_attempts":1

curl -s -X POST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=429,key_2=500' \
  -d '{"session_id":"s-demo-3","message":"Say hey"}'
# 200, "key_id":"key_3", "fallback_attempts":2

curl -s -i -X POST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"s-demo-4","message":"Say yo"}'
# HTTP 503 {"detail":"all providers unavailable"}
curl -s $B/sessions/s-demo-4/messages
# {"detail":"session not found"}   (nothing was stored)
```

**Multi-turn memory and per-message tokens**
```bash
curl -s -X POST $B/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-demo-5","message":"My name is Ada."}'
curl -s -X POST $B/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-demo-5","message":"What is my name?"}'
# the reply mentions Ada; input_tokens is larger than in the first turn because the history is included
curl -s $B/sessions/s-demo-5/messages
```

**Validation**
```bash
curl -s -X POST $B/chat -H 'Content-Type: application/json' -d '{"session_id":"x","message":"   "}'   # 422
curl -s -X POST $B/chat -H 'Content-Type: application/json' -d '{bad json'                          # 422
```

**In the UI**
- Every assistant reply shows chips for source, model, latency, and in/out tokens, plus a line with `key_id` and `fallback_attempts`.
- To see a cache hit: send a message, click **New chat** (or reload), and send the same message again. The chip switches to `LLM_CACHE`.
- To see failover: pick a preset in the **simulate failure** dropdown under the input. `key_1 → 429` shows `key: key_2 · fallback attempts: 1`, and `All keys → 429` shows a readable error in the chat.

**Automated tests**

`make test` runs the backend tests (pytest, 22 tests). They run the **real LiteLLM Router, fallbacks and cache**, with only the provider call replaced by LiteLLM's `mock_response`, so no network or keys are needed. They cover:
- every 422 case
- 404
- history order and session isolation
- history being sent to the model
- cache hits and misses, including "cache hit faster than live"
- `key_1=429`, `key_1=500`, `key_1=429,key_2=502` and `*=429`
- 503 leaving no history
- the simulation being per-request and switched off by the flag
- CORS
- header parsing

---

## 5. Setup Used

- **Editor:** Cursor
- **AI tools:** Claude Code (AI coding assistant)
- **Stack:** Python 3.13, FastAPI, LiteLLM 1.110.2 (Router + cache), SQLite; React 19, Vite, TypeScript; Docker multi-stage builds (uv → python-slim, node → nginx-alpine); Render (backend) and Vercel (frontend).

---

## 6. Bonus features

- **Failover simulator in the UI.**
  - A dropdown next to the input sends `X-Simulate-Failure` (`key_1 → 429`, `key_1 → 500`, `key_1 → 429, key_2 → 500`, `All keys → 429`).
  - Each reply also shows which key answered and how many keys failed first.
  - *Why:* resilience is the most important behaviour of this app, and this makes it visible and demonstrable in one click, without curl.
- **GitHub Actions CI** (`.github/workflows/ci.yml`).
  - On every push it runs the backend test suite, the frontend type-check and build, and a `docker compose build` of both images.
  - *Why:* it proves the contract tests and Docker builds stay green.
- **Three-key pool.** Failover across more than two keys can be demonstrated (`key_1 → key_2 → key_3`).
- **Small extras:**
  - a "New chat" button (a new session without reloading)
  - a session-level system prompt (the spec's optional feature, `data-testid="system-prompt-input"`)
  - light and dark themes
