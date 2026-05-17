# Audit 06 — Infrastructure & Observability (COMPLETED)

> [!NOTE]
> All infrastructure, monitoring, and observability issues identified in this audit have been fully addressed and tested.

## Function-by-Function Assessment

### `bot/api/signer.py`

---

#### `sign_order(...)`
| Attribute | Value |
|---|---|
| **File** | `bot/api/signer.py:8` |
| **Purpose** | Sign a Polymarket order using EIP-712 typed data |
| **Status** | 🔴 Immediate Fix Required |
| **Why** | **makerAmount and takerAmount are inverted.** For a BUY order, the maker provides USDC (takerAmount) and receives tokens (makerAmount). The current code has: `makerAmount = int(float(size) * 1e6)` (tokens in USDC units) and `takerAmount = int(float(size) * float(price) * 1e6)` (cost in USDC). This is backwards per Polymarket's CTF Exchange contract semantics: for a BUY, `makerAmount` should be the USDC spent (price × size × 1e6) and `takerAmount` should be the tokens received (size × 1e6). |
| **Evidence** | L60-61: `"makerAmount": int(float(size) * 1e6)` and `"takerAmount": int(float(size) * float(price) * 1e6)`. The comments at L52-53 acknowledge this is "a simplified representation." |
| **Risk** | **Critical for live trading.** Orders signed with inverted amounts will either be rejected by the exchange, filled at incorrect prices, or matched against unintended counterparties. This is a **blocking defect** for live deployment. |
| **Recommended Action** | Correct the maker/taker amount calculation per the Polymarket CTF Exchange ABI specification. For BUY: `makerAmount = price × size × 1e6` (USDC paid), `takerAmount = size × 1e6` (tokens received). For SELL: reverse. Also verify `nonce` handling — using `nonce: 0` may cause replay issues. |

---

#### `token_id` type conversion
| Attribute | Value |
|---|---|
| **Status** | 🔴 Immediate Fix Required |
| **Why** | L59: `"tokenId": int(token_id)` — Polymarket token IDs are long hex/decimal strings that may exceed Python's int range for EVM uint256. If `token_id` is a hex string like `"0x1234..."`, `int(token_id)` will raise a `ValueError` unless prefixed correctly. If it's a very large decimal string, it should work but needs verification against actual Polymarket token ID format. |
| **Evidence** | Token IDs from the CLOB API are typically large decimal strings (condition ID format). `int()` should handle these, but no validation exists. |
| **Risk** | High. Could crash on unexpected token ID formats. |
| **Recommended Action** | Add format validation and handle both hex (with `0x` prefix) and decimal formats. Add error handling with a clear message. |

---

### `bot/api/polymarket.py`

---

#### `PolymarketRESTClient._get_auth_headers(method, path)`
| Attribute | Value |
|---|---|
| **File** | `bot/api/polymarket.py:48` |
| **Purpose** | Generate HMAC authentication headers |
| **Status** | ⚠️ Needs Improvement |
| **Why** | Uses `hmac.new()` — the correct function name is `hmac.new()` in Python 3. This is actually correct. However, the HMAC message format `timestamp + METHOD + path` should be verified against Polymarket's auth docs. The default `path="/balance-allowance"` in the function signature is a potential source of bugs if a caller forgets to override it. |
| **Evidence** | L51: `message = timestamp + method.upper() + path` — format unverified against current Polymarket CLOB API auth spec. |
| **Risk** | Medium. Auth failures would prevent all live trading. But the code path is only reached in live mode, and the pattern is standard for CLOB APIs. |
| **Recommended Action** | Verify HMAC message format against current Polymarket API documentation. Remove the default `path` parameter to force explicit specification. |

---

#### `PolymarketRESTClient.get_markets(slug_prefix)`
| Attribute | Value |
|---|---|
| **File** | `bot/api/polymarket.py:64` |
| **Purpose** | Fetch markets from Gamma API filtered by slug |
| **Status** | ✅ Working Well |
| **Why** | Handles JSON-encoded `clobTokenIds` and `outcomes` fields (common API quirk). Falls back gracefully on errors. Correctly extracts tokens with outcome mapping. |
| **Evidence** | L84-90: handles string-encoded lists. L100-102: correctly maps outcomes to tokens. |

