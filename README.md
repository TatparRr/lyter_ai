# LYFTER AI Chat

## 1. Live URLs
- Frontend: `https://YOUR-FRONTEND.example.com`
- Backend: `https://YOUR-BACKEND.example.com`

Replace these placeholders before submission.

## 2. 3. 4. 5. 6. Run locally

### Docker
```bash
cp .env.example .env
make up
```
Open http://localhost:3000. API: http://localhost:8000.

Use `make down` to stop containers. SQLite is stored in the `app_data` named Docker volume.

### Without Docker
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
DATABASE_URL=sqlite:///./app.db ENABLE_FAILURE_SIMULATION=true uvicorn app.main:app --app-dir backend --reload --port 8000
```
In another terminal:
```bash
cd frontend
npm install
npm run dev -- --port 3000
```

With no keys, the backend returns safe demo responses. Add at least two real comma-separated keys to `LLM_API_KEYS` for real inference.

## Design decisions: key fallback, caching, SQLite session storage
LiteLLM calls providers sequentially; 429 and 5xx conditions fall through to the next key. A per-request simulation header is parsed only when enabled and never changes real key configuration. Responses are cached in memory using a SHA-256 key based on model plus the complete system/history/user message list. SQLite stores ordered user and assistant rows per session; cache hits and live responses never alter the API shape.

## Verification: how to see failover, cache hits and per-message token usage
1. Send the same message in two different sessions. The second response should have `LLM_CACHE`, identical reply text, and `key_id: null`.
2. Send with `X-Simulate-Failure: key_1=429` and two configured keys. The response should come from `key_2`.
3. Send `X-Simulate-Failure: *=429`. It should return 503 `{"detail":"all providers unavailable"}` and add no history.
4. Open developer tools/network to verify input/output token fields and the metadata shown beside each assistant reply.

Example:
```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/chat -H 'Content-Type: application/json' -d '{"session_id":"demo","message":"What is the capital of France?"}'
```

## Setup Used: your editor and AI tools
VS Code, React/Vite, FastAPI, SQLite, LiteLLM, Docker Compose, GitHub Copilot.

## Bonus features (if any)
- Optional system prompt field
- Demo fallback without API keys
- Durable Docker volume and clean responsive UI
