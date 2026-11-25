# TITAN AI PREMIUM — Demo USD-M Futures Worker

## Scope

The continuous worker supports **Demo/Shadow Spot** and **Demo/Shadow USD-M Futures**. Futures discovery and analysis use Binance public endpoints only. The worker does not read Binance API credentials and does not submit Testnet or Live orders.

## Selecting Futures mode

1. Keep the environment and account target set to `demo`.
2. Set the selected market to `futures`.
3. Start the Master worker through the normal authenticated `/api/master/start` route.
4. The supervisor launches the worker for the selected market. A market mismatch or Emergency Stop prevents startup.

For a local CLI run, first select `futures` in the Demo system state, then run:

```bash
python -m app.scripts.run_demo_loop --market futures --interval 300
```

The manual `/api/demo/cycle` route also accepts `futures`, but only when it matches the selected market state.

## Futures discovery and analysis

- `GET /api/scanner/futures`: public read-only discovery of liquid USDT perpetuals.
- `GET /api/scanner/smart-futures`: authenticated multi-timeframe Futures analysis; requires Demo/Futures to be the selected state.
- Discovers active, trading **USDT-margined perpetual contracts** only.
- Ranks candidates by 24-hour USDT quote volume.
- Requires verified public candles on 1D, 4H, 1H, 15M and 5M.
- Validates current mark price and funding rate before analysis.
- Uses the existing decision and risk engines for Long/Short/Wait decisions.
- Existing open positions are processed before new candidates, even when the scanner finds no new entry signal.

## Safety limits

- Maximum gross notional per Futures position: **800 USDT**, including added entries.
- Maximum portfolio gross exposure: configured portfolio exposure multiple (default **2× initial Demo equity**).
- Maximum leverage: configured global cap (default **10×**).
- Default Futures risk budget: **0.5% of Demo equity per trade**, scaled down when available isolated margin falls.
- New entries are blocked by daily-loss limits, open-position limits, Emergency Stop, malformed risk configuration, stale data, or failed analysis.
- Opposite-side signals close the existing position with reduce-only semantics and do not reverse in the same cycle.
- Verified funding history is settled once per funding event. The payment amount is an estimate using the latest validated mark, not a historical funding-time mark.
- Isolated-margin and liquidation prices are internal estimates. Maintenance margin, fee tiers, bracket tiers, mark-price behavior and liquidation execution can differ from Binance's exchange calculations. They are not a substitute for exchange-side protective orders.

## Emergency behavior

The worker stops and latches Emergency Stop on critical public-data, mark-price, funding-history, analysis, liquidation-state, or liquidation-close failures. Position reductions remain available in the Shadow engine while new entries are blocked.

## Explicit non-goals

This worker does not enable Binance Testnet, Live trading, authenticated private endpoints, API-key handling, or external order submission. Any future real-money activation requires a separate explicit approval and a separate readiness review.