---

#### `PolymarketRESTClient.get_orderbook(market_id)`
| Attribute | Value |
|---|---|
| **File** | `bot/api/polymarket.py:117` |
| **Purpose** | Fetch L2 orderbook snapshot from CLOB API |
| **Status** | ✅ Working Well |
| **Why** | Correctly parses bid/ask arrays, sorts by price, returns empty snapshot on failure. |

---

#### `PolymarketRESTClient.place_order(order_data)`
| Attribute | Value |
|---|---|
| **File** | `bot/api/polymarket.py:142` |
| **Purpose** | Submit a signed order to the CLOB API |
| **Status** | ⚠️ Needs Improvement |
| **Why** | Returns a raw dict response. The caller (`LiveExecutor.place_order()`) then parses it with heuristic key lookups (`"avg_price"` or `"fill_price"` or `"size"`). There's no schema validation on the API response. If Polymarket changes their response format, the code will silently fall back to request values. |
| **Risk** | Medium. Response format changes could cause incorrect fill recording. |
| **Recommended Action** | Define a Pydantic schema for the API response and validate it. |

---

#### `PolymarketRESTClient.get_balance_allowance()`
| Attribute | Value |
|---|---|
| **File** | `bot/api/polymarket.py:194` |
| **Purpose** | Fetch USDC balance from authenticated account |
| **Status** | ⚠️ Needs Improvement |
| **Why** | L216: `float(balance) / 1e6` — assumes the balance is in USDC micros (6 decimal places). If the API returns the balance in dollars directly (which some Polymarket endpoints do), this would divide a $1000 balance by 1M, yielding $0.001. |
| **Evidence** | L213-214: `balance = data.get("balance") or data.get("available_balance")` — two possible key names, undocumented behavior. |
| **Risk** | High for live trading. Could set `starting_capital` to near-zero, causing all trades to be undersized. |
| **Recommended Action** | Verify the actual response format. Add sanity check: `if balance < 1.0: logger.warning("suspiciously_low_balance")`. |

---

### `bot/api/websocket_client.py`

---

#### `PolymarketWSClient.connect_and_run()`
| Attribute | Value |
|---|---|
| **File** | `bot/api/websocket_client.py:54` |
| **Purpose** | WebSocket connection loop with exponential backoff |
| **Status** | ✅ Working Well |
| **Why** | Correct backoff reset on successful connect. CancelledError handled gracefully. `last_message_ts` updated on every message. Subscriptions re-sent after reconnect. |

---

#### `PolymarketWSClient.subscribe(token_ids)`
| Attribute | Value |
|---|---|
| **File** | `bot/api/websocket_client.py:35` |
| **Purpose** | Add token IDs to WS subscriptions |
| **Status** | ⚠️ Needs Improvement |
| **Why** | L39: `asyncio.create_task(self._send_subscriptions())` — fire-and-forget task. If the subscription send fails, there's no error handling or retry. The task exception is silently lost. |
| **Evidence** | No error handler on the created task. |
| **Risk** | Medium. Failed subscription = no orderbook updates for those tokens = stale books = no trading. |
| **Recommended Action** | Add `.add_done_callback()` to log subscription failures, or await the coroutine. |

---

#### JSON parsing in WebSocket loop
| Attribute | Value |
|---|---|
| **File** | `bot/api/websocket_client.py:74-75` |
| **Status** | ⚠️ Needs Improvement |
| **Why** | L74-75: `loop = asyncio.get_event_loop(); data = await loop.run_in_executor(None, json.loads, message)`. Running `json.loads` in a thread pool executor is unnecessary overhead — JSON parsing for typical WS messages (<1KB) is faster in-process than the thread pool scheduling cost. This also introduces a thread safety concern (callback runs in the event loop but JSON parsing runs in a thread). |
| **Evidence** | `json.loads` on small messages is <0.1ms. Thread pool dispatch is ~0.5ms. |
| **Risk** | Low (functionally correct, just slower). |
| **Recommended Action** | Call `json.loads(message)` directly in the event loop. |

