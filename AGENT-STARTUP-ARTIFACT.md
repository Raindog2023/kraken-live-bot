# Goose Startup Artifact — OmniRoute + LLM

## Requirement
Every Goose session start must:
1. **Start OmniRoute server** if not already running
2. **Verify the LLM is functional** via OmniRoute

## OmniRoute Details
- **Server:** `http://127.0.0.1:20128` (loopback only)
- **Config/data:** `C:\Users\Rene\.omniroute\`
- **API key:** `OMNIROUTE_API_KEY` in `C:\Users\Rene\.omniroute\.env` (hash prefix: sk-d55bf185…)
- **Start command:** `omniroute --no-open` from `C:\Users\Rene` (NOT System32)
- **Health check:** `curl http://127.0.0.1:20128/api/monitoring/health` → `{"status":"healthy","setupComplete":true}`

## LLM Verification
- **Endpoint:** `http://127.0.0.1:20128/v1/chat/completions`
- **Auth header:** `Authorization: Bearer <OMNIROUTE_API_KEY>`
- **Model:** `openai/gpt-5.4-mini` (per `AGENT_MODEL` in `kraken-live-bot/.env`)
- **Test:** POST a minimal message → expect valid chat completion response

## Env Variables (from kraken-live-bot/.env)
```
AGENT_BASE_URL=http://127.0.0.1:20128/v1
AGENT_API_KEY=sk-d55bf185277ead7d-d43513-83d60ead
AGENT_MODEL=openai/gpt-5.4-mini
PAPER_TRADING=true
LIVE_TRADING=false
```

## Verified Functional (2026-10-23)
OmniRoute health ✅ / LLM chat completion ✅ (returned `"OK"`)