import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Polymarket Bots Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Paths & Config ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DASHBOARD_DIR = BASE_DIR / "dashboard"
LOGS_DIR = BASE_DIR / "logs"

# Ensure dirs exist
DASHBOARD_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Mount static files for dashboard
app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")

# State to track running bot processes
# Format: { "v1": {"process": Popen_obj, "start_time": float, "params": dict} }
running_bots = {}

# ── Models ──────────────────────────────────────────────────────────────────
class BotStartParams(BaseModel):
    strategy: str
    stake: float = 10.0
    duration: int = 5
    interval: int = 5

# ── Helpers ──────────────────────────────────────────────────────────────────

def read_trades_file(filename: str) -> list:
    path = BASE_DIR / filename
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def get_bot_stats(strategy: str) -> dict:
    """Aggregate stats from the trades file for a strategy."""
    # Mapping strategies to their trades file
    if strategy == "v1":
        file_name = "hft_trades.json"
    else:
        file_name = f"hft_trades_{strategy}.json"
        
    trades = read_trades_file(file_name)
    
    total_pnl = 0.0
    wins = 0
    losses = 0
    total_resolved = 0
    open_count = 0
    open_positions = []
    
    for t in trades:
        status = t.get("status", "open")
        if status in ("won", "lost", "sold"):
            pnl = t.get("pnl", 0) or 0
            total_pnl += pnl
            total_resolved += 1
            if pnl > 0:
                wins += 1
            else:
                losses += 1
        elif status == "open":
            open_count += 1
            open_positions.append(t)
            
    win_rate = (wins / total_resolved * 100) if total_resolved > 0 else 0
    
    return {
        "strategy": strategy,
        "total_pnl": round(total_pnl, 2),
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "open_count": open_count,
        "total_resolved": total_resolved,
        "open_positions": open_positions,
        "recent_history": sorted([t for t in trades if t.get("status") != "open"], key=lambda x: x.get("exit_time", ""), reverse=True)[:5],
        "is_running": strategy in running_bots
    }

# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
def serve_index():
    return FileResponse(str(DASHBOARD_DIR / "index.html"))

@app.get("/api/status")
def get_status():
    """Return global status of all known strategies."""
    strategies = ["v1", "v2", "v3", "v1opt", "v2opt", "v4", "v2opt2", "v2opt3", "v5", "copy"]
    
    stats_list = []
    global_pnl = 0.0
    global_open = 0
    
    # Also check if any running bot process died
    for s in list(running_bots.keys()):
        proc = running_bots[s]["process"]
        if proc.poll() is not None:
            # Process ended
            del running_bots[s]
    
    for s in strategies:
        st = get_bot_stats(s)
        global_pnl += st["total_pnl"]
        global_open += st["open_count"]
        stats_list.append(st)
        
    return {
        "global_pnl": round(global_pnl, 2),
        "global_open": global_open,
        "active_bots": len(running_bots),
        "strategies": stats_list
    }

@app.post("/api/bots/start")
def start_bot(params: BotStartParams):
    strategy = params.strategy
    if strategy in running_bots:
        if running_bots[strategy]["process"].poll() is None:
            raise HTTPException(status_code=400, detail=f"Bot {strategy} is already running.")
            
    log_file_path = LOGS_DIR / f"{strategy}.log"
    log_file = open(log_file_path, "a", encoding="utf-8")
    
    cmd = [
        "python", "bot.py",
        "--mode", "scalp",
        "--strategy", strategy,
        "--stake", str(params.stake),
        "--duration", str(params.duration),
        "--interval", str(params.interval)
    ]
    
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=str(BASE_DIR)
        )
        running_bots[strategy] = {
            "process": proc,
            "start_time": time.time(),
            "params": params.model_dump()
        }
        return {"status": "started", "strategy": strategy, "pid": proc.pid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/bots/stop/{strategy}")
def stop_bot(strategy: str):
    if strategy not in running_bots:
        raise HTTPException(status_code=404, detail=f"Bot {strategy} is not running.")
        
    proc = running_bots[strategy]["process"]
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            
    del running_bots[strategy]
    return {"status": "stopped", "strategy": strategy}

if __name__ == "__main__":
    import uvicorn
    print("Starting Dashboard API on http://localhost:8000")
    uvicorn.run("dashboard_api:app", host="0.0.0.0", port=8000, reload=True)
