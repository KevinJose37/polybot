import json
import glob
from datetime import datetime

def print_stats(title, strategies, all_trades):
    print(f"\n{'='*40}")
    print(f" {title} ")
    print(f"{'='*40}")
    
    print("\n=== RENDIMIENTO POR ESTRATEGIA ===")
    strat_results = []
    for strat, trades in strategies.items():
        closed = [t for t in trades if t.get("status") in ["won", "lost", "sold"]]
        if not closed: continue
        wins = len([t for t in closed if t.get("pnl", 0) > 0])
        losses = len(closed) - wins
        wr = wins / len(closed) * 100
        pnl = sum(t.get("pnl", 0) for t in closed)
        strat_results.append((strat, len(closed), wins, wr, pnl))
        
    strat_results.sort(key=lambda x: x[4], reverse=True)
    for s, count, wins, wr, pnl in strat_results:
        print(f"[{s.upper():<8}] Trades: {count:<3} | Victorias: {wins:<2} | WR: {wr:5.1f}% | P&L: ${pnl:6.2f}")
        
    print("\n=== RENDIMIENTO GENERAL POR ACTIVO ===")
    assets = {}
    for strat, t in all_trades:
        a = t.get("asset")
        if a not in assets:
            assets[a] = {"trades": 0, "wins": 0, "pnl": 0.0}
        assets[a]["trades"] += 1
        if t.get("pnl", 0) > 0:
            assets[a]["wins"] += 1
        assets[a]["pnl"] += t.get("pnl", 0)
        
    for a, data in sorted(assets.items(), key=lambda x: x[1]['pnl'], reverse=True):
        wr = data["wins"] / data["trades"] * 100 if data["trades"] else 0
        print(f"[{a:<4}] Trades: {data['trades']:<3} | WR: {wr:5.1f}% | P&L: ${data['pnl']:6.2f}")

    print("\n=== TOXICIDAD DE ACTIVOS POR ESTRATEGIA ===")
    for strat, trades in strategies.items():
        closed = [t for t in trades if t.get("status") in ["won", "lost", "sold"]]
        if not closed: continue
        strat_assets = {}
        for t in closed:
            a = t.get("asset")
            if a not in strat_assets:
                strat_assets[a] = {"trades": 0, "wins": 0, "pnl": 0.0}
            strat_assets[a]["trades"] += 1
            if t.get("pnl", 0) > 0:
                strat_assets[a]["wins"] += 1
            strat_assets[a]["pnl"] += t.get("pnl", 0)
            
        print(f"\n{strat.upper()}:")
        for a, data in sorted(strat_assets.items(), key=lambda x: x[1]['pnl'], reverse=True):
            wr = data["wins"] / data["trades"] * 100 if data["trades"] else 0
            print(f"  {a:<4}: {data['trades']:<2} trades | WR: {wr:5.1f}% | P&L: ${data['pnl']:6.2f}")

def analyze():
    files = glob.glob("backups/may07_night/hft_trades*.json")
    
    night_strategies = {}
    full_day_strategies = {}
    
    night_trades = []
    full_day_trades = []
    
    for f in files:
        strategy_name = f.replace("backups/may07_night\\hft_trades", "").replace("backups/may07_night/hft_trades", "").replace(".json", "")
        if strategy_name == "":
            strategy_name = "v1"
        else:
            strategy_name = strategy_name.strip("_")
            
        try:
            with open(f, "r") as file:
                trades = json.load(file)
        except:
            continue
            
        if strategy_name not in night_strategies:
            night_strategies[strategy_name] = []
            full_day_strategies[strategy_name] = []
            
        for t in trades:
            if t.get("status") not in ["won", "lost", "sold"]:
                continue
                
            full_day_strategies[strategy_name].append(t)
            full_day_trades.append((strategy_name, t))
            
            if t.get("entry_time", "") > "2026-05-07T22:00:00":
                night_strategies[strategy_name].append(t)
                night_trades.append((strategy_name, t))

    print_stats("ANÁLISIS DE LA SESIÓN NOCTURNA (Después de las 6:00 PM)", night_strategies, night_trades)
    print_stats("ANÁLISIS DEL DÍA COMPLETO", full_day_strategies, full_day_trades)

if __name__ == "__main__":
    analyze()
