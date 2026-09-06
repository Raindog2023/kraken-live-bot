# Kraken Live Bot

Live XBTUSD scanner. `ai-trader` cannot place Kraken orders; this service can.

Dashboard: `/dashboard`

Required Render env:
- `KRAKEN_API_KEY`
- `KRAKEN_API_SECRET`
- `KRAKEN_BASE_URL=https://api.kraken.com`
- `ANTHROPIC_API_KEY` (primary) plus optional OpenAI/Gemini/Perplexity
- `LIVE_TRADING=true`
- `PAUSED=false`
- `WEBHOOK_SECRET`

Scans BTC every 5 minutes. BUY/SELL at confidence >= 65 submits a **$50** market order on Kraken (buy uses quote volume / viqc).
