"""
build_v9_lookup.py — Genera la tabla de decisión para V9.

Lee analysis/v9_training_data.json y agrupa por (time_of_day, asset, price_bucket)
para encontrar qué estrategia tiene mejor rendimiento en cada contexto.

Output: scalper/v9_lookup_table.json
"""
import json
import os
from collections import defaultdict

MIN_SAMPLE_SIZE = 15   # Mínimo trades para considerar un patrón confiable
FALLBACK_STRATEGY = "V2opt3"


def compute_sharpe_like(wins: int, total: int, total_pnl: float) -> float:
    """
    Composite metric: WR × avg_pnl.
    Positive = profitable in expectation.
    """
    if total == 0:
        return -999
    wr = wins / total
    avg_pnl = total_pnl / total
    return wr * avg_pnl


def main():
    # Load dataset
    with open("analysis/v9_training_data.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    
    trades = data["trades"]
    print(f"Loaded {len(trades)} trades from dataset\n")

    # ── Group by context key ─────────────────────────────────────
    # Key: "time_of_day|asset|price_bucket"
    context_groups = defaultdict(lambda: defaultdict(list))
    
    for t in trades:
        key = f"{t['time_of_day']}|{t['asset']}|{t['price_bucket']}"
        strategy = t["strategy"]
        context_groups[key][strategy].append(t)
    
    # ── Build lookup table ───────────────────────────────────────
    lookup_table = {}
    total_keys = 0
    fallback_keys = 0
    confident_keys = 0
    
    for context_key in sorted(context_groups.keys()):
        strategies = context_groups[context_key]
        total_keys += 1
        
        # Aggregate stats for ALL strategies in this context
        all_stats = {}
        total_trades_in_context = 0
        
        for strategy, strat_trades in strategies.items():
            n = len(strat_trades)
            wins = sum(1 for t in strat_trades if t["result"] == "won")
            total_pnl = sum(t["pnl"] for t in strat_trades)
            wr = round(wins / n * 100, 1) if n > 0 else 0
            avg_pnl = round(total_pnl / n, 4) if n > 0 else 0
            sharpe = compute_sharpe_like(wins, n, total_pnl)
            
            all_stats[strategy] = {
                "n": n,
                "wins": wins,
                "wr": wr,
                "total_pnl": round(total_pnl, 4),
                "avg_pnl": avg_pnl,
                "sharpe": round(sharpe, 6),
            }
            total_trades_in_context += n
        
        # Find the best strategy for this context
        # Only consider strategies with >= MIN_SAMPLE_SIZE trades
        confident_strategies = {
            s: st for s, st in all_stats.items() 
            if st["n"] >= MIN_SAMPLE_SIZE
        }
        
        if confident_strategies:
            # Best = highest sharpe-like metric
            best_strategy = max(
                confident_strategies.keys(),
                key=lambda s: confident_strategies[s]["sharpe"]
            )
            best = confident_strategies[best_strategy]
            
            # Build alternatives (other strategies with enough data)
            alternatives = {}
            for s, st in confident_strategies.items():
                if s != best_strategy:
                    alternatives[s] = {
                        "wr": st["wr"],
                        "avg_pnl": st["avg_pnl"],
                        "n": st["n"],
                        "sharpe": st["sharpe"],
                    }
            
            lookup_table[context_key] = {
                "winner": best_strategy,
                "wr": best["wr"],
                "avg_pnl": best["avg_pnl"],
                "sharpe": best["sharpe"],
                "sample_size": best["n"],
                "total_trades_in_context": total_trades_in_context,
                "is_fallback": False,
                "alternatives": alternatives,
            }
            confident_keys += 1
        else:
            # Not enough data — aggregate across ALL strategies for WR estimate
            total_wins = sum(st["wins"] for st in all_stats.values())
            total_pnl = sum(st["total_pnl"] for st in all_stats.values())
            agg_wr = round(total_wins / total_trades_in_context * 100, 1) if total_trades_in_context > 0 else 50
            agg_avg_pnl = round(total_pnl / total_trades_in_context, 4) if total_trades_in_context > 0 else 0
            
            lookup_table[context_key] = {
                "winner": FALLBACK_STRATEGY,
                "wr": agg_wr,
                "avg_pnl": agg_avg_pnl,
                "sharpe": round(compute_sharpe_like(total_wins, total_trades_in_context, total_pnl), 6),
                "sample_size": total_trades_in_context,
                "total_trades_in_context": total_trades_in_context,
                "is_fallback": True,
                "all_strategies": {
                    s: {"n": st["n"], "wr": st["wr"]}
                    for s, st in all_stats.items()
                },
            }
            fallback_keys += 1
    
    # ── Save ─────────────────────────────────────────────────────
    output_path = os.path.join("scalper", "v9_lookup_table.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(lookup_table, f, indent=2, ensure_ascii=False)
    
    # ── Print results ────────────────────────────────────────────
    print("=" * 80)
    print("  V9 LOOKUP TABLE BUILDER — RESULTS")
    print("=" * 80)
    
    print(f"\n  Total context keys:     {total_keys}")
    print(f"  Confident (>={MIN_SAMPLE_SIZE} trades): {confident_keys}")
    print(f"  Fallback (V2opt3):      {fallback_keys}")
    
    print(f"\n  {'Context Key':<35} {'Winner':<10} {'WR':>6} {'AvgPnL':>8} {'N':>5} {'Fallback':>9}")
    print(f"  {'-'*35} {'-'*10} {'-'*6} {'-'*8} {'-'*5} {'-'*9}")
    
    for key in sorted(lookup_table.keys()):
        entry = lookup_table[key]
        fb = "YES" if entry["is_fallback"] else ""
        icon = "[*]" if not entry["is_fallback"] else "[F]"
        print(
            f"  {icon} {key:<33} {entry['winner']:<10} "
            f"{entry['wr']:>5.1f}% ${entry['avg_pnl']:>+6.3f} "
            f"{entry['sample_size']:>5} {fb:>9}"
        )
    
    # Winner distribution
    print(f"\n  Strategy Selection Distribution:")
    winner_counts = defaultdict(int)
    for entry in lookup_table.values():
        if not entry["is_fallback"]:
            winner_counts[entry["winner"]] += 1
    for s, n in sorted(winner_counts.items(), key=lambda x: -x[1]):
        print(f"    {s}: selected as winner in {n} context(s)")
    print(f"    {FALLBACK_STRATEGY}: fallback in {fallback_keys} context(s)")
    
    # Highlight high-confidence patterns
    print(f"\n  High-Confidence Patterns (WR >= 60%, N >= {MIN_SAMPLE_SIZE}):")
    for key in sorted(lookup_table.keys()):
        e = lookup_table[key]
        if not e["is_fallback"] and e["wr"] >= 60:
            print(f"    [!!] {key}: {e['winner']} WR={e['wr']}% N={e['sample_size']}")
    
    print(f"\n  Dangerous Contexts (WR < 45%):")
    for key in sorted(lookup_table.keys()):
        e = lookup_table[key]
        if e["wr"] < 45:
            print(f"    [!!] {key}: WR={e['wr']}% -> V9 will PENALIZE entries here")
    
    print(f"\n  Output saved to: {output_path}")


if __name__ == "__main__":
    main()
