from pathlib import Path
import json
import sys

root = Path(sys.argv[1])
knowledge_dir = root / "app/knowledge"
knowledge_dir.mkdir(parents=True, exist_ok=True)

def C(domain, key, title, summary, details, tags, sources, caution):
    return {
        "key": key, "category": domain, "title": title, "summary": summary,
        "details": details, "tags": tags, "source_keys": sources,
        "verified": False, "confidence": 0.0,
        "confidence_note": "Seed educational note; source review and empirical validation are pending.",
        "caution": caution,
    }

entries = [
C("market_structure","market_structure_swing_confirmation","Swing-point confirmation",
  "A swing high or low is a local pivot confirmed only after enough candles form on both sides. The confirmation delay means the latest apparent pivot can change before it is confirmed.",
  ["Keep confirmed and provisional pivots separate.","Do not use future candles when evaluating a historical signal."],
  ["swing_high","swing_low","pivot","repainting"],["binance_spot_api","cme_liquidity"],"Swing points are heuristic and depend on the chosen lookback window."),
C("candlesticks","candlesticks_body_wick_metrics","Candle anatomy and close strength",
  "A candle is defined by open, high, low and close. Body size, upper and lower wick proportions, range and close location describe rejection and closing pressure.",
  ["Normalize wick and body measurements by the candle range.","A strong close near the high or low is context, not a standalone entry signal."],
  ["OHLC","body","wick","close_strength"],["binance_spot_api","cme_liquidity"],"Candle shape alone does not establish buyer or seller intent."),
C("technical_indicators","technical_indicators_trend_averages","Moving averages for trend context",
  "Simple and exponential moving averages smooth price over a chosen lookback. Their slope, ordering and price relationship can describe trend context but lag turning points.",
  ["Use the same candle-close convention across timeframes.","Avoid treating moving-average crosses as guaranteed predictions."],
  ["EMA","SMA","trend","lagging"],["binance_spot_api"],"Moving averages are derived from historical prices and can whipsaw in ranges."),
C("technical_indicators","technical_indicators_momentum_oscillators","Momentum oscillators",
  "RSI and MACD summarize recent momentum using different transformations of price history. Divergence and overbought or oversold readings require trend and regime context.",
  ["RSI is bounded; MACD is not.","A divergence can persist while price continues trending."],
  ["RSI","MACD","momentum","divergence"],["binance_spot_api"],"Oscillator extremes are not automatic reversal signals."),
C("technical_indicators","technical_indicators_volatility_tools","Volatility indicators",
  "ATR estimates recent true-range movement, Bollinger Bands describe dispersion around a moving average, and ADX estimates trend strength rather than direction.",
  ["Use ATR to scale stops and compare volatility across assets.","Do not interpret band touches or ADX thresholds without price structure."],
  ["ATR","Bollinger","ADX","volatility"],["binance_spot_api"],"Indicator periods and thresholds need out-of-sample testing."),
C("chart_patterns","chart_patterns_continuation_reversal","Continuation and reversal formations",
  "Triangles, flags, channels, double tops and head-and-shoulders are geometric descriptions of price compression or failed continuation. Their usefulness depends on breakout confirmation and invalidation.",
  ["Define pivots and neckline mechanically before testing.","Measure false-break frequency and post-break slippage."],
  ["flags","triangles","double_top","head_shoulders"],["cme_liquidity","binance_spot_api"],"Pattern names are subjective unless detection rules are explicit."),
C("volume","volume_relative_baseline","Relative volume",
  "Relative volume compares current traded volume with a historical baseline for the same interval. It helps distinguish ordinary movement from unusually active trading.",
  ["Compare like-for-like time intervals.","Exchange-reported volume may differ across venues and quote currencies."],
  ["relative_volume","volume_sma","participation"],["binance_spot_api","cme_liquidity"],"Volume is venue-specific and can be distorted by wash trading or fragmented liquidity."),
C("volume","volume_vwap_obv","VWAP and on-balance volume",
  "VWAP weights price by traded volume over a defined session or rolling window. OBV accumulates signed volume as a rough participation measure; both are descriptive rather than causal.",
  ["Specify the session reset and data source for VWAP.","Use OBV divergence as a secondary observation, not a trade trigger."],
  ["VWAP","OBV","volume_weighted"],["binance_spot_api"],"Crypto trades continuously, so session definitions must be explicit."),
C("order_flow","order_flow_trade_delta","Trade delta and cumulative volume delta",
  "Trade delta estimates aggressive buying volume minus aggressive selling volume from trade-side classification. Cumulative delta aggregates that imbalance over time.",
  ["Document how buyer-initiated and seller-initiated trades are classified.","Compare delta with price response to identify possible absorption."],
  ["delta","CVD","aggressive_flow"],["binance_spot_api","cme_liquidity"],"Trade-side inference can be noisy when exchange feeds omit reliable aggressor flags."),
C("order_flow","order_flow_absorption_imbalance","Order-book imbalance and absorption",
  "Displayed depth imbalance compares resting bid and ask quantities. Absorption describes heavy aggressive flow that produces limited price progress near a level.",
  ["Measure depth across multiple levels and normalize by total depth.","Depth can be cancelled before execution; verify with actual trades."],
  ["imbalance","absorption","order_book"],["cme_liquidity","binance_spot_api"],"Displayed liquidity is not guaranteed executable liquidity."),
C("liquidity","liquidity_depth_spread","Depth and spread",
  "Liquidity is reflected in the bid-ask spread, executable depth and the amount that can trade without moving price materially. Thin books increase slippage and execution risk.",
  ["Measure depth at several price levels.","Estimate cost for the intended order size, not only the top-of-book spread."],
  ["depth","spread","market_liquidity"],["cme_liquidity","cme_liquidity_measure"],"Order-book snapshots can become stale between updates."),
C("liquidity","liquidity_sweep_confirmation","Liquidity sweeps and reclaim",
  "A wick beyond a prior swing followed by a close back inside can indicate a failed breakout or stop run. Confirmation requires the close and subsequent price behavior.",
  ["Record the swept level and the candle close separately.","Distinguish a wick-only sweep from a confirmed close beyond the level."],
  ["sweep","false_breakout","stop_run"],["binance_spot_api","cme_liquidity"],"A sweep label is a price-action hypothesis, not proof of stop orders being triggered."),
C("market_microstructure","microstructure_tick_queue","Tick size and queue priority",
  "Tick size constrains valid prices, while queue priority determines which resting orders execute first at a price level. Small price improvements can change queue position and fill probability.",
  ["Load exchange filters and tick sizes dynamically.","Model price-time priority and exchange-specific exceptions."],
  ["tick_size","queue_priority","filters"],["binance_spot_api","binance_usdm_order"],"Matching rules differ across venues and can change."),
C("market_microstructure","microstructure_market_impact","Market impact",
  "Market impact is the price movement caused by executing an order. It depends on order size, depth, volatility, urgency and venue conditions.",
  ["Estimate impact from depth and historical fills.","Separate temporary impact from broader market movement where possible."],
  ["market_impact","slippage","execution_cost"],["cme_liquidity_measure","binance_spot_api"],"Impact estimates are uncertain and regime-dependent."),
C("market_microstructure","microstructure_matching_engine","Matching engine and order lifecycle",
  "An exchange matching engine accepts, validates, queues, matches and updates orders. Client acknowledgements and final execution reports are distinct events.",
  ["Track NEW, PARTIALLY_FILLED, FILLED, CANCELED and EXPIRED states.","Treat timeouts as unknown until queried by client order ID."],
  ["matching_engine","order_lifecycle","execution_report"],["binance_spot_api","binance_usdm_order"],"A network timeout does not prove an order was rejected."),
C("spot_market","spot_market_asset_ownership","Spot ownership and settlement",
  "A spot purchase exchanges quote assets for the base asset, subject to venue custody and account rules. Spot exposure is not the same as a leveraged derivative position.",
  ["Track base and quote balances separately.","Consider custody, withdrawal and venue counterparty risks."],
  ["spot","base_asset","quote_asset"],["binance_spot_api","cftc_crypto_risks"],"Exchange balances represent claims on the venue until assets are withdrawn on-chain."),
C("spot_market","spot_market_balance_reservation","Free, locked and available balances",
  "Spot account balances can be divided into free and locked amounts. Open orders may reserve assets, so the displayed total is not necessarily available for another trade or withdrawal.",
  ["Use free balance for sell sizing.","Reconcile locked balances with open orders before placing new orders."],
  ["free_balance","locked_balance","available_balance"],["binance_spot_api"],"Balance updates may be delayed relative to order events."),
C("spot_market","spot_market_exchange_filters","Spot symbol filters",
  "Exchanges impose symbol-specific quantity steps, tick sizes, minimum quantities and notional limits. Orders must be rounded and validated against current filters before submission.",
  ["Fetch exchangeInfo and apply LOT_SIZE, PRICE_FILTER and NOTIONAL rules.","Reject rather than silently round a quantity beyond the user's intent."],
  ["LOT_SIZE","PRICE_FILTER","NOTIONAL"],["binance_spot_api"],"Filters vary by symbol and may change without notice."),
C("futures_market","futures_perpetual_vs_delivery","Perpetual and dated futures",
  "Perpetual contracts have no scheduled expiry and use funding mechanisms to influence their relationship with spot. Dated futures expire and converge through their settlement process.",
  ["Read the contract specification and settlement method.","Do not assume all futures contracts are linear or quote-margined."],
  ["perpetual","delivery","settlement"],["binance_usdm_order","cftc_futures_basics"],"Contract design differs between venues and products."),
C("futures_market","futures_mark_index_last_price","Mark, index and last price",
  "Last traded price reflects the latest match, index price references an underlying basket, and mark price is used by many derivatives venues for margin and liquidation calculations.",
  ["Use mark price for liquidation-risk monitoring where the exchange specifies it.","Track divergence between mark, index and last price."],
  ["mark_price","index_price","last_price"],["binance_usdm_mark_price","cftc_futures_basics"],"The exact mark-price methodology is exchange- and contract-specific."),
C("futures_market","futures_contract_specification","Futures contract specifications",
  "A futures contract defines symbol, multiplier, quote and margin assets, quantity precision, settlement, funding and position modes. These properties determine exposure and PnL calculations.",
  ["Load specifications per symbol rather than assuming BTCUSDT conventions.","Distinguish linear USDT-margined and inverse coin-margined contracts."],
  ["contract_multiplier","linear","inverse"],["binance_usdm_order","cftc_futures_basics"],"Never reuse one contract's sizing formula for another without checking its specification."),
C("leverage_margin","margin_initial_maintenance","Initial and maintenance margin",
  "Initial margin is required to open a leveraged position; maintenance margin is the minimum equity needed to keep it open. Falling below maintenance requirements can trigger liquidation.",
  ["Compute margin from notional and leverage, then apply exchange tier rules.","Monitor maintenance margin and margin ratio continuously."],
  ["initial_margin","maintenance_margin","margin_ratio"],["cftc_futures_basics","cftc_margin"],"Exchange tiers and collateral haircuts can change the required margin."),
C("leverage_margin","margin_isolated_cross","Isolated and cross margin",
  "Isolated margin assigns collateral to a specific position, while cross margin shares eligible account collateral across positions. Cross margin can expose more account equity to a losing position.",
  ["Use isolated margin for bounded position-level collateral where supported.","Do not infer liquidation safety from leverage alone."],
  ["isolated_margin","cross_margin","collateral"],["binance_usdm_order","cftc_futures_basics"],"Margin modes and risk treatment vary by exchange."),
C("liquidation","liquidation_price_mechanics","Liquidation price mechanics",
  "Liquidation occurs when the exchange's mark-price and margin rules determine that account equity no longer meets maintenance requirements. Fees, funding, other positions and tier brackets can move the effective threshold.",
  ["Use the exchange's position-risk response when available.","Keep a safety buffer from the estimated liquidation price."],
  ["liquidation_price","mark_price","maintenance_margin"],["binance_usdm_mark_price","cftc_futures_basics"],"Simplified formulas are estimates and must not replace exchange account data."),
C("liquidation","liquidation_insurance_adl","Insurance funds and auto-deleveraging",
  "Some derivatives venues use insurance funds and auto-deleveraging to manage losses when liquidation execution cannot fully cover obligations. ADL can reduce profitable counterparty positions.",
  ["Monitor exchange-specific ADL indicators and liquidation notices.","Avoid assuming a stop order guarantees an exit during extreme markets."],
  ["insurance_fund","ADL","liquidation"],["binance_usdm_user_stream","cftc_crypto_risks"],"Mechanisms differ by venue and can change during stress."),
C("funding","funding_rate_sign_and_cost","Funding-rate direction and cost",
  "Perpetual funding transfers payments between long and short holders at scheduled times. The sign determines which side pays, while the rate and position notional determine the approximate payment.",
  ["Calculate funding using the contract's current notional and settlement schedule.","Funding can change before the next timestamp."],
  ["funding_rate","funding_payment","perpetual"],["binance_usdm_funding","cftc_futures_basics"],"Funding is variable and not a guaranteed yield."),
C("funding","funding_basis_carry","Basis and carry",
  "Basis is the difference between futures and spot prices. It can reflect funding expectations, interest rates, inventory, risk and market imbalances.",
  ["Compare like-for-like contract and spot references.","Include fees, funding, borrow and settlement in any carry estimate."],
  ["basis","carry","contango","backwardation"],["binance_usdm_funding","cme_liquidity_measure"],"Basis trades are exposed to convergence and execution risk."),
C("order_types","order_types_market_limit","Market and limit orders",
  "Market orders prioritize execution and accept available prices; limit orders constrain price but may not fill. The choice trades off immediacy against price certainty.",
  ["Estimate slippage and depth before market orders.","Do not assume a limit order will execute just because price touches its limit."],
  ["market_order","limit_order","fill_probability"],["binance_spot_api","finra_order_types"],"Order behavior and protections vary across exchanges."),
C("order_types","order_types_stop_trigger","Stop and stop-limit orders",
  "A stop order activates after a trigger condition; a stop-limit then submits a limit order, which may remain unfilled. Trigger reference and execution rules are venue-specific.",
  ["Distinguish trigger price from execution price.","Stress-test gaps and fast markets where stop-limit orders can miss execution."],
  ["stop_market","stop_limit","trigger_price"],["binance_spot_api","finra_order_types"],"A stop order is not a guarantee of a specific execution price."),
C("execution","execution_slippage_model","Slippage modeling",
  "Slippage is the difference between an expected reference price and the actual fill. It can arise from spread, depth, volatility, latency and order size.",
  ["Model slippage by symbol, side, order type and volatility regime.","Use actual fill records to recalibrate estimates."],
  ["slippage","fill_price","spread"],["cme_liquidity_measure","binance_spot_api"],"Historical average slippage can understate tail events."),
C("execution","execution_partial_fill_latency","Partial fills and latency",
  "An order may fill in multiple executions over time. Latency between signal, submission, acknowledgement and fill can change the market context and the final average price.",
  ["Maintain an explicit order state machine.","Do not submit a duplicate when an acknowledgement is delayed; query by client order ID."],
  ["partial_fill","latency","order_state"],["binance_spot_api","binance_usdm_user_stream"],"Latency distributions change with network and exchange load."),
C("risk_management","risk_position_sizing","Position sizing from risk budget",
  "Position size should be derived from the monetary risk budget and the distance to the invalidation or stop level. Notional, margin, leverage and exchange caps are separate constraints.",
  ["Calculate size after fees and plausible slippage where possible.","Apply hard notional caps after the risk-based size calculation."],
  ["position_sizing","risk_budget","stop_distance"],["cftc_futures_basics","binance_usdm_order"],"A stop does not cap loss during gaps or unavailable liquidity."),
C("risk_management","risk_daily_loss_and_circuit_breaker","Daily loss limits and circuit breakers",
  "A daily loss limit can stop new entries after realized and unrealized losses reach a configured threshold. Circuit breakers should also react to data, API and execution failures.",
  ["Define whether the limit uses equity, balance or starting capital.","Keep emergency stop state persistent across restarts."],
  ["daily_loss","circuit_breaker","emergency_stop"],["cftc_crypto_risks","owasp_api_security"],"Loss limits reduce exposure but cannot guarantee a maximum loss."),
C("portfolio_management","portfolio_allocation_exposure","Capital allocation and exposure",
  "Portfolio allocation controls how much capital and notional exposure is assigned to each asset and strategy. Per-trade caps do not replace portfolio-wide exposure limits.",
  ["Track spot market value, futures notional and margin separately.","Reserve capacity for fees, drawdown and operational needs."],
  ["allocation","notional_exposure","capital"],["cftc_futures_basics","cme_liquidity_measure"],"Correlated positions can create more risk than their individual caps suggest."),
C("portfolio_management","portfolio_correlation_concentration","Correlation and concentration",
  "Several positions can share the same underlying risk driver even when their symbols differ. Correlation and concentration analysis helps estimate combined exposure.",
  ["Use rolling and stressed correlations, not only long-run averages.","Aggregate exposure by asset, sector, quote currency and strategy."],
  ["correlation","concentration","portfolio_risk"],["fred_api","bis_stablecoins"],"Correlations can rise sharply during market stress."),
C("portfolio_management","portfolio_rebalancing_rules","Portfolio rebalancing",
  "Rebalancing restores target weights or risk budgets after prices and positions move. Rebalancing rules should include transaction costs, liquidity and tax or reporting constraints.",
  ["Set bands or scheduled review rules before observing outcomes.","Avoid rebalancing solely because a position has moved against the trader."],
  ["rebalancing","weights","turnover"],["cftc_futures_basics","fred_api"],"Frequent rebalancing can increase costs and churn."),
C("backtesting","backtest_lookahead_bias","Look-ahead bias",
  "Look-ahead bias occurs when a backtest uses information that was unavailable at the simulated decision time. It can make a strategy appear more profitable than it could have been.",
  ["Use only fully closed candles for signals unless intrabar data is modeled.","Shift features and labels to reflect actual availability times."],
  ["lookahead_bias","timestamp_alignment","backtest"],["binance_spot_api"],"A high backtest return is not evidence of future profitability."),
C("backtesting","backtest_costs_and_fills","Realistic costs and fills",
  "A useful backtest models fees, spread, slippage, partial fills, funding and exchange constraints. Assuming every order fills at the candle close creates optimistic results.",
  ["Use maker and taker fees appropriate to the account.","Stress costs under thin liquidity and high volatility."],
  ["fees","slippage","fill_model"],["binance_spot_api","cme_liquidity_measure"],"Paper fills are still approximations of live matching-engine behavior."),
C("backtesting","backtest_survivorship_selection","Survivorship and selection bias",
  "Survivorship bias appears when tests include only assets that remain listed or successful. Selection bias appears when many strategy variants are tried and only the best is reported.",
  ["Include delisted and failed assets when data is available.","Record every tested strategy variant and reserve untouched evaluation data."],
  ["survivorship_bias","selection_bias","multiple_testing"],["cftc_crypto_risks"],"Crypto listing history and symbol migrations can make historical universes incomplete."),
C("walk_forward_validation","validation_chronological_splits","Chronological train-validation-test splits",
  "Time-series evaluation must preserve temporal order. Training on future periods and evaluating on earlier periods leaks information and invalidates the test.",
  ["Keep a final holdout untouched until model selection is complete.","Document feature and label windows for each split."],
  ["time_split","holdout","data_leakage"],["fred_api"],"Random shuffling is usually inappropriate for non-stationary market data."),
C("walk_forward_validation","validation_walk_forward","Walk-forward evaluation",
  "Walk-forward testing repeatedly trains or tunes on a past window and evaluates on the next unseen window. It measures whether a method remains useful as conditions evolve.",
  ["Use fixed, documented rolling or expanding windows.","Aggregate results across folds rather than choosing the best fold."],
  ["walk_forward","rolling_window","out_of_sample"],["fred_api"],"Repeated tuning on walk-forward results can still overfit the evaluation process."),
C("walk_forward_validation","validation_purging_embargo","Purging and embargo for overlapping labels",
  "When labels overlap in time, training examples near a test interval can leak future information. Purging removes overlapping samples and an embargo separates adjacent periods.",
  ["Use label horizons to identify overlapping observations.","Apply purging before model fitting and feature selection."],
  ["purging","embargo","cross_validation"],["fred_api"],"Validation design depends on the exact prediction horizon and label construction."),
C("statistics","statistics_expectancy","Expected value and expectancy",
  "Trade expectancy combines win probability, average win and average loss. It should be measured net of costs and alongside drawdown and tail risk.",
  ["Estimate expectancy from a sufficiently large and representative sample.","Report uncertainty and the distribution of outcomes, not only the mean."],
  ["expectancy","win_rate","average_win_loss"],["cftc_futures_basics"],"A positive historical expectancy can disappear after regime changes."),
C("statistics","statistics_variance_drawdown","Variance and drawdown",
  "Variance measures dispersion around an average, while drawdown measures peak-to-trough loss in an equity path. Both describe risks that a win rate alone cannot show.",
  ["Track maximum and time-under-water drawdown.","Compare risk-adjusted metrics only with consistent sampling and costs."],
  ["variance","drawdown","risk_adjusted"],["fred_api"],"Small samples can make risk estimates unstable."),
C("statistics","statistics_confidence_intervals","Confidence intervals and sample size",
  "A performance estimate from a finite number of trades has sampling uncertainty. Confidence intervals and bootstrap methods help show how uncertain win rates and expectancy may be.",
  ["Report the sample size and interval method.","Avoid interpreting overlapping intervals as proof of equality or difference."],
  ["confidence_interval","bootstrap","sample_size"],["fred_api"],"Statistical significance does not imply economic usefulness."),
C("time_series","timeseries_stationarity","Stationarity and non-stationarity",
  "A stationary series has stable statistical properties over time, while financial price and volatility processes often change. Many models require transformations or explicit regime handling.",
  ["Test assumptions on the transformed feature used by the model.","Do not assume stationarity because a chart looks stable."],
  ["stationarity","non_stationarity","returns"],["fred_api"],"Statistical tests have assumptions and limited power in short samples."),
C("time_series","timeseries_autocorrelation","Autocorrelation and dependence",
  "Autocorrelation measures dependence between observations separated by a lag. It can reveal persistence, mean reversion or volatility clustering, but these properties may be regime-specific.",
  ["Inspect returns and volatility separately.","Correct inference for serial dependence when evaluating strategies."],
  ["autocorrelation","volatility_clustering","lag"],["fred_api"],"Observed dependence may be unstable and sensitive to sampling frequency."),
C("time_series","timeseries_resampling_alignment","Resampling and candle alignment",
  "Resampling converts observations into a chosen timeframe using explicit open, high, low, close and volume aggregation rules. Incorrect timezone or interval alignment can create false signals.",
  ["Use exchange timestamps and consistent UTC boundaries.","Do not mix partially formed and closed candles in one feature set."],
  ["resampling","timezone","candle_alignment"],["binance_spot_api"],"Different venues may define candle boundaries or data corrections differently."),
C("market_regimes","regime_trend_range","Trend and range regimes",
  "Trending and ranging markets have different signal behavior. Trend filters, volatility and swing structure can help classify the current regime, but transitions are uncertain.",
  ["Use multiple independent features and a neutral state.","Evaluate strategy performance separately by regime."],
  ["trend_regime","range_regime","market_state"],["binance_spot_api","cme_liquidity"],"Regime labels are estimates and may lag turning points."),
C("market_regimes","regime_volatility_states","Volatility regimes",
  "Volatility regimes describe changes in the scale and clustering of price movement. Stops, position sizes and expected slippage should adapt to volatility without overreacting to noise.",
  ["Use realized volatility or ATR with a documented window.","Stress test sudden volatility expansion and liquidity deterioration."],
  ["volatility_regime","ATR","volatility_clustering"],["binance_spot_api","fred_api"],"Volatility estimates are backward-looking and can jump abruptly."),
C("strategy_design","strategy_signal_vs_execution","Separate signal generation from execution",
  "A trading strategy should distinguish the market hypothesis, entry signal, sizing, order choice, exit and failure handling. Separating these components makes testing and diagnosis clearer.",
  ["Store signal time and order submission time separately.","Do not let execution success retroactively change the signal label."],
  ["strategy_design","signal","execution"],["binance_spot_api","cme_liquidity"],"A profitable signal can still lose money through poor execution."),
C("strategy_design","strategy_robustness_sensitivity","Robustness and parameter sensitivity",
  "A robust strategy should not depend on one narrow parameter choice or one short market period. Sensitivity analysis checks whether performance degrades smoothly around selected settings.",
  ["Test nearby parameter values and different assets.","Prefer stable regions over a single best-performing point."],
  ["robustness","sensitivity","parameter_search"],["fred_api"],"Large parameter searches increase multiple-testing risk."),
C("machine_learning","ml_overfitting_regularization","Overfitting and regularization",
  "Overfitting occurs when a model learns noise in the training sample rather than repeatable structure. Regularization, simpler models and genuinely unseen evaluation data can reduce but not eliminate it.",
  ["Compare with simple baselines.","Track the number of experiments and model versions."],
  ["overfitting","regularization","generalization"],["fred_api"],"Complexity can hide data leakage and selection bias."),
C("machine_learning","ml_feature_label_leakage","Feature and label leakage",
  "Leakage occurs when a feature, label or preprocessing step contains information from the evaluation period or future. It can create impressive but invalid model performance.",
  ["Fit scalers and transformations only on training data.","Align each feature with the time it would actually be available."],
  ["data_leakage","features","labels"],["fred_api"],"Leakage can be subtle in rolling indicators and overlapping horizons."),
C("machine_learning","ml_validation_baselines","Model validation and baselines",
  "Machine-learning models should be compared with naive and domain-specific baselines using chronological validation. Calibration, stability and error analysis matter in addition to raw accuracy.",
  ["Evaluate precision, recall, calibration and economic utility where appropriate.","Keep a final untouched holdout."],
  ["baseline","calibration","validation"],["fred_api"],"Prediction accuracy alone does not establish positive trading expectancy."),
C("data_quality","data_missing_outliers","Missing data and outlier handling",
  "Missing candles, duplicate records, bad ticks and extreme outliers can distort indicators and model features. Data cleaning must preserve a trace of what was changed and why.",
  ["Detect gaps and duplicates before indicator calculation.","Do not silently interpolate executable prices."],
  ["missing_data","duplicates","outliers"],["binance_spot_api"],"A real market jump should not be deleted as an outlier without evidence."),
C("data_quality","data_timestamp_integrity","Timestamp and candle-close integrity",
  "Market data must distinguish event time, exchange transaction time, receive time and candle close time. Signals based on an unclosed candle can change before the interval ends.",
  ["Store source timestamps and ingestion timestamps.","Reject stale or future-dated data and monitor clock drift."],
  ["timestamps","closed_candle","data_integrity"],["binance_spot_api","binance_usdm_user_stream"],"Clock synchronization and feed delays can affect short-timeframe strategies."),
C("exchange_api","api_rate_limits_backoff","Rate limits and backoff",
  "Exchange APIs enforce request and order limits. A client should honor rate-limit headers, use bounded exponential backoff and avoid retry storms.",
  ["Treat HTTP 429 and 418 as mandatory slow-down signals.","Cache public metadata and batch requests where supported."],
  ["rate_limit","backoff","API"],["binance_spot_api","binance_usdm_order"],"Rate limits differ by endpoint, account and IP."),
C("exchange_api","api_signing_idempotency","Signed requests and idempotency",
  "Private exchange endpoints require authenticated signatures and timestamps. Client order IDs support safe status lookup and help prevent duplicate submissions after ambiguous responses.",
  ["Never log API secrets or signed query strings.","Query order status before retrying an uncertain submission."],
  ["HMAC","client_order_id","idempotency"],["binance_spot_api","binance_usdm_order"],"Signature and client-ID rules are exchange-specific and may change."),
C("security","security_least_privilege_keys","Least-privilege API keys",
  "API credentials should have only the permissions required for the task. Trading keys should be separated by environment and should not have withdrawal permission unless absolutely required.",
  ["Use separate Testnet and Live credentials.","Restrict IPs where the exchange supports it and rotate exposed keys immediately."],
  ["API_key","least_privilege","credential_isolation"],["binance_spot_api","owasp_api_security"],"No application-side encryption compensates for a compromised host."),
C("security","security_secret_storage_rotation","Secret storage and rotation",
  "Secrets should be encrypted with authenticated encryption, excluded from source control and logs, and rotated when compromise is suspected. Key management is separate from ciphertext storage.",
  ["Require a strong server-side key and restrictive file permissions.","Fail closed when a key is missing or decryption authentication fails."],
  ["Fernet","key_rotation","secret_management"],["owasp_api_security"],"A weak server master key undermines encrypted credential storage."),
C("compliance","compliance_jurisdiction_product_scope","Jurisdiction and product restrictions",
  "Trading products and exchange access can be restricted by jurisdiction, customer classification or local rules. Product availability does not establish that a user is legally permitted to trade it.",
  ["Keep jurisdiction checks configurable and evidence-based.","Do not infer legal permission from API availability."],
  ["jurisdiction","restricted_products","compliance"],["cftc_crypto_risks","cftc_futures_basics"],"This educational note is not legal advice; consult qualified local counsel."),
C("compliance","compliance_audit_records","Audit trails and records",
  "Reliable trading systems retain decision, order, fill, configuration and incident records with timestamps and actor identity. Records support reconciliation, debugging and compliance review.",
  ["Make audit events append-only where possible.","Never store raw secrets in event details."],
  ["audit_log","retention","traceability"],["owasp_api_security","binance_spot_api"],"Record-retention duties vary by jurisdiction and business model."),
C("macro_economics","macro_policy_rates","Policy rates and discount rates",
  "Central-bank policy rates influence financing costs, discount rates and risk appetite. Their market impact depends on expectations, guidance and the difference between actual and anticipated decisions.",
  ["Track releases and revisions with their publication times.","Compare the announcement with consensus expectations where reliable data exists."],
  ["policy_rate","central_bank","macro"],["fred_api","bis_stablecoins"],"Macro relationships are conditional and can reverse across regimes."),
C("macro_economics","macro_inflation_growth","Inflation and growth data",
  "Inflation, employment and growth releases can change expectations for rates and liquidity. Data revisions and release calendars matter when evaluating historical signals.",
  ["Use point-in-time data vintages for backtests.","Avoid using revised data before its actual release date."],
  ["inflation","GDP","employment"],["fred_api"],"Economic indicators are revised and may not be available at the first estimate."),
C("macro_economics","macro_dollar_liquidity_risk","Dollar liquidity and risk appetite",
  "Broad dollar strength, funding conditions and financial stress can influence cross-asset demand. Crypto responses are not stable enough to be treated as fixed correlations.",
  ["Use multiple indicators and test lag structures.","Separate correlation from causal claims."],
  ["DXY","liquidity","risk_on_risk_off"],["fred_api","bis_stablecoins"],"Macro indicators may be delayed and should not override direct market data."),
C("correlation","correlation_rolling_stability","Rolling correlation",
  "Rolling correlation estimates how two return series co-move over a selected window. The estimate depends on window length and can change sharply during stress.",
  ["Use synchronized returns and consistent sampling.","Compare several windows and stress periods."],
  ["rolling_correlation","covariance","returns"],["fred_api","bis_stablecoins"],"Correlation is not causation and is not constant."),
C("correlation","correlation_beta_hedging","Beta and hedge ratios",
  "Beta estimates sensitivity to a reference market, while hedge ratios estimate offsets between exposures. A hedge can fail when correlations or volatility relationships change.",
  ["Estimate hedge ratios from aligned returns.","Reassess basis, fees and residual risk after hedging."],
  ["beta","hedge_ratio","systematic_risk"],["fred_api","cme_liquidity_measure"],"A statistical hedge is not a guaranteed offset."),
C("on_chain","onchain_utxo_account_models","UTXO and account-based ledgers",
  "Bitcoin uses an unspent-transaction-output model, while Ethereum uses an account-based state model. Their transaction, fee and balance semantics differ.",
  ["Do not compare raw transaction counts across chains without normalization.","Understand address, wallet and entity distinctions."],
  ["UTXO","account_model","blockchain"],["bitcoin_how_it_works","ethereum_smart_contracts"],"On-chain addresses do not map one-to-one to real-world entities."),
C("on_chain","onchain_network_activity_metrics","Network activity metrics",
  "Active addresses, transaction counts, transfer volume and fee totals can describe network usage, but each metric has limitations and can be affected by bots, batching and exchange flows.",
  ["Document provider methodology and entity adjustments.","Compare trends rather than treating one metric as intrinsic value."],
  ["active_addresses","transactions","network_activity"],["bitcoin_faq","ethereum_smart_contracts"],"On-chain metrics are proxies and can be manipulated or misinterpreted."),
C("on_chain","onchain_fees_congestion_finality","Network fees, congestion and finality",
  "Network fees respond to blockspace demand and protocol fee markets. Confirmation and finality differ across consensus systems and should be considered when moving assets.",
  ["Monitor fee estimates and confirmation requirements.","Distinguish transaction inclusion from economic finality."],
  ["gas","network_fees","finality"],["bitcoin_how_it_works","ethereum_smart_contracts"],"Network conditions and fee markets can change rapidly."),
C("tokenomics","tokenomics_issuance_supply","Issuance and supply schedule",
  "Token issuance, burns and supply caps affect the number of units available over time. Circulating, total and maximum supply are different measures.",
  ["Verify definitions and contract-level token mechanics.","Include emissions and burns in long-horizon analysis."],
  ["issuance","circulating_supply","max_supply"],["cftc_token_caution","bitcoin_how_it_works"],"Supply schedules can change through governance or protocol upgrades."),
C("tokenomics","tokenomics_unlock_vesting","Unlocks and vesting",
  "Token unlocks release previously restricted allocations to investors, teams or ecosystems. Their market impact depends on recipient behavior, liquidity and expectations.",
  ["Track unlock calendars and actual circulating supply changes.","Do not assume every unlocked token is immediately sold."],
  ["token_unlock","vesting","supply_overhang"],["cftc_token_caution"],"Unlock data providers may differ and schedules can be revised."),
C("tokenomics","tokenomics_fdv_circulating_valuation","Market capitalization and fully diluted valuation",
  "Market capitalization uses circulating supply, while fully diluted valuation applies price to a broader assumed supply. Large gaps can signal future dilution but do not alone establish overvaluation.",
  ["Check supply definitions and vesting assumptions.","Compare valuation with usage, liquidity and competing networks."],
  ["market_cap","FDV","dilution"],["cftc_token_caution"],"Valuation ratios are context-dependent and not standalone buy or sell signals."),
C("stablecoins","stablecoins_reserve_models","Stablecoin reserve models",
  "Stablecoins may be backed by cash-like reserves, collateral, crypto assets or algorithmic mechanisms. Redemption rights, custody and reserve transparency differ by issuer.",
  ["Read issuer attestations and redemption terms.","Distinguish proof of reserves from a full audit of liabilities."],
  ["reserves","redemption","issuer_risk"],["bis_stablecoins","cftc_crypto_risks"],"A peg target is not a guarantee of redeemability at par."),
C("stablecoins","stablecoins_depeg_mechanics","Depeg and run risk",
  "A stablecoin can trade away from its reference value when redemption, reserve confidence, market liquidity or banking access is impaired. A depeg can propagate through trading pairs and collateral.",
  ["Monitor secondary-market price, redemption access and reserve disclosures.","Stress-test collateral haircuts and exchange conversion routes."],
  ["depeg","run_risk","collateral"],["bis_stablecoins","cftc_crypto_risks"],"Peg stability can fail suddenly, including during market-wide stress."),
C("stablecoins","stablecoins_liquidity_counterparty","Stablecoin liquidity and counterparty risk",
  "Stablecoin exposure includes issuer, custodian, banking, smart-contract, bridge and exchange risks. High trading volume does not prove deep executable liquidity or low redemption risk.",
  ["Identify the actual issuer and chain for each token.","Avoid treating different stablecoins as interchangeable."],
  ["counterparty","bridge_risk","liquidity"],["bis_stablecoins","ethereum_smart_contracts"],"Stablecoin legal claims and protections vary across jurisdictions."),
C("trading_psychology","psychology_fomo_impulse","FOMO and impulsive entries",
  "Fear of missing out can cause late entries after large moves and weaken adherence to predefined risk. A written checklist can separate evidence from urgency.",
  ["Require a setup, invalidation and position size before entry.","Record skipped trades as well as executed trades."],
  ["FOMO","impulse","checklist"],["cftc_crypto_risks","finra_order_types"],"Behavioral rules support discipline but do not eliminate emotional bias."),
C("trading_psychology","psychology_loss_aversion_disposition","Loss aversion and disposition effect",
  "Traders may hold losing positions too long and realize winners too early because losses and gains are experienced asymmetrically. Predefined exits reduce ad hoc decisions.",
  ["Evaluate exits against the original thesis and invalidation level.","Measure average win, average loss and time in trade."],
  ["loss_aversion","disposition_effect","exit_discipline"],["cftc_futures_basics"],"A fixed stop still needs liquidity and gap-risk allowances."),
C("trading_psychology","psychology_revenge_overtrading","Revenge trading and overtrading",
  "After a loss, a trader may increase frequency or size to recover quickly. Daily limits, cooldowns and mandatory review can interrupt this feedback loop.",
  ["Use a cooldown after a configured loss or repeated errors.","Never increase risk automatically to recover a drawdown."],
  ["revenge_trading","overtrading","cooldown"],["cftc_crypto_risks"],"Behavioral safeguards should be tested for bypass paths."),
C("operations","operations_monitoring_alerts","Monitoring and operational alerts",
  "A trading system needs health checks for market data, clock drift, API connectivity, database writes, order-state lag and emergency stops. Alerts should distinguish warnings from trading halts.",
  ["Use heartbeat and stale-data thresholds.","Keep alert delivery independent from the component being monitored."],
  ["monitoring","heartbeat","alerts"],["owasp_api_security","binance_spot_api"],"A green dashboard is not proof that every subsystem is healthy."),
C("operations","operations_incident_recovery_reconciliation","Incident recovery and reconciliation",
  "After a crash or connection loss, the system should reconcile exchange balances, positions and orders before resuming. Uncertain orders must be queried by client ID before any retry.",
  ["Persist intent before sending an order.","Resume only after local and exchange state agree or a human resolves the discrepancy."],
  ["reconciliation","recovery","uncertain_order"],["binance_spot_api","binance_usdm_order","owasp_api_security"],"Never assume local state is authoritative after a disconnect."),
]

