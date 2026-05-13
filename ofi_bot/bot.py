import asyncio
import os
import sys
import logging
from loguru import logger
from collections import deque

# Suprimir logs estándar (ej: websockets) que se imprimen en la consola e ignoran a loguru
logging.getLogger().setLevel(logging.CRITICAL)

from config.settings import config
from data.database import db
from data.collector import DataCollector
from data.feeds.binance_book import BinanceOrderBookFeed
from data.feeds.binance_trades import BinanceTradeFeed
from data.feeds.polymarket import PolymarketFeed

from signals.aggregator import SignalAggregator
from models.probability import ProbabilityModel
from sizing.kelly import KellySizer
from execution.polymarket_client import PolymarketClient
from execution.risk_manager import RiskManager
from execution.market_discovery import MarketDiscovery
from execution.paper_trader import PaperTrader
from models.trainer import auto_retrain_models
from utils.dashboard import print_dashboard

# Estados compartidos en memoria (Zero Latency Lookup)
active_markets = {}        # key (asset_window) -> market_data dict
shared_odds_state = {}     # market_id -> odds (MarketOdds object)
traded_markets = set()     # market_id operados. Aplica regla: "1 apuesta máxima simultánea/histórica por mercado"
poly_feeds_cache = {}      # market_id -> PolymarketFeed instance (Persiste la conexión y caché)
latest_logs = deque(maxlen=6) # Historial de logs para el dashboard

async def db_cleanup_task():
    """Limpia los snapshots y trades más antiguos de 7 días."""
    while True:
        try:
            await db.cleanup_old_data(days=7)
        except Exception as e:
            logger.error(f"Error en limpieza BD: {e}")
        await asyncio.sleep(86400) # Una vez al día

# ====================================================================
# TAREAS DE INGESTA DE DATOS (Productores)
# ====================================================================

async def book_worker(asset, bq, agg, collector):
    """Procesa actualizaciones de Order Book de Binance tan rápido como llegan."""
    while True:
        try:
            snapshot = await bq.get() # Bloquea de forma eficiente hasta que haya datos
            agg.process_orderbook(snapshot)
            # Enviar a colector en background sin bloquear la actualización de señales en memoria
            asyncio.create_task(collector.collect_order_book(snapshot))
            bq.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error en book_worker ({asset}): {e}")

async def trade_worker(asset, tq, agg, collector):
    """Procesa trades (CVD) de Binance."""
    while True:
        try:
            trade = await tq.get()
            agg.process_trade(trade)
            asyncio.create_task(collector.collect_trade(trade))
            tq.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error en trade_worker ({asset}): {e}")

# ====================================================================
# TAREAS DE ESTADO DE MERCADO (Polymarket)
# ====================================================================

