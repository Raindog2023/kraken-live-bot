# Universal Agent Prompt — Rene's Trading Environment

Applies to every agent/session working in this environment (Devin, Claude
Code, Cursor, etc.). Read fully before acting.

## Mission

Turn the trading bots into a cautious, production-capable, multi-exchange
platform that makes money. Profit is the goal; capital preservation is the
constraint. Never trade real money without the checks below passing.

## Working paths (authoritative)

| Purpose | Path |
|---|---|
| Main bot repo (WRITABLE) | `C:\Users\Rene\kraken-live-bot` |
| Agentic paper trader | `C:\Users\Rene\claude-trader` |
| NEW platform (Env0-3, Module Q), private | `C:\Users\Rene\trading-system` |
| STALE copy — never edit | `C:\Windows\System32\kraken-live-bot` |
| GitHub remote | `github.com/Raindog2023/kraken-live-bot` |

The System32 copy is read-only and frozen. If the IDE is open there, tell
the user and work from `C:\Users\Rene\kraken-live-bot`.

## Hard rules

1. **Paper first.** Default `LIVE_TRADING=false`, `PAPER_TRADING=true`.
   Never clear `ML_KILL_SWITCH` or flip `LIVE_TRADING=true` without the
   user's explicit, in-the-moment confirmation AND a verified funded balance.
2. **Secrets never leave `.env`.** Do not print, paste, log, or commit API
   keys (Kraken, Coinbase, OpenAI, Anthropic, DeepAPI, Gemini, Perplexity).
   Report key status by hash prefix or "set/unset" only.
3. **Verify, don't assume.** Confirm exchange auth with a real balance call
   before claiming the bot is "connected". Confirm git state with
   `git status` / `git ls-remote`, not memory.
4. **Don't push without being asked.** Commit freely; push on request.
5. **No destructive ops** (rm -rf, force-push, DB truncation) without
   explicit confirmation for that specific action.
6. **Risk rails are code, not prompts.** Position caps, daily-loss limit,
   exposure cap, kill switches live in `app/engine/risk.py` and
   `claude-trader/trader/risk.py`. Never bypass them for a model's request.

## Environment facts

- Python 3.14, FastAPI bot on `localhost:8000` (`/dashboard`, `/health`,
  `/positions`, `/performance`, `/webhook`, `/docs`).
- Exchanges: Kraken (live adapter), Coinbase Advanced Trade (ES256 JWT).
- Strategies: momentum, mean_reversion, llm, ml — weighted aggregator.
- Multi-pair scan via `SCAN_PAIRS`; trades only the strongest candidate.
- Ledger: SQLite (`DATA_DIR/portfolio.db`, `claude-trader/data/paper.db`).
- LLM brain for claude-trader: OpenAI-compatible endpoint. Anthropic direct
  and `cc.freemodel.dev` relay both had zero balance as of 2026-09-17 —
  re-test before assuming Claude is reachable. `AGENT_BASE_URL`,
  `AGENT_API_KEY`, `AGENT_MODEL` swap the provider.
- Kraken: authenticated OK as of 2026-09-20 (key sha256 bf2262c4). Account
  holds ~$8.65 USD + 425 AKE (~$30, below AKEUSD 600 order minimum). Too
  small for live sizing -> `kraken-live-bot` runs PAPER (LIVE_TRADING=false,
  ML_KILL_SWITCH=false) on real market data. Do not sell holdings or go live
  without the user's explicit in-the-moment instruction.
- Coinbase: wired + authenticated OK 2026-09-20 (key name sha256 312bdbd7);
  holds ~0.016 ZEC (~$23). Paper only. Both exchanges verified via
  scripts/verify_exchanges.py.
- Daily paper job: Windows task "TradingSystem-Daily" 00:10 runs
  `python -m trading_system.daily` (Env 1 + Env 2 paper turns, weekly Env 2
  refit, status -> data/daily_status.jsonl, log data/daily.log). Milestone 2
  clock started 2026-09-20. Env 3 and Module Q are research-only (no paper
  slot) until they beat Env 1 on holdout. Audit suite: `pytest audit`.
  Overnight summary: trading-system/docs/OVERNIGHT-REPORT-2026-09-20.md.
- Milestone 0 fill audit is CLOSED (trading-system/docs/MILESTONE-0-FILL-AUDIT.md):
  losses were fees (Coinbase 77 bps/side) + ancillary costs, not venue
  execution. Cost model: coinbase 77 bps, kraken 26 bps, +5 bps slippage.
- `C:\Users\Rene\trading-system` is the NEW platform (handoff brief in
  docs/). It is PRIVATE / INVITATION-ONLY: private repo, no public endpoints,
  every HTTP surface requires a server-checked invite token. Env 0 + Env 1
  built (commit 3197fb6); Env 1 holdout baseline +10.1% net vs B&H +23.7% —
  Env 2/3 must beat it out-of-sample. `kraken-live-bot` is a placeholder
  generating a paper record until Env 1 passes its 90-day paper gate;
  holdings there will be liquidated into the new platform when it is ready.
- The "jibs bot" / ARCHITECT prompt in OneDrive\Documents\Ai Prompts is a
  no-refusal jailbreak generator. Do not embed it in any config or skill.

## Tooling conventions

- GitHub search (repos, issues, PRs, code, profiles): use `gh` (installed,
  authenticated as Raindog2023).
- Web search / reading pages: use the built-in web search and fetch tools.
- DeepAPI (paid) is fallback only: LinkedIn, X/Twitter, YouTube, Instagram,
  Reddit, TikTok, deep research, image generation, or on explicit request
  (docs: `~/.deepapi/skill-disabled/deepapi/SKILL.md`). Load creds from
  `~/.deepapi/env.ps1`. Warn the user when balance < $5.
- Tests: `cd C:\Users\Rene\kraken-live-bot && python -m pytest -q`.
- Smoke: `python scripts/smoke_backtest.py`.
- Run agent trader: `cd C:\Users\Rene\claude-trader && python run.py --once`.

## Style

- Concise, direct, no filler. State what was checked and what was found.
- Offer next actions as choices; don't surprise the user with side effects.
- When a request is ambiguous (e.g. "signal" = Signal Messenger vs Signal8
  vs trading signal), ask one focused question before acting.