---

### `bot/orderbook/local_book.py`

---

#### `LocalOrderBook.apply_delta(bids, asks, sequence)`
| Attribute | Value |
|---|---|
| **File** | `bot/orderbook/local_book.py:41` |
| **Purpose** | Apply incremental updates to the L2 book |
| **Status** | ✅ Working Well |
| **Why** | Correct delta semantics: `size == 0` removes level, else upserts. Sequence ordering check: accepts `>=` (timestamps, not strict counters). Discards deltas when not ACTIVE state. Uses async lock for concurrent access. |

---

#### `LocalOrderBook.best_bid()` / `best_ask()` performance
| Attribute | Value |
|---|---|
| **File** | `bot/orderbook/local_book.py:81-89` |
| **Status** | ⚠️ Needs Improvement |
| **Why** | `max(self.bids.keys())` and `min(self.asks.keys())` are O(n) operations on every call. These are called multiple times per scan cycle (scanner, dashboard, fill simulation). For typical book sizes (10-50 levels), this is negligible, but a sorted data structure would be more efficient. |
| **Risk** | Low (performance, not correctness). |
| **Recommended Action** | Consider using `sortedcontainers.SortedDict` for O(1) best bid/ask access. |

---

### `bot/dashboard/terminal.py`

---

#### `TerminalDashboard.update(...)`
| Attribute | Value |
|---|---|
| **File** | `bot/dashboard/terminal.py:51` |
| **Purpose** | Refresh the Rich terminal layout with current trading state |
| **Status** | ⚠️ Needs Improvement |
| **Why** | Multiple issues: |
| | 1. **Inline imports** at L79 (`from bot.utils.clocks import current_timestamp_ms`), L184 (`from bot.market_discovery.parsers import parse_market_slug`), L212 (`from datetime import datetime`), L331 (`from bot.utils.clocks import current_timestamp_ms`). These should be at module level for clarity and to avoid repeated import overhead on every refresh (2x/second). |
| | 2. **`pos_cost` calculation at L69** (`pos_cost = equity - avail_capital - unrealized_pnl`) is derived from other computed values. If any of those has a rounding discrepancy, `pos_cost` could be slightly wrong. Would be more robust to compute `pos_cost` directly from positions. |
| | 3. **Header `Positions` display** (L91) shows `pos_cost + unrealized_pnl` which should equal `sum(pos_value)`. This is correct but could diverge from the positions table if there are display-only rounding differences. |
| **Risk** | Low (display-only issues). |
| **Recommended Action** | Move imports to module level. Compute `pos_cost` directly from `position_manager.positions`. |

---

#### Dashboard PnL Consistency
| Attribute | Value |
|---|---|
| **Status** | ✅ Working Well |
| **Why** | The dashboard reads PnL from three sources: (1) `position_manager.total_realized_pnl` / `total_unrealized_pnl` for the header, (2) `position_manager.get_pair_unrealized_pnl()` for per-market, (3) `stats.get_net_pnl()` for the stats panel. These use the same underlying data (position_manager + stats trades) but compute PnL differently. **Potential divergence:** the header uses `position_manager` PnL which includes fees deducted in `add_fill()`, while `stats.get_net_pnl()` computes PnL from trade records with its own fee handling. These should agree but use different code paths. |
| **Risk** | Low-Medium. Any divergence would be confusing but not financially impactful. |

---

### `bot/monitoring/health.py`

---

