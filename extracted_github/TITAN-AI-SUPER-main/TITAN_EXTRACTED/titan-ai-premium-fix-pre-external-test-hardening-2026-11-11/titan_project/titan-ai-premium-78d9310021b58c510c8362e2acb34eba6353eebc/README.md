# TITAN SUPER AI PREMIUM (Foundation Build)

This workspace contains an **integrated, extensible foundation** for TITAN SUPER AI PREMIUM (Backend + Frontend + SQLite + tests/docs). It follows the core safety and architecture requirements from the master specification, but **it is not the full final system**. Some advanced engines/integrations remain partial or require external services.

> Historical reports dated 2026-11-07 are retained as records of earlier source/archive snapshots. Their test totals, archive names, and completion claims are not current release evidence; use the Verification section below for the latest checks.

## What is real in this build
- **Execution separation**: internal Demo/Shadow and Binance Demo/Testnet are separate account targets. Testnet starts the supervised execution worker after verified connection. Binance Live credentials, if present, are restricted to read-only monitoring; Live order execution is hard-disabled in this release and cannot be enabled by environment variables or dashboard controls. Auto Demo→Live is disabled. Withdrawal/transfer permissions are not supported.
- **TITAN AI CORE foundation**: engine registry/status, system state, audit logging, knowledge/learning records (SQLite).
- **Working analysis pipeline (tested)** using **LABELED SYNTHETIC** candles for research/testing and **Binance Public read-only** candles for live market analysis:
  - Multi-timeframe provider supports all 15 configured timeframes; the primary decision hierarchy is 1D/4H/1H/15M/5M. It supports labeled synthetic data and Binance Public klines (read-only, no API key). In-progress candles are excluded from public-data analysis.
  - Technical analysis (EMA 9/21/50/200, SMA, VWAP, Supertrend, Ichimoku, RSI, MACD, Stoch RSI, CCI, Bollinger, ATR, candle patterns, confirmed-pivot BOS/CHOCH, liquidity sweeps, FVG, order-block candidates, and market-regime heuristics).
  - Risk/cost engine (estimates; fee/slippage assumptions labeled; leverage capped; Spot has no leverage; Earn not treated as trade execution).
  - Decision engine (BUY/SELL/LONG/SHORT/WAIT) with rationale, confidence note, and data-source warning.
- **Backend API code** (Starlette runtime with service layer) for status, state, strategies, analysis, decisions, audit, knowledge, learning, and read-only Smart Scanner.
- **Frontend** (React + Vite, mobile-responsive PWA shell) that consumes `/api/*` and shows honest statuses (no fake data).
- **Supervised Demo/Shadow worker**: Master Start/Stop/Emergency Stop controls a joinable in-process worker for the selected Spot or USD-M Futures market. Automatic mode uses the Spot BUY scanner or the dedicated USDT-perpetual Futures LONG/SHORT scanner; existing Shadow positions are cycled first for protection. It uses public read-only data and internal Shadow records only; it never sends Binance orders. The verified Binance Testnet worker starts automatically after successful credential verification; BUY/SELL entry, automatic position management, durable idempotency, order reconciliation, and tracked-order cancellation use one supervised execution service.
- **Smart Scanners**: bounded liquid Spot discovery and a separate active USDT-perpetual Futures discovery path; both analyze 1D/4H/1H/15M/5M with freshness/coverage checks, ranked results and explicit skipped-candidate reasons.
- **Backtesting**: six implemented strategy rules (trend, momentum, mean reversion, structure breakout, M5 scalping, and H1/H4 swing) are connected to causal next-candle-open simulation with fees/slippage and rolling Walk-Forward/OOS folds. Futures simulation additionally models funding, partial fills, and liquidation estimates. External signal callbacks are explicitly marked as not lookahead-verified unless generated from causal history prefixes.

## What remains incomplete or requires external configuration

- **Binance Testnet execution:** Spot/Futures order submission and automatic position management are enabled through the supervised worker after verified Testnet credentials. The worker rechecks account trade permission, risk limits, order state and exchange reconciliation. It supports signed order cancellation and fails closed on uncertain outcomes. No user-credentialed real Testnet order has yet been submitted in CI; the user must verify the connection with their own Testnet keys.
- **Futures margin mode:** before every new Futures Testnet position/order, TITAN requests Binance `ISOLATED` margin for that symbol and fails closed if the exchange refuses. Binance's `-4046` response is treated as confirmation that the symbol is already isolated. Reduce-only close orders deliberately skip margin/leverage changes so an existing Cross position can still be closed safely. These paths are covered by deterministic unit tests but have not yet been exercised with user-provided Futures Testnet credentials; there is no independent post-change position-risk read-back yet.
- **Still deferred:** exchange-side modify, exchange-native SL/TP/trailing orders, WebSocket execution stream, and fully verified external reconnect/resynchronization. Tracked exchange-order cancellation is implemented through the supervised execution service; it is not yet independently verified with user-supplied Binance keys. The Futures Testnet opening path requests ISOLATED margin and applies the configured leverage before submitting a new position; reduce-only closes deliberately skip margin/leverage changes so an existing Cross position can still be closed. These controls have deterministic unit coverage but no user-credentialed exchange verification or independent post-change position-risk read-back yet. Demo/backtest margin and liquidation figures remain estimates. Live credentials may be connected for read-only account monitoring and continuous market analysis. Real-money execution is hard-disabled in this release at the server configuration layer; no dashboard action or environment variable can enable Live orders. Re-enablement requires a future release after your explicit decision and a dedicated safety review.
- **Still partial:** knowledge-source verification/provenance; full continuous-learning integration into strategy parameters; liquidity/order-flow strategy depth (current order-flow evidence is recent aggregate-trade taker flow, not a full synchronized depth stream); complete on-chain/tokenomics due diligence; broad news coverage and economic-calendar/NLP analysis; exchange-exact Futures liquidation/funding behavior (the Demo worker uses conservative estimates, and funding payments use the latest mark rather than a historical funding-time mark); wider ML calibration/OOS validation; arbitrage execution; and full agent orchestration.
- **Research-only outputs:** performance analytics use the internal Demo/Shadow ledger; prediction tracking is a single mark-to-market observation, not realized PnL. The ML model is a baseline and cannot independently authorize trades.
- **Local/OpenAI AI runtime:** OpenAI remains the primary read-only evaluator; a local Ollama-compatible model is used only when its loopback runtime is reachable and the configured model is installed. The setup helper and offline boundaries are documented in [Local AI setup](docs/LOCAL_AI_SETUP.md). A local model can evaluate an already-available analysis context offline, but fresh Binance candles/news cannot be refreshed without internet; synthetic candles remain clearly labeled and are not real market data.
- **External configuration:** Telegram requires a Bot Token and allowlisted Chat IDs in server environment variables. OpenAI is the primary read-only AI evaluator; its key can be entered in the app and is encrypted server-side with a strong `TITAN_SECRET_KEY`, or supplied through an environment-variable reference. The local loopback model is fallback only. Public CoinGecko/RSS endpoints have coverage and rate-limit constraints.
- **Deployment:** production multi-process worker supervision, 24/7 monitoring, and Android APK packaging remain separate work. The current worker is managed inside one application process.