# Source catalog entries are suggestions only; none are considered admin-verified.
sources = [
    {"key":"binance_spot_api","title":"Binance Spot REST API documentation","publisher":"Binance","url":"https://developers.binance.com/en/docs/products/spot/rest-api","domains":["spot_market","order_types","execution","exchange_api","data_quality"],"scope_note":"Primary Spot API reference; re-check endpoint versions, filters and rate limits.","status":"pending_admin_review","verified":False},
    {"key":"binance_usdm_order","title":"Binance USDⓈ-M Futures New Order reference","publisher":"Binance","url":"https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order","domains":["futures_market","leverage_margin","order_types","execution","exchange_api"],"scope_note":"USDⓈ-M Futures order parameters, including positionSide and reduceOnly; verify current exchange rules.","status":"pending_admin_review","verified":False},
    {"key":"binance_usdm_mark_price","title":"Binance USDⓈ-M Futures Mark Price reference","publisher":"Binance","url":"https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price","domains":["futures_market","liquidation","funding"],"scope_note":"Mark price, index price and funding-related fields.","status":"pending_admin_review","verified":False},
    {"key":"binance_usdm_funding","title":"Binance USDⓈ-M Futures Funding Rate History","publisher":"Binance","url":"https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History","domains":["funding","futures_market"],"scope_note":"Funding history endpoint and response semantics.","status":"pending_admin_review","verified":False},
    {"key":"binance_usdm_user_stream","title":"Binance Futures user data stream reference","publisher":"Binance","url":"https://developers.binance.com/en/docs/products/derivatives-trading-coin-futures/user-data-streams","domains":["operations","execution","liquidation"],"scope_note":"This surfaced page is COIN-M Futures scoped; use only for event concepts that are verified to match the deployed USDⓈ-M stream.","status":"pending_admin_review","verified":False},
    {"key":"cftc_futures_basics","title":"CFTC Basics of Futures Trading","publisher":"CFTC","url":"https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/FuturesMarketBasics/index.htm","domains":["futures_market","leverage_margin","liquidation","risk_management","compliance"],"scope_note":"Regulatory education on futures mechanics and risks.","status":"pending_admin_review","verified":False},
    {"key":"cftc_margin","title":"CFTC Economic Purpose of Futures Markets and How They Work","publisher":"CFTC","url":"https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/economicpurpose.html","domains":["leverage_margin","liquidation","futures_market"],"scope_note":"Initial and maintenance margin, marking to market and margin calls.","status":"pending_admin_review","verified":False},
    {"key":"cftc_crypto_risks","title":"CFTC Understand the Risks of Virtual Currency Trading","publisher":"CFTC","url":"https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/understand_risks_of_virtual_currency.html","domains":["risk_management","spot_market","futures_market","stablecoins","trading_psychology","compliance"],"scope_note":"Customer education on spot and derivatives risks; not legal advice.","status":"pending_admin_review","verified":False},
    {"key":"cftc_token_caution","title":"CFTC Use Caution When Buying Digital Coins or Tokens","publisher":"CFTC","url":"https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/caution_of_digital_currencies.html","domains":["tokenomics","compliance","risk_management"],"scope_note":"Customer education on token rights, supply and project risks.","status":"pending_admin_review","verified":False},
    {"key":"cme_liquidity","title":"CME Group Liquidity and Immediacy","publisher":"CME Group","url":"https://www.cmegroup.com/education/courses/trading-and-analysis/liquidity-and-immediacy","domains":["liquidity","market_microstructure","order_flow","execution"],"scope_note":"Education on spread, liquidity and immediacy in futures markets.","status":"pending_admin_review","verified":False},
    {"key":"cme_liquidity_measure","title":"CME Group How Traders Measure Liquidity","publisher":"CME Group","url":"https://www.cmegroup.com/education/articles-and-reports/how-traders-measure-liquidity","domains":["liquidity","market_microstructure","volume","portfolio_management"],"scope_note":"Education on volume, open interest, spread, depth and slippage.","status":"pending_admin_review","verified":False},
    {"key":"finra_order_types","title":"FINRA Understanding Order Types","publisher":"FINRA","url":"https://syndication.finra.org/content/understanding-order-types-can-save-time-and-money","domains":["order_types","execution","trading_psychology"],"scope_note":"Investor education on market, limit, stop and stop-limit order behavior.","status":"pending_admin_review","verified":False},
    {"key":"bitcoin_faq","title":"Bitcoin.org FAQ","publisher":"Bitcoin.org","url":"https://bitcoin.org/en/faq","domains":["on_chain","tokenomics"],"scope_note":"Introductory Bitcoin transaction and confirmation explanations.","status":"pending_admin_review","verified":False},
    {"key":"bitcoin_how_it_works","title":"Bitcoin.org How Bitcoin Works","publisher":"Bitcoin.org","url":"https://bitcoin.org/en/how-it-works","domains":["on_chain","tokenomics"],"scope_note":"Introductory explanation of keys, transactions, mining and confirmations.","status":"pending_admin_review","verified":False},
    {"key":"ethereum_smart_contracts","title":"Ethereum.org Smart Contracts","publisher":"Ethereum.org","url":"https://ethereum.org/developers/docs/smart-contracts/","domains":["on_chain","tokenomics","stablecoins"],"scope_note":"Official ecosystem documentation on smart contracts and their limitations.","status":"pending_admin_review","verified":False},
    {"key":"bis_stablecoins","title":"BIS Stablecoins: Framing the Debate","publisher":"Bank for International Settlements","url":"https://www.bis.org/speeches/20260420-stablecoins-framing-debate","domains":["stablecoins","macro_economics","correlation"],"scope_note":"Policy and financial-stability context for stablecoins; verify publication date and scope.","status":"pending_admin_review","verified":False},
    {"key":"fred_api","title":"Federal Reserve Bank of St. Louis FRED API Series Reference","publisher":"Federal Reserve Bank of St. Louis","url":"https://fred.stlouisfed.org/docs/api/fred/series.html","domains":["macro_economics","time_series","statistics","correlation","machine_learning"],"scope_note":"Primary API documentation for economic time-series data and observation metadata.","status":"pending_admin_review","verified":False},
    {"key":"owasp_api_security","title":"OWASP API Security Top 10","publisher":"OWASP","url":"https://api-security.owasp.org/","domains":["security","exchange_api","operations","compliance"],"scope_note":"Security risk taxonomy for APIs; map controls to the current version before implementation.","status":"pending_admin_review","verified":False},
]