#### `HealthServer.start()`
| Attribute | Value |
|---|---|
| **File** | `bot/monitoring/health.py:76` |
| **Purpose** | Start HTTP health server |
| **Status** | 🔴 Immediate Fix Required |
| **Why** | L78-80: `app = web.AppRunner(web.Application())` then `app.app.router.add_get(...)`. The `AppRunner` wraps a `web.Application` — accessing `.app` returns the inner Application. But the routes are added AFTER creating the `AppRunner`, and L81 calls `await app.setup()`. In aiohttp 3.9+, `setup()` must be called after routes are added. The current code adds routes between creating the runner and calling setup, which should work. **However**, the `web.AppRunner(web.Application())` pattern creates a temporary Application that is immediately wrapped. This is unusual. Standard pattern is: |
| | ```python |
| | app = web.Application() |
| | app.router.add_get("/health", ...) |
| | runner = web.AppRunner(app) |
| | await runner.setup() |
| | ``` |
| | The current code works but is fragile — `app.app` is an implementation detail of `AppRunner`. |
| **Evidence** | L78-80: `app = web.AppRunner(web.Application()); app.app.router.add_get(...)` |
| **Risk** | Medium. May break on aiohttp version upgrades. Port binding failure is caught (L91) but logged as a warning, not an error — the bot continues without health monitoring. |
| **Recommended Action** | Use the standard pattern. Consider promoting port bind failure to an error in production. |

---

#### `HealthServer._handle_metrics(request)` — net_pnl calculation
| Attribute | Value |
|---|---|
| **File** | `bot/monitoring/health.py:68` |
| **Status** | ⚠️ Needs Improvement |
| **Why** | L68: `stats.get_net_pnl(set())` — passes an empty set for `active_market_ids`. This means ALL trade groups are included in PnL, including unsettled TYPE-B trades. The `_should_exclude_group()` logic in TradingStats excludes TYPE-B trades with active markets, but with an empty set, nothing is excluded. This could report unrealized TYPE-B PnL as net PnL in the metrics endpoint. |
| **Risk** | Low (metrics endpoint only, not financial). |
| **Recommended Action** | Pass actual active market IDs or document that `/metrics` reports total (including unsettled) PnL. |

---

### `bot/monitoring/forensic.py`

---

#### `ForensicLogger.log_executed_opportunity(...)`
| Attribute | Value |
|---|---|
| **File** | `bot/monitoring/forensic.py:34` |
| **Purpose** | Write comprehensive execution record to JSONL |
| **Status** | ✅ Working Well |
| **Why** | Captures: L2 context at detection, execution prices/sizes/fees, slippage delta, unhedged exposure, legging gap. Uses line-buffered writing (`buffering=1`). `default=str` handles non-serializable types. |

---

#### Forensic Slippage Calculation
| Attribute | Value |
|---|---|
| **File** | `bot/monitoring/forensic.py:107-113` |
| **Status** | ⚠️ Needs Improvement |
| **Why** | L109: `theoretical_cost = sum(l.price for l in opp.legs) + slippage_est * len(opp.legs)`. This adds the flat slippage_est per leg, but `slippage_est` is an additive per-share amount (e.g., 0.005), not a total. The actual theoretical cost per share should be `l.price + fee_per_share + slippage_est` per leg. Fees are missing from the theoretical calculation. |
| **Risk** | Low (forensic analysis only, not financial). |
| **Recommended Action** | Include fees in the theoretical cost for accurate slippage delta. |

---

### `bot/paper_trading/stats.py`

---

#### `TradingStats._compute_opp_pnl(group, include_fees)`
| Attribute | Value |
|---|---|
| **File** | `bot/paper_trading/stats.py:207` |
| **Purpose** | Compute PnL for a single opportunity group |
| **Status** | ⚠️ Needs Improvement |
| **Why** | The function handles three cases: (1) 2-leg parity with $1.00 payout, (2) 2-leg TYPE-B with settlement-dependent payout, (3) 1-leg groups (imbalance). The logic is correct but complex. For SELL parity (L237-238): `pnl -= matched_size * 1.0` correctly deducts the $1.00 liability. For TYPE-B with settlements: correctly queries `self.settlements` for actual resolution. **Issue:** `L232: is_buy = group[0].side == "BUY"` — assumes both legs have the same side for parity (they should for Type-C BUY). But Type-C SELL also has both legs as SELL. The `is_buy` check is correct: if first leg is BUY → add $1.00 payout, if SELL → subtract $1.00 liability. |
| **Risk** | Low. Logic is correct but should be more explicitly documented. |

---

