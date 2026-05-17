# Polymarket-Arb Codebase Audit — Executive Summary (COMPLETED)

> [!NOTE]
> All issues and vulnerabilities identified in this audit and its sub-audits have been fully addressed and tested.

**Audit Date:** 2026-05-17  
**Auditor:** Antigravity Code Audit System  
**Scope:** Full end-to-end audit of `polymarket-arb` trading bot  
**Target Scripts:** `papertrade.py`, `live.py`, `terminal.py` and all transitive dependencies  

---

## Executive Summary

The codebase implements a **Polymarket binary-option arbitrage bot** targeting up/down crypto markets across 5-minute and 15-minute intervals. It supports three strategy types:

- **Type-A (Parity):** Buy YES + NO when ask sum < $1.00 — subsumed by Type-C
- **Type-B (Monotonicity):** Cross-timeframe probability dislocation (sell 5m, buy 15m)
- **Type-C (Exhaustive):** Generalized parity detecting both BUY and SELL dislocations

### Overall Assessment: **Functional with Material Defects**

The architecture is well-organized with clean separation of concerns: detectors are pure functions, execution follows a protocol pattern for paper/live parity, risk management includes atomic exposure reservation, and observability is solid. The codebase has clearly been through multiple audit-remediation cycles.

However, **several correctness, resilience, and operational safety issues remain** that could cause incorrect PnL reporting, missed risk enforcement, silent data loss, and—in the live trading path—real financial exposure from unhedged leg imbalances.

### Severity Distribution

| Severity | Count | Impact |
|---|---|---|
| **Immediate Fix Required** | 8 | Incorrect PnL, unhedged live exposure, data loss, silent failures |
| **Needs Improvement** | 14 | Brittleness, maintainability, operational risk |
| **Working Well** | 20+ | Core architecture, detection logic, risk framework |

### Top 5 Critical Issues

1. **EIP-712 signer has inverted maker/taker amounts** — live orders will be malformed, causing rejections or incorrect fill sizes on-chain (signer.py L60-61)
2. **PositionManager.add_fill division-by-zero** when `new_size == 0` during position flip (position_manager.py L101)
3. **Live executor records `no_liquidity` for ALL exceptions**, including network errors, signature failures, and API throttling — corrupts statistics (live_engine.py L233)
4. **Health server `app.app` access before `setup()`** — the `start()` method has an incorrect construction pattern that will crash on startup (health.py L78-80)
5. **Settings TOML-env merge is non-atomic** — env vars are loaded first, then overwritten by TOML, meaning `.env` credentials can be silently replaced by TOML defaults (settings.py L109-125)

### What Works Well

- Pure-function detector design (parity, exhaustive, monotonicity)
- Atomic exposure reservation in risk engine
- Parity-aware mark-to-market valuation
- Pluggable clock system for deterministic testing
- Warmup gate to prevent stale-book trading
- Forensic JSONL logger with full execution context
- Absence-counting for market resolution (prevents premature settlement)

---

> [!CAUTION]
> **This codebase should NOT be used for live trading until all "Immediate Fix Required" items are resolved.** The signer bug alone guarantees malformed on-chain orders. The position manager division-by-zero will crash during normal operation when positions fully close and re-open in the opposite direction.

---

## Audit Documents Index

| Document | Contents |
|---|---|
| [01 — Executive Summary](./code_audit_01_executive_summary.md) | This document |
| [02 — Scope & Architecture](./code_audit_02_scope_architecture.md) | Files reviewed, data flow, architecture |
| [03 — Strategy & Detection](./code_audit_03_strategy_detection.md) | Arbitrage detectors, scanner, math |
| [04 — Execution & Position Management](./code_audit_04_execution_positions.md) | Executors, position tracking, fill management |
| [05 — Risk & Safety](./code_audit_05_risk_safety.md) | Risk engine, kill switch, exposure limits |
| [06 — Infrastructure & Observability](./code_audit_06_infrastructure.md) | API, WebSocket, persistence, health, dashboard |
| [07 — Remediation Plan](./code_audit_07_remediation.md) | Priority-ranked fixes and confidence notes |
