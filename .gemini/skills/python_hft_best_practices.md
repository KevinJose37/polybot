# Python HFT & WebSocket — Best Practices & Conventions

This document defines the coding standards, conventions, and critical patterns
for all Python code in the **polystudio** project. Every code change, review,
and generation MUST adhere to these rules.

---

## 1. Python Style & PEPs

### PEP 8 — Style Guide
- **Indentation:** 4 spaces, no tabs.
- **Line length:** Max 100 characters (hard limit). Break long expressions with
  parenthetical continuation.
- **Imports:** Group in order: stdlib → third-party → local. One import per line.
  Use absolute imports (`from scalper.config import X`, not relative).
- **Naming:**
  - `snake_case` for functions, variables, module names.
  - `PascalCase` for classes.
  - `UPPER_SNAKE_CASE` for module-level constants.
  - Prefix private/internal names with underscore (`_lock`, `_ws_running`).
- **Trailing commas** in multi-line collections, function signatures, and dicts.
- **Docstrings:** All public functions MUST have a docstring (PEP 257).
  Use triple double-quotes. First line is a summary. Blank line before Args/Returns.

### PEP 484 / PEP 604 — Type Hints
- All function signatures MUST include type hints for parameters and return values.
- Use modern union syntax: `float | None` instead of `Optional[float]`.
- Use `list[str]` instead of `List[str]` (Python 3.10+).
- Complex return types should use `TypedDict` or `NamedTuple` instead of raw dicts.

### PEP 20 — The Zen of Python (Key Rules)
- **Explicit is better than implicit.** Never rely on default behavior silently.
  If a function can return `None`, the caller MUST check for it.
- **Errors should never pass silently.** Log exceptions with context.
  Bare `except:` is FORBIDDEN. Always catch specific exception types.
- **Flat is better than nested.** Max 3 levels of indentation in any function.
  Extract helper functions if deeper.

---

## 2. Critical Patterns for This Project

### 2.1 Thread-Safety (WebSocket ↔ Main Thread)

The WebSocket runs in a background thread. The main trading loop reads shared
state from the main thread. ALL access to shared buffers MUST be protected.

```python
# ✅ CORRECT: Read and process inside the lock
with _lock:
    history = list(_mid_history.get(token_id, []))  # snapshot inside lock
    if not history:
        return 0.0
    current = history[-1][1]
    oldest = history[0][1]
return round(current - oldest, 4)

# ❌ WRONG: Read reference inside lock, iterate outside (race condition)
with _lock:
    history = _mid_history.get(token_id)  # just a reference!
# history can be mutated by WS thread RIGHT HERE
for t, p in history:  # RuntimeError: deque mutated during iteration
    ...
```

**Rules:**
- If you read a `deque` or mutable collection from `_mid_history`, `_prices`,
  or `_orderbooks`, you MUST either:
  1. Copy the entire collection inside `with _lock:`, OR
  2. Perform ALL computation inside `with _lock:`.
- Never hold the lock while performing I/O (network calls, file writes, prints).
- The `update_mid_history()` function expects `_lock` to be held by the caller.

### 2.2 Mid-Price Calculation (Polymarket)

Polymarket markets frequently have one-sided liquidity (only bids OR only asks).
The mid-price calculation MUST handle this gracefully:

```python
# ✅ CORRECT: Use if/elif to avoid dividing by zero-side
if best_bid > 0 and best_ask > 0:
    mid = (best_bid + best_ask) / 2.0
elif best_bid > 0:
    mid = best_bid
elif best_ask > 0:
    mid = best_ask
else:
    return  # no data at all

# ❌ WRONG: Averaging with a zero side halves the real price
if best_bid > 0 or best_ask > 0:
    mid = (best_bid + best_ask) / 2.0  # If bid=0, ask=0.50 → mid=0.25 (WRONG!)
```

### 2.3 Orderbook Snapshot vs Incremental Updates

Polymarket sends two types of messages:
- `book`: Full snapshot (all bids and asks). Sent on initial subscription.
- `price_change`: Incremental update (single order added/removed).

**BOTH must feed `update_mid_history()`.** The snapshot (`book`) is the first
and most critical price sample — it establishes the velocity baseline. If you
forget to call `update_mid_history()` in the snapshot handler, the velocity
buffer stays empty for 60-120 seconds.