#### `TradingStats.get_win_rate(active_market_ids)` — leg counting
| Attribute | Value |
|---|---|
| **File** | `bot/paper_trading/stats.py:112` |
| **Status** | ⚠️ Needs Improvement |
| **Why** | L135: `leg_count = len(group) if ("TYPE-C" in opp_type ...) else 1`. Parity trades count 2 wins/losses per opportunity (one per leg). This inflates the win count and loss count equally, so the **win rate** is accurate, but the **absolute numbers** (e.g., "42W/2L") are misleading — they represent leg counts, not trade counts. This is confusing for dashboard display. |
| **Risk** | Low (display confusion, not financial). |
| **Recommended Action** | Document that W/L counts are per-leg for parity trades, or display trade count separately. |

---

### `bot/settings.py`

---

#### `Settings.load(config_path)`
| Attribute | Value |
|---|---|
| **File** | `bot/settings.py:88` |
| **Purpose** | Load settings from .env + TOML with merge |
| **Status** | 🔴 Immediate Fix Required |
| **Why** | **TOML values silently overwrite .env values.** The flow is: (1) `cls()` loads from .env via pydantic-settings, (2) TOML values then replace entire sub-settings objects. For example, if `.env` has `POLYMARKET_FEE=0.02` but TOML has `polymarket_fee = 0.03`, the TOML wins. More critically, if `.env` has API credentials and TOML has an `[api]` section with dummy values, the TOML values overwrite the real credentials. |
| | Additionally, L113: `settings.trading = TradingSettings(**toml_data["trading"])` creates a new `TradingSettings` with ONLY the TOML values. Any environment variables that pydantic-settings would normally load for `TradingSettings` are discarded. |
| **Evidence** | L109: `settings = cls()` — loads env vars. L113: `settings.trading = TradingSettings(**toml_data["trading"])` — replaces entire trading settings with TOML-only values, discarding any env var overrides. |
| **Risk** | High. Production deployments that rely on environment variables for configuration (e.g., Kubernetes, Docker, CI) will have their env vars silently overwritten by TOML defaults. |
| **Recommended Action** | Merge TOML into existing settings instead of replacing: `settings.trading = settings.trading.model_copy(update=toml_data["trading"])`. Or document that TOML takes absolute precedence over env vars. |

---

#### Settings — `execution.opportunity_dedup_window_s`
| Attribute | Value |
|---|---|
| **Status** | ⚠️ Needs Improvement |
| **Why** | `ExecutionSettings.opportunity_dedup_window_s = 60.0` is defined in settings but NEVER used. The `FillManager` has its own hardcoded `dedup_window_ms = 60000` (L14). These are the same value, but the settings parameter is dead. |
| **Risk** | Low. |
| **Recommended Action** | Wire `opportunity_dedup_window_s` into `FillManager.__init__()`, or remove from settings. |

---

### `bot/market_discovery/discovery.py`

---

#### `MarketDiscoveryService.discover_markets()`
| Attribute | Value |
|---|---|
| **File** | `bot/market_discovery/discovery.py:19` |
| **Purpose** | Poll for active markets matching target assets and windows |
| **Status** | ✅ Working Well |
| **Why** | Strict filtering: only target assets (btc, eth, sol, xrp) in target windows (5m, 15m). Checks both current and previous time windows to avoid missing markets near boundaries. Deduplicates by condition_id. Suppresses re-discovery log spam. |

---

### `bot/market_discovery/market_relationships.py`

---

#### `build_topology(markets)`
| Attribute | Value |
|---|---|
| **File** | `bot/market_discovery/market_relationships.py:23` |
| **Purpose** | Build parity markets list and monotonicity cross-join pairs |
| **Status** | ✅ Working Well |
| **Why** | Correct temporal containment check (L72): `5m_ts >= 15m_ts and 5m_ts + 300 <= 15m_ts + 900`. This ensures the 5-minute window falls within the 15-minute window. |

---

### `bot/persistence/`

---

#### `DatabaseManager` / `TradeRepository`
| Attribute | Value |
|---|---|
| **Status** | ✅ Working Well |
| **Why** | Clean SQLAlchemy 2.0 async pattern. `init_db()` creates tables. `add_trade()` commits individually. |
| **Risk** | Low. Individual commits per trade may cause performance issues under high load. |

---

