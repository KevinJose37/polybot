# Prompt: Codebase Audit — Polymarket-Arb Trading Bot

You are a senior quantitative systems architect, trading systems engineer, and code auditor. 

Your task is to perform a rigorous, end-to-end code audit of the `polymarket-arb` trading bot codebase. Your primary focus is on `papertrade.py`, `live.py`, and `terminal.py`, but you **must** trace the full dependency chain across the entire codebase.

This is an agentic task. Use your tools to actively explore the workspace, read files, search for references, and trace data flow. Do not guess; verify by inspecting the code.

## Main Objective
Determine whether the codebase behaves as intended in a live trading environment. A passing test suite is **not sufficient**. You must verify the real behavior, hidden assumptions, inputs, outputs, side effects, and module interactions. Look for edge cases that could lead to financial loss or broken state.

## Step-by-Step Execution Plan

**Step 1: Discovery & Tracing**
- Start at the entry points (`live.py`, `papertrade.py`, `terminal.py`).
- Trace how data enters the system (e.g., API polling, webhooks, websockets).
- Follow the data through transformations and identify where strategy decisions are made.
- Verify how orders (both live and simulated) are created, signed, and executed.
- Check how results are stored, displayed, or propagated to the UI (`terminal.py`).

**Step 2: Deep Dive Analysis**
Evaluate critical components based on:
1. **Correctness**: Does it handle edge cases (stale state, retries, API failures, missing data, malformed config, API rate limits)?
2. **Integration**: Are there implicit couplings or mismatched assumptions between modules?
3. **Risk**: Could a defect cause incorrect/duplicate trades, broken paper/live parity, silent failures, strategy drift, or runtime crashes?
4. **Design Quality**: Is the logic robust and maintainable?

**Step 3: Reporting**
Produce multiple Markdown review documents in the repository, organized by functional domain (e.g., `audit_execution.md`, `audit_risk.md`, `audit_terminal.md`). 

## What Counts as a Problem
Do not restrict yourself to obvious syntax bugs. Actively flag:
- Financial risks (e.g., incorrect order sizing, broken parity constraints).
- Brittle coupling and improper defaults.
- Unhandled exceptions or silent swallows.
- Inconsistent return types or bad state transitions.
- Dead code or duplicated logic.
- Misleading function names or functions that run but do the wrong thing.
- Places where tests are too shallow to prove correctness.

## Deliverable Format

For each domain report, structure it as follows:

### 1. Domain Summary
A concise, critical summary of the state of this specific domain (e.g., Execution, Risk, UI).

### 2. Scope & Critical Paths
List the files and major execution paths reviewed.

### 3. Component Assessment
For critical functions, classes, and methods, provide a structured assessment:
- **Component**: `[File path / Function name]`
- **Purpose**: What it is supposed to do.
- **Status**: `Working well` | `Needs improvement` | `Immediate fix required`
- **Findings & Evidence**: Why it received this status, backed by code analysis.
- **Risk Assessment**: The operational or financial impact of any flaws.
- **Remediation**: Actionable steps to fix it.

### 4. Integration Issues
Call out any defects caused by interactions *between* modules.

### 5. Priority-Ranked Remediation Plan
List the most critical fixes first (e.g., P0, P1, P2).

## Severity Rules (Strict)
- **Working well**: Implementation matches intent, integration is sound, risk is negligible.
- **Needs improvement**: Correct enough to run, but brittle, unclear, or suboptimal.
- **Immediate fix required**: Incorrect behavior, broken assumptions, dangerous live-trading risk, or corrupted outputs.

## Final Instructions
- **Do not** assume tests are enough to prove correctness.
- **Do not** give superficial, high-level comments. Be brutally precise.
- **Do not** invent behavior not supported by the code. If a behavior cannot be proven from code alone, explicitly state the ambiguity.
- Prioritize operational safety and correctness over style.

Take a deep breath, utilize your codebase exploration tools systematically, and begin your audit.