# Link concepts to suggested sources, but keep all seeds unverified and confidence at zero.
source_map = {x["key"]: x for x in sources}
for entry in entries:
    for source_key in entry["source_keys"]:
        if source_key not in source_map:
            raise SystemExit(f"Unknown suggested source key: {source_key}")
    entry["verified"] = False
    entry["confidence"] = 0.0

catalog_path = knowledge_dir / "seed_catalog.json"
catalog_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
sources_path = knowledge_dir / "source_catalog.json"
sources_path.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")

p = knowledge_dir / "base.py"
s = p.read_text()
if "def source_catalog()" not in s:
    s = s.replace("from dataclasses import asdict, dataclass, field\n",
                  "from dataclasses import asdict, dataclass, field\nimport json\nfrom pathlib import Path\n", 1)
    marker = "\n\nREQUIRED_KNOWLEDGE_DOMAINS:"
    loader = '''

# Extended curriculum seeds are educational notes, not verified source material.
_seed_catalog_path = Path(__file__).with_name("seed_catalog.json")
if _seed_catalog_path.exists():
    _seed_rows = json.loads(_seed_catalog_path.read_text(encoding="utf-8"))
    _seed_keys = {item.key for item in SEED_CONCEPTS}
    for _row in _seed_rows:
        if _row["key"] not in _seed_keys:
            SEED_CONCEPTS.append(KnowledgeConcept(**_row))


def source_catalog() -> list[dict[str, Any]]:
    path = Path(__file__).with_name("source_catalog.json")
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    # A catalog entry is only a suggested reference until an administrator reviews it.
    return [{**row, "verified": False, "status": "pending_admin_review"} for row in rows]
'''
    if marker not in s:
        raise SystemExit("Knowledge domain registry insertion marker missing.")
    s = s.replace(marker, loader + marker, 1)