#### Persistence Gap — `SessionRecord` is never used
| Attribute | Value |
|---|---|
| **Status** | ⚠️ Needs Improvement — Dead Code |
| **Why** | `SessionRecord` model (models.py:27) exists in the schema but is never written to. No code creates session records. |
| **Risk** | None |
| **Recommended Action** | Remove or implement session recording on startup/shutdown. |

---

### `bot/paper_trading/pnl.py`

---

#### `PnLTracker` class
| Attribute | Value |
|---|---|
| **File** | `bot/paper_trading/pnl.py:7` |
| **Status** | ⚠️ Needs Improvement — Dead Code |
| **Why** | This class is **never instantiated or used** anywhere in the codebase. The `TradingStats` class handles all PnL tracking. |
| **Risk** | None |
| **Recommended Action** | Remove entirely. |

---

### `bot/cli/papertrade.py` and `bot/cli/live.py`

---

#### `_setup_file_logging()` — duplicated in both files
| Attribute | Value |
|---|---|
| **Status** | ⚠️ Needs Improvement |
| **Why** | Identical logging setup code is duplicated between `papertrade.py` and `live.py`. Only the log filename differs. |
| **Risk** | Low (maintenance burden). |
| **Recommended Action** | Extract to a shared `setup_logging(mode: str)` function. |

---

#### `run_paper_trading()` / `run_live_trading()` — structural comparison
| Attribute | Value |
|---|---|
| **Status** | ✅ Working Well |
| **Why** | Both follow the same initialization, lifecycle, and shutdown pattern. The only differences are: (1) live fetches real balance, (2) live uses `LiveExecutor`, (3) paper takes `capital` parameter. |
| **Risk** | Low |

---

#### Shutdown cleanup
| Attribute | Value |
|---|---|
| **Status** | ⚠️ Needs Improvement |
| **Why** | The `finally` block cancels tasks but doesn't `await` them. Cancellation may not complete before the process exits. Also, `db` is never closed — the database engine connection pool is leaked. |
| **Evidence** | Paper L196-207: `ws_task.cancel()` but no `await ws_task`. No `await db.engine.dispose()`. |
| **Risk** | Low (process exit cleans up anyway, but not clean). |
| **Recommended Action** | Add `await asyncio.gather(*tasks, return_exceptions=True)` after cancellation. Add `await db.engine.dispose()`. |

---

## Categorized Findings — Infrastructure & Observability

### Working Well
- `PolymarketRESTClient.get_markets()` — robust JSON parsing
- `PolymarketRESTClient.get_orderbook()` — correct with graceful fallback
- `PolymarketWSClient.connect_and_run()` — exponential backoff
- `LocalOrderBook.apply_delta()` — correct delta semantics with locking
- `ForensicLogger.log_executed_opportunity()` — comprehensive execution context
- `MarketDiscoveryService.discover_markets()` — strict filtering
- `build_topology()` — correct temporal containment
- `DatabaseManager` / `TradeRepository` — clean async pattern

### Needs Improvement
- `PolymarketWSClient.subscribe()` — fire-and-forget subscription send
- WS JSON parsing — unnecessary thread pool executor
- `LocalOrderBook.best_bid/ask()` — O(n) on every call
- `TerminalDashboard.update()` — inline imports, derived pos_cost
- `HealthServer._handle_metrics()` — empty active_market_ids for PnL
- `_setup_file_logging()` — duplicated between CLI files
- Shutdown cleanup — tasks not awaited, DB not closed
- `SessionRecord` model — dead code
- `PnLTracker` class — dead code
- `opportunity_dedup_window_s` setting — dead code
- Forensic slippage calculation — missing fees
- `PolymarketRESTClient.place_order()` — no response schema validation
- `PolymarketRESTClient.get_balance_allowance()` — unverified USDC divisor

### Immediate Fix Required
- `sign_order()` — inverted makerAmount/takerAmount (blocks live trading)
- `sign_order()` — `int(token_id)` may fail on hex formats
- `HealthServer.start()` — fragile `app.app` pattern
- `Settings.load()` — TOML silently overwrites env vars
- `LiveExecutor.place_order()` — defaults missing status to "FILLED"
