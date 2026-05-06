const API_BASE = "http://localhost:8000/api";
const POLL_INTERVAL = 3000; // 3 seconds

// State
let botsData = [];

// DOM Elements
const botsContainer = document.getElementById("bots-container");
const globalPnl = document.getElementById("global-pnl");
const globalActive = document.getElementById("global-active");
const globalOpen = document.getElementById("global-open");
const template = document.getElementById("bot-card-template");

// Utility to format currency
const formatUSD = (val) => {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
};

// Fetch data from API
async function fetchStatus() {
    try {
        const res = await fetch(`${API_BASE}/status`);
        const data = await res.json();
        
        updateGlobalStats(data);
        updateBotsList(data.strategies);
    } catch (err) {
        console.error("Failed to fetch status", err);
    }
}

function updateGlobalStats(data) {
    globalPnl.textContent = formatUSD(data.global_pnl);
    globalPnl.className = "value " + (data.global_pnl >= 0 ? "positive" : "negative");
    
    globalActive.textContent = data.active_bots;
    globalOpen.textContent = data.global_open;
}

function updateBotsList(strategies) {
    // If first load, clear spinner
    if (botsContainer.querySelector(".loading-spinner")) {
        botsContainer.innerHTML = "";
    }

    strategies.forEach(strategy => {
        let card = document.getElementById(`bot-${strategy.strategy}`);
        
        if (!card) {
            // Create new card
            const clone = template.content.cloneNode(true);
            card = clone.querySelector(".bot-card");
            card.id = `bot-${strategy.strategy}`;
            card.dataset.strategy = strategy.strategy;
            
            card.querySelector(".strategy-name").textContent = strategy.strategy;
            
            // Event listeners
            card.querySelector(".btn-config").addEventListener("click", () => toggleConfig(card));
            card.querySelector(".btn-start").addEventListener("click", () => startBot(strategy.strategy, card));
            card.querySelector(".btn-stop").addEventListener("click", () => stopBot(strategy.strategy));
            
            botsContainer.appendChild(card);
        }
        
        // Update state
        updateBotCard(card, strategy);
    });
}

function updateBotCard(card, data) {
    const isRunning = data.is_running;
    
    // Status badge
    const badge = card.querySelector(".status-badge");
    badge.textContent = isRunning ? "RUNNING" : "STOPPED";
    badge.className = `status-badge ${isRunning ? "running" : "stopped"}`;
    
    // Card border
    if (isRunning) card.classList.add("running");
    else card.classList.remove("running");
    
    // Buttons
    card.querySelector(".btn-start").classList.toggle("hidden", isRunning);
    card.querySelector(".btn-stop").classList.toggle("hidden", !isRunning);
    card.querySelector(".btn-config").classList.toggle("hidden", isRunning); // Hide config while running
    if (isRunning) card.querySelector(".config-panel").classList.add("hidden");
    
    // Stats
    const pnlEl = card.querySelector(".pnl-val");
    pnlEl.textContent = formatUSD(data.total_pnl);
    pnlEl.className = "val pnl-val " + (data.total_pnl >= 0 ? "positive" : "negative");
    
    card.querySelector(".wr-val").textContent = `${data.win_rate}%`;
    card.querySelector(".trades-val").textContent = data.total_resolved;
    
    // Positions
    const posList = card.querySelector(".positions-list");
    posList.innerHTML = ""; // Clear
    
    // Render Open
    data.open_positions.forEach(p => {
        posList.appendChild(createTradeElement(p));
    });
    
    // Render Recent (limit to 3 visually so it doesn't get huge)
    data.recent_history.slice(0, 3).forEach(p => {
        posList.appendChild(createTradeElement(p));
    });
}

function createTradeElement(trade) {
    const el = document.createElement("div");
    
    const statusClass = trade.status === "open" ? "open" : (trade.pnl > 0 ? "won" : "lost");
    el.className = `trade-item ${statusClass}`;
    
    const resultText = trade.status === "open" 
        ? "OPEN" 
        : formatUSD(trade.pnl);
        
    el.innerHTML = `
        <div class="trade-info">
            <span class="trade-asset">${trade.asset} <span class="trade-side">${trade.side}</span></span>
            <span style="font-size: 0.65rem; color: var(--text-secondary)">@ $${trade.entry_price} • $${trade.stake}</span>
        </div>
        <div class="trade-result ${statusClass}">
            ${resultText}
        </div>
    `;
    
    return el;
}

function toggleConfig(card) {
    card.querySelector(".config-panel").classList.toggle("hidden");
}

async function startBot(strategy, card) {
    const stake = parseFloat(card.querySelector(".config-stake").value) || 10;
    const interval = parseInt(card.querySelector(".config-interval").value) || 5;
    const duration = parseInt(card.querySelector(".config-duration").value) || 5;
    
    try {
        const res = await fetch(`${API_BASE}/bots/start`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ strategy, stake, interval, duration })
        });
        
        if (!res.ok) {
            const err = await res.json();
            alert(`Failed to start: ${err.detail}`);
            return;
        }
        
        // Hide config
        card.querySelector(".config-panel").classList.add("hidden");
        // Force immediate refresh
        fetchStatus();
    } catch (err) {
        console.error(err);
        alert("Error starting bot");
    }
}

async function stopBot(strategy) {
    try {
        const res = await fetch(`${API_BASE}/bots/stop/${strategy}`, {
            method: "POST"
        });
        
        if (!res.ok) {
            const err = await res.json();
            alert(`Failed to stop: ${err.detail}`);
            return;
        }
        
        // Force immediate refresh
        fetchStatus();
    } catch (err) {
        console.error(err);
        alert("Error stopping bot");
    }
}

// Start polling
fetchStatus();
setInterval(fetchStatus, POLL_INTERVAL);
