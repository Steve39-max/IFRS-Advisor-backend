# IFRS Advisor Backend

Milestone 1: a FastAPI backend that talks to OpenAI, ready to deploy on Railway
and be called from the IFRS Advisor site.

## Endpoints

- `GET /health` - returns `{"status": "ok"}`
- `POST /api/ask` - body `{"question": "..."}` -> `{"answer": "..."}`

## Run locally

```
pip install -r requirements.txt
cp .env.example .env   # then fill in OPENAI_API_KEY
uvicorn app.main:app --reload
```

Test it:

```
curl -X POST http://localhost:8000/api/ask -H "Content-Type: application/json" -d "{\"question\": \"What does IAS 1 require for a complete set of financial statements?\"}"
```

## Deploy to Railway

1. Push this repo to GitHub (see below).
2. In Railway: New Project -> Deploy from GitHub repo -> select `IFRS-Advisor-backend`.
3. Railway auto-detects Python and uses the `Procfile` to start the server.
4. In the Railway project's Variables tab, add:
   - `OPENAI_API_KEY` = your OpenAI key
   - `OPENAI_MODEL` = `gpt-4o-mini` (optional, this is the default)
   - `ALLOWED_ORIGINS` = the URL(s) of your frontend site, comma-separated
5. Railway gives you a public URL once deployed (Settings -> Networking -> Generate Domain).
6. Confirm it's alive: `https://<your-railway-domain>/health`

## Wiring the frontend

Point the site's chat feature at `POST https://<your-railway-domain>/api/ask`
with `{"question": "..."}` and render the `answer` field in the response.