async def discovery_worker(assets, windows, discovery):
    """Busca nuevos mercados cada 30 segundos y mantiene actualizado el registro."""
    while True:
        try:
            for asset in assets:
                for w in windows:
                    key = f"{asset}_{w}"
                    market_data = await discovery.get_active_market(asset, w)
                    
                    if market_data:
                        market_id = market_data["market_id"]
                        # Verificamos si es un slug de mercado completamente nuevo para la ventana
                        if key not in active_markets or active_markets[key]["slug"] != market_data["slug"]:
                            logger.info(f"[Discovery] Nuevo mercado rastreado: {market_data['slug']}")
                            active_markets[key] = market_data
                            
            await asyncio.sleep(30.0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error en discovery_worker: {e}")
            await asyncio.sleep(5.0)

async def fetch_and_store_odds(market_id, feed):
    """Obtiene odds para un mercado usando su propia instancia en caché."""
    try:
        odds = await feed.get_market_odds()
        if odds:
            shared_odds_state[market_id] = odds
    except Exception as e:
        # Ignoramos errores de red momentáneos o Rate Limits para que no crasheen el pooler.
        # Logear en nivel debug para limpiar la consola.
        logger.debug(f"Error obteniendo odds para {market_id}: {e}")

async def odds_updater_worker():
    """Actualiza en paralelo las cuotas de todos los mercados activos sin bloquear el HFT."""
    while True:
        try:
            tasks = []
            markets_to_fetch = list(active_markets.values())
            
            for market_data in markets_to_fetch:
                market_id = market_data["market_id"]
                
                # Si ya apostamos en este mercado, dejamos de estresar la API preguntando odds
                if market_id in traded_markets:
                    continue
                
                # Instanciamos y reusamos el feed para respetar su caché interno HTTP
                if market_id not in poly_feeds_cache:
                    poly_feeds_cache[market_id] = PolymarketFeed(
                        token_id_yes=market_data["token_id_yes"],
                        token_id_no=market_data["token_id_no"],
                        market_id=market_data["market_id"]
                    )
                
                feed = poly_feeds_cache[market_id]
                tasks.append(fetch_and_store_odds(market_id, feed))
            
            if tasks:
                await asyncio.gather(*tasks)
            
            # Pausa de 2 segundos. Evitamos sobrepasar el límite de peticiones (429 Rate Limit)
            await asyncio.sleep(2.0)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error general en odds_updater: {e}")
            await asyncio.sleep(5.0)

# ====================================================================
# MOTOR HFT CENTRAL (Consumidor de latencia microsegundo)
# ====================================================================

async def decision_engine_worker(assets, windows, aggregators, model, sizer, poly_client, risk_manager, paper_trader, is_paper_trading, models_ready_event):
    """Bucle principal de decisión. Reacciona a señales y odds cacheadas en memoria RAM pura."""
    logger.info("Esperando calibración inicial de modelos ML...")
    await models_ready_event.wait()
    
    logger.success("Signal Engine HFT iniciado. Latencia objetivo < 5ms por ciclo.")
    
    live_balance = 0.0
    if not is_paper_trading:
        try:
            live_balance = await poly_client.get_balance()
            logger.info(f"Balance inicial real (USDC): {live_balance:.2f}")
        except Exception as e:
            logger.error(f"No se pudo obtener balance inicial: {e}")

    last_log_time = {}

    while True:
        try:
            import time
            now_time = time.time()
            if now_time - last_log_time.get('diagnostics', 0) > 10.0:
                last_log_time['diagnostics'] = now_time
                feat_ok = sum(1 for a, agg in aggregators.items() if agg.get_feature_vector())
                latest_logs.appendleft(f"Status HFT -> Mercados: {len(active_markets)} | Odds Cacheados: {len(shared_odds_state)} | Feeds Llenos: {feat_ok}/{len(aggregators)}")
            
            # Iteramos rápido usando solo diccionarios O(1) en RAM
            for key, market_data in list(active_markets.items()):
                market_id = market_data["market_id"]
                
                # Regla del negocio: Solo operamos 1 vez por mercado histórico
                if market_id in traded_markets:
                    continue
                
                # Verificar si el odds updater ya trajo datos para este mercado
                if market_id not in shared_odds_state:
                    continue
                
                odds = shared_odds_state[market_id]
                asset, window_str = key.split('_')
                w = int(window_str)
                
                # Señales instantáneas calculadas en background
                features = aggregators[asset].get_feature_vector()
                if not features:
                    continue
                
                # Predecir y dimensionar la apuesta
                p_up = model.predict_proba(features, asset, w)
                current_bankroll = paper_trader.balance if is_paper_trading else live_balance
                decision = sizer.evaluate_bet(p_model=p_up, yes_price=odds.yes_price, bankroll=current_bankroll)
                
                import time
                now_time = time.time()
                # Logear métricas cada 10 segundos para no saturar la consola
                if now_time - last_log_time.get(market_id, 0) > 10.0:
                    last_log_time[market_id] = now_time
                    
                    # FeatureVector es probablemente un dataclass o dict, manejamos ambos
                    if hasattr(features, "get"):
                        ofi_val = features.get("ofi_10s", 0)
                        vwap_val = features.get("vwap_dev_bps", 0)
                    else:
                        ofi_val = getattr(features, "ofi_10s", 0)
                        vwap_val = getattr(features, "vwap_dev_bps", 0)
                        
                    edge = p_up - odds.yes_price
                    log_msg = f"[{asset} {w}m] P_up: {p_up:.3f} | Mercado: {odds.yes_price:.3f} | Edge: {edge:.3f} | OFI_10s: {ofi_val:.2f} | VWAP: {vwap_val:.2f} | Operar: {decision.should_bet}"
                    logger.info(f"📊 {log_msg}")
                    latest_logs.appendleft(log_msg)
                
                if decision.should_bet:
                    if is_paper_trading:
                        success = paper_trader.place_bet(decision, odds, asset, w, market_data["slug"])
                        if success:
                            logger.success(f"¡Paper Orden [{asset} {w}m]! {decision.direction} | ${decision.amount_usdc:.2f}")
                            traded_markets.add(market_id)
                            
                            from utils.schemas import BetReceipt
                            receipt = BetReceipt(
                                tx_hash="paper_trade_simulated",
                                amount_usdc=decision.amount_usdc,
                                price_filled=odds.yes_price if decision.direction == "YES" else (1.0 - odds.yes_price),
                                status="filled",
                                timestamp_ms=int(time.time() * 1000)
                            )
                            # Guardado en background. Fire-and-forget.
                            asyncio.create_task(db.record_bet(
                                decision=decision, receipt=receipt, features=features,
                                market_id=odds.market_id, asset=asset, window_minutes=w
                            ))
                    else:
                        if risk_manager.can_place_order(decision):
                            # Ejecución en tiempo real hacia Polymarket
                            receipt = await poly_client.place_bet(decision, odds)
                            if receipt:
                                logger.success(f"¡Orden ejecutada [{asset} {w}m]! {decision.direction} | Monto: {decision.amount_usdc:.2f}")
                                risk_manager.record_new_position()
                                traded_markets.add(market_id)
                                
                                # Actualizar bankroll en background para el próximo ciclo
                                live_balance = await poly_client.get_balance()
                                
                                asyncio.create_task(db.record_bet(
                                    decision=decision, receipt=receipt, features=features,
                                    market_id=odds.market_id, asset=asset, window_minutes=w
                                ))

            # Pequeño respiro de 1 milisegundo para ceder el Event Loop sin introducir latencia notable
            await asyncio.sleep(0.001)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error en decision_engine HFT: {e}")
            await asyncio.sleep(1.0)


# ====================================================================
# PUNTO DE ENTRADA Y ORQUESTACIÓN
# ====================================================================

async def run_bot():
    """Inicializa todas las tareas concurrentes y orquesta la ejecución."""
    logger.remove()
    os.makedirs("logs", exist_ok=True)
    logger.add("logs/bot.log", mode="w", level=config.log_level)
    logger.info("Iniciando Polymarket OFI Bot (Arquitectura Asíncrona Concurrente HFT)")

    assets = ["btcusdt", "xrpusdt", "solusdt", "ethusdt"]
    windows = [5, 15, 60]

    # Iniciar DB y Collector
    await db.connect()
    collector = DataCollector()
    await collector.start()
    
    background_tasks = []
    background_tasks.append(asyncio.create_task(db_cleanup_task()))

    # Inicializar Agregadores y Colas
    aggregators = {asset: SignalAggregator(vwap_window_seconds=300) for asset in assets}
    book_queues = {asset: asyncio.Queue() for asset in assets}
    trade_queues = {asset: asyncio.Queue() for asset in assets}
    
    # Iniciar Productores (Feeds de WebSocket Binance y Consumers locales)
    feeds = []
    for asset in assets:
        book_feed = BinanceOrderBookFeed(asset, book_queues[asset])
        trade_feed = BinanceTradeFeed(asset, trade_queues[asset])
        
        asyncio.create_task(book_feed.connect())
        asyncio.create_task(trade_feed.connect())
        feeds.extend([book_feed, trade_feed])
        
        background_tasks.append(asyncio.create_task(book_worker(asset, book_queues[asset], aggregators[asset], collector)))
        background_tasks.append(asyncio.create_task(trade_worker(asset, trade_queues[asset], aggregators[asset], collector)))

    # Herramientas del Negocio
    model = ProbabilityModel()
    sizer = KellySizer()
    poly_client = PolymarketClient()
    risk_manager = RiskManager()
    discovery = MarketDiscovery()
    
    paper_trader = PaperTrader(initial_balance=40.48, slippage=0.01)
    models_ready_event = asyncio.Event()
    
    if config.paper_trading:
        background_tasks.append(asyncio.create_task(paper_trader.resolve_loop(db)))
        background_tasks.append(asyncio.create_task(print_dashboard(paper_trader, latest_logs)))
        background_tasks.append(asyncio.create_task(auto_retrain_models(models_ready_event)))
        logger.info("Modo Simulación: Dashboard interactivo y auto-entrenamiento activados.")

    # Iniciar Productores Secundarios (Polymarket APIs)
    background_tasks.append(asyncio.create_task(discovery_worker(assets, windows, discovery)))
    background_tasks.append(asyncio.create_task(odds_updater_worker()))
    
    # Iniciar Consumidor HFT Central
    decision_task = asyncio.create_task(
        decision_engine_worker(
            assets, windows, aggregators, model, sizer, poly_client, risk_manager, paper_trader, config.paper_trading, models_ready_event
        )
    )

    logger.success("Bot corriendo... Todos los workers asíncronos en línea.")

    try:
        await decision_task
    except asyncio.CancelledError:
        logger.info("Apagando el bot, recibida señal de cancelación...")
    finally:
        # Cierre ordenado
        for feed in feeds:
            feed.stop()
        await collector.stop()
        await db.close()
        for task in background_tasks:
            task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot detenido por el usuario.")