# Preserve suggested source references separately from actual verified source-record IDs.
old = '''    for item in items:
        ids = sorted(links.get(item["key"], set()))
        item["source_keys"] = [str(x) for x in ids]
'''
new = '''    for item in items:
        suggested = list(item.get("source_keys", []))
        ids = sorted(links.get(item["key"], set()))
        item["suggested_source_keys"] = suggested
        item["source_keys"] = [str(x) for x in ids]
'''
if old in s:
    s = s.replace(old, new, 1)
p.write_text(s)

# Expose the pending source catalog in both API implementations.
p = root / "app/api/routes.py"
s = p.read_text()
if 'def knowledge_source_catalog(' not in s:
    insert = s.index('\n\n@router.get("/knowledge/coverage")')
    route = '''

@router.get("/knowledge/catalog")
def knowledge_source_catalog():
    from app.knowledge.base import source_catalog
    return {"items": source_catalog()}
'''
    s = s[:insert] + route + s[insert:]
p.write_text(s)

p = root / "app/star_app.py"
s = p.read_text()
if 'async def knowledge_source_catalog(' not in s:
    insert = s.index('\n\nasync def learning_list')
    route = '''

async def knowledge_source_catalog(request: Request):
    from app.knowledge.base import source_catalog
    return J({"items": source_catalog()})
'''
    s = s[:insert] + route + s[insert:]
    s = s.replace('''    Route("/api/knowledge/coverage", knowledge_coverage),
''','''    Route("/api/knowledge/coverage", knowledge_coverage),
    Route("/api/knowledge/catalog", knowledge_source_catalog),
''',1)
p.write_text(s)