### 2.4 Token ID Lifecycle

Every 5-minute market on Polymarket creates NEW token IDs. Code MUST:
- Subscribe new tokens to the WS on every market scan cycle.
- Clear stale tokens from buffers when calling `replace_subscriptions()`.
- Never assume a token ID persists across market rotations.
- Map outcomes dynamically: outcomes can be `["Up", "Down"]` OR `["Yes", "No"]`
  OR `["Down", "Up"]`. NEVER hardcode index 0 = UP.

```python
# ✅ CORRECT: Dynamic outcome matching
for i, o in enumerate(outcomes):
    ol = str(o).lower()
    if ol in ("yes", "up"):
        up_idx = i
    elif ol in ("no", "down"):
        down_idx = i
```

### 2.5 Price Validation Before Execution

All entry and exit prices MUST be re-validated against live REST data immediately
before order submission. The display price (from periodic scans) can be stale.

```python
# ✅ CORRECT: Double-check price right before buying
rest_book = fetch_rest_book(token_id)
true_ask = rest_book["best_ask"]
if true_ask < min_entry_price or true_ask > poly_price_cap:
    return SKIP  # Price moved out of golden zone

# ❌ WRONG: Trust the cached scanner price for execution
entry_price = market["up_price"]  # Could be 30 seconds old
execute_buy(token_id, entry_price)  # May buy at a completely different price
```

---

## 3. Error Handling & Logging

### Logging Levels
- `logger.debug()` — Verbose data (buffer sizes, raw WS messages). Off in production.
- `logger.info()` — Key events (subscriptions, trades, reconnections).
- `logger.warning()` — Recoverable issues (stale data, thin liquidity).
- `logger.error()` — Failures that need attention (WS disconnect, API errors).
- `logger.critical()` — Data corruption, impossible states.

### Exception Rules
```python
# ✅ CORRECT: Specific exception with context
try:
    response = await ws.recv()
except websockets.ConnectionClosed as e:
    logger.warning("WS connection closed: code=%s reason=%s", e.code, e.reason)
    break
except asyncio.TimeoutError:
    # Expected — check for pending subscriptions
    pass

# ❌ WRONG: Silently swallowing all errors
try:
    response = await ws.recv()
except:
    pass
```

### Defensive Float Parsing
Polymarket API returns strings for numeric fields. Always wrap with defaults:
```python
price = float(change.get("price", 0) or 0)   # Handles None, "", and missing
size = float(change.get("size", 0) or 0)
```

---

## 4. asyncio & WebSocket Patterns

### Event Loop Isolation
The WS runs in its own thread with its own event loop. Never call
`asyncio.run()` from a thread that already has a loop.

```python
# ✅ CORRECT: Create a new loop for the background thread
def _ws_thread_entry():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_ws_main())
```

### Hot-Subscription Without Deadlock
When subscribing to new tokens, the WS loop must check for pending subs
even when no messages are arriving (markets between rotations go quiet):

```python
# ✅ CORRECT: Use wait_for with timeout to avoid deadlock
while running:
    try:
        msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
        process(msg)
    except asyncio.TimeoutError:
        pass  # No message — check for pending subs below

    if pending_subscribe:
        await ws.send(json.dumps(sub_message))

# ❌ WRONG: async for blocks indefinitely if no messages arrive
async for msg in ws:  # Blocks here forever if old tokens are resolved
    if pending_subscribe:  # Never reached!
        await ws.send(...)
```

---

## 5. Testing & Verification Checklist

Before any code change is considered complete:

- [ ] **Thread-safety audit:** Any shared buffer access (`_mid_history`,
      `_prices`, `_orderbooks`) is fully inside `with _lock:`.
- [ ] **Mid-price correctness:** One-sided liquidity (bid=0 or ask=0) does
      not distort the calculated mid.
- [ ] **History buffer feed:** Both `_parse_book_message` and
      `_parse_price_change` call `update_mid_history()`.
- [ ] **Velocity edge cases:** `get_mid_velocity()` returns a meaningful
      value even with only 1-2 samples (no arbitrary "too sparse" cutoffs).
- [ ] **Outcome mapping:** `"Up"/"Down"` and `"Yes"/"No"` are both handled.
- [ ] **No bare except blocks.**
- [ ] **All functions have type hints and docstrings.**
- [ ] **REST price re-validation before every order submission.**