## Verification

Latest merged source at the start of this audit: `c07934a17dde5220a6df47ff18c0b0907b410ff0` (2026-11-26).

- **Pull-request CI:** run [#45744952405](https://github.com/aloa39847-source/Atlas/actions/runs/45744952405) completed successfully on the final reviewed PR head. All five jobs passed: backend compile/tests, frontend tests and production builds, production container build, full-cycle isolated Demo Shadow smoke (Spot + Futures), and the 10-minute Demo Shadow stability test.
- **Main-branch package:** run [#45747602823](https://github.com/aloa39847-source/Atlas/actions/runs/45747602823) completed successfully after merge. It created and verified the single `ATLAS_AUTONOMOUS_TRADER_FINAL` ZIP artifact (SHA-256: `545369a8b9f3c286ae617d81ef711242d10a87bdf49b7e718279d2f01a17f11a`).
- **Risk and execution safety:** daily loss is capped at 7%. Spot per-trade notional is capped at 100 USDT. Futures initial margin is capped at 30 USDT per entry; the strategy engine estimates leverage up to the hard 10x ceiling, and both the worker and independent pre-trade gateway enforce notional no greater than 30 USDT multiplied by the selected leverage (maximum 300 USDT). Binance Live order execution is hard-disabled in this release; no Live order was submitted.
- **News freshness:** decision news context rejects missing, invalid, future-dated, or older-than-60-minute original publication timestamps.
- **Strategy coverage:** the catalog includes standard and advanced strategy rules plus an ICT/SMC candidate model. ICT and data-dependent strategies remain partial where required market evidence is unavailable; registration does not imply every strategy has generated a valid live signal.

These checks prove code/build/test behavior only. Demo Shadow smoke and stability runs are isolated internal simulations; they are not user-credentialed Binance Testnet order-lifecycle tests, profitability evidence, or Live trading validation.

### Not yet externally verified or production-ready

- No user Binance Testnet credentials were supplied to this CI run. No real Testnet order lifecycle was exercised; the current order/close regressions use mocked adapters or internal Shadow records.
- **Current account lifecycle:** internal Demo uses Shadow records; verified Binance Testnet credentials start the supervised automated execution worker; verified Binance Live credentials, if configured, are read-only. Real-money order execution is hard-disabled in this release.
- OpenAI is the primary read-only AI evaluator when configured; no API key is committed or supplied by CI. The local loopback fallback has not been verified on Android/Termux.
- The frontend is packaged as an installable PWA and served from the same origin as the API. It has not yet been deployed to a public HTTPS URL, and no native Android APK has been built.
- Render's current free-plan configuration uses ephemeral storage. SQLite records and locally stored encrypted credentials can be lost on restart or redeploy; do not rely on this plan for persistent or continuous trading. Configure persistent storage/database and deployment secrets before using the hosted service.
- Historical reports in this repository describe older snapshots and are not current release evidence. No new release ZIP or published release artifact is claimed by these CI runs.

These checks prove code/build/test behavior only; they do not prove profitability or real-exchange execution. Never present synthetic/test data as live market data.

## Run (standard environment)
### Backend
From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r backend/requirements.txt
cp .env.example .env
# Edit .env: replace owner@example.com and set a unique random TITAN_SECRET_KEY (32+ characters).
# The app loads this project-root .env automatically; process environment variables take precedence.
uvicorn app.main:app --app-dir backend --reload --host 0.0.0.0 --port 8000
```

Credential encryption remains disabled until a strong non-placeholder `TITAN_SECRET_KEY` is configured.

### Frontend
```bash
cd frontend
npm install
npm run dev
```
Vite proxies `/api` to `http://127.0.0.1:8000` by default.

## Safety rules
- Never commit `.env`, API keys, Telegram tokens, or secrets.
- Use Binance Demo/Testnet before any live environment; verify environment support.
- No auto demo→live. No withdrawal permissions.
- Do not present synthetic/test data as real market data.
- No profit guarantees.

## Reference and backup handling

The immutable reference ZIP remains at the repository root, outside this project directory. The release workflow packages tracked files from this project tree, includes the tracked `recovered/` material, and explicitly excludes nested ZIP archives. Untracked runtime data and credentials are not added because packaging uses `git ls-files`. The root reference archive remains unchanged.