# Regression tests for curriculum breadth and pending-only provenance.
p = root / "tests/test_knowledge_foundation_gates.py"
s = p.read_text()
if "test_every_required_domain_has_at_least_three_seeded_concepts" not in s:
    s += '''

def test_every_required_domain_has_at_least_three_seeded_concepts():
    from app.knowledge.base import SEED_CONCEPTS, REQUIRED_KNOWLEDGE_DOMAINS
    for domain, spec in REQUIRED_KNOWLEDGE_DOMAINS.items():
        count = sum(1 for concept in SEED_CONCEPTS
                    if concept.category in spec["categories"] or concept.category == domain)
        assert count >= 3, f"knowledge domain under-seeded: {domain} ({count})"
    assert all(not concept.verified and concept.confidence == 0.0 for concept in SEED_CONCEPTS
               if concept.key not in {"market_structure_bos", "candlestick_context", "liquidity_sweep"})
    

def test_seed_source_catalog_is_visible_but_never_auto_verified():
    from app.knowledge.base import source_catalog
    catalog = source_catalog()
    assert len(catalog) >= 15
    assert all(x["url"].startswith("https://") for x in catalog)
    assert all(x["verified"] is False and x["status"] == "pending_admin_review" for x in catalog)
'''
p.write_text(s)
