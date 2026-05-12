import json
import statistics
import collections

file_path = 'data/trades/archive_1778596785_copy_89b5cdaa.json'

try:
    with open(file_path, 'r', encoding='utf-8') as f:
        trades = json.load(f)
except Exception as e:
    print(f"Error reading file: {e}")
    exit(1)

resolved_trades = [t for t in trades if t.get('status') in ('won', 'lost', 'sold')]
if not resolved_trades:
    print("No resolved trades found.")
    exit(0)

# 1. OUR PERFORMANCE
our_wins = [t for t in resolved_trades if t.get('pnl', 0) > 0]
our_losses = [t for t in resolved_trades if t.get('pnl', 0) <= 0]
our_wr = len(our_wins) / len(resolved_trades) * 100
our_pnl = sum(t.get('pnl', 0) for t in resolved_trades)

# 2. HIS PERFORMANCE (based on original_size and pnl proportionality, or just win/loss of the event)
# Since we copy his exact side, if the trade is won for us, it's won for him.
his_wins = our_wins
his_losses = our_losses
his_wr = our_wr # WR is identical because we hold to resolution or sell when he sells.
# Wait, let's see if his_wr differs if we missed some trades? The JSON only contains trades WE took.
# But we can calculate HIS hypothetical PnL on these exact trades:
his_pnl = 0.0
for t in resolved_trades:
    orig_size = float(t.get('original_size', 0))
    # Approximation of his PnL:
    # If won, he gets (1/entry - 1) * size. If lost, he loses size.
    # Actually, let's just look at his original sizes for wins vs losses
    pass

his_win_sizes = [float(t.get('original_size', 0)) for t in his_wins]
his_loss_sizes = [float(t.get('original_size', 0)) for t in his_losses]

# 3. COIN PERFORMANCE (Our perspective)
coin_stats = collections.defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl': 0.0})
for t in resolved_trades:
    slug = t.get('slug', '').lower()
    if 'btc' in slug or 'bitcoin' in slug:
        coin = 'BTC'
    elif 'eth' in slug or 'ethereum' in slug:
        coin = 'ETH'
    elif 'sol' in slug or 'solana' in slug:
        coin = 'SOL'
    else:
        coin = 'Other'
        
    pnl = t.get('pnl', 0)
    coin_stats[coin]['pnl'] += pnl
    if pnl > 0:
        coin_stats[coin]['wins'] += 1
    else:
        coin_stats[coin]['losses'] += 1

# 4. HIS BET SIZING ANALYSIS
# Bracket sizes: <$100, $100-$300, >$300
bracket_stats = {
    '<$100': {'wins': 0, 'losses': 0},
    '$100-$300': {'wins': 0, 'losses': 0},
    '>$300': {'wins': 0, 'losses': 0}
}

for t in resolved_trades:
    size = float(t.get('original_size', 0))
    pnl = t.get('pnl', 0)
    is_win = pnl > 0
    
    if size < 100:
        b = '<$100'
    elif size <= 300:
        b = '$100-$300'
    else:
        b = '>$300'
        
    if is_win:
        bracket_stats[b]['wins'] += 1
    else:
        bracket_stats[b]['losses'] += 1

print("========================================")
print(f"ANÁLISIS COPY TRADING (Noche Anterior)")
print(f"Total Trades Copiados y Resueltos: {len(resolved_trades)}")
print("========================================")
print("\n--- NUESTRO RENDIMIENTO ---")
print(f"Nuestro Win Rate: {our_wr:.2f}% ({len(our_wins)}W / {len(our_losses)}L)")
print(f"Nuestro PnL (Simulado): ${our_pnl:.2f}")

print("\n--- EL RENDIMIENTO DE OHANISM (En los trades que le copiamos) ---")
print(f"Su Win Rate: {our_wr:.2f}% (Idéntico al nuestro porque copiamos las mismas salidas)")
print(f"Promedio de su Apuesta cuando GANÓ: ${statistics.mean(his_win_sizes) if his_win_sizes else 0:.2f}")
print(f"Promedio de su Apuesta cuando PERDIÓ: ${statistics.mean(his_loss_sizes) if his_loss_sizes else 0:.2f}")

print("\n--- SU WIN RATE POR TAMAÑO DE APUESTA ---")
for k, v in bracket_stats.items():
    total = v['wins'] + v['losses']
    if total > 0:
        wr = v['wins'] / total * 100
        print(f"Apuestas {k}: {total} trades | WR: {wr:.1f}% ({v['wins']}W / {v['losses']}L)")
    else:
        print(f"Apuestas {k}: 0 trades")

print("\n--- NUESTRO PNL POR MONEDA ---")
for coin, st in coin_stats.items():
    total = st['wins'] + st['losses']
    wr = (st['wins'] / total * 100) if total > 0 else 0
    print(f"[{coin}] Trades: {total} | WR: {wr:.1f}% | PnL Nuestro: ${st['pnl']:.2f}")

# 5. DIAGNOSTICS: QUÉ HICIMOS BIEN Y QUÉ PODEMOS MEJORAR
print("\n--- DIAGNÓSTICO DE FILTROS ---")
# Check slippage (vwap entry_price vs original_price)
slippages = []
delays = []
for t in resolved_trades:
    orig_p = float(t.get('original_price', 0))
    our_p = float(t.get('entry_price', 0))
    if orig_p > 0:
        slippages.append(our_p - orig_p)
    delays.append(t.get('signal_delay_s', 0))

avg_slip = statistics.mean(slippages) if slippages else 0
avg_delay = statistics.mean(delays) if delays else 0

print(f"Deslizamiento (Slippage) Promedio: ${avg_slip:+.4f} por acción (Nuestro precio de compra vs el de él)")
print(f"Retraso de Señal Promedio: {avg_delay:.1f} segundos")

