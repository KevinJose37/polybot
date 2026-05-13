import asyncio
import os
import json
import time
import numpy as np
import pandas as pd
import joblib
from loguru import logger
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from data.database import db
from utils.schemas import OrderBookSnapshot, OrderBookLevel, TradeEvent
from signals.aggregator import SignalAggregator

def train_models_from_events(asset: str, windows: list[int], df_samples: pd.DataFrame):
    """
    Entrena los modelos para todas las ventanas dadas usando el dataset de features
    generado por el event replay.
    """
    if len(df_samples) < 50:
        logger.warning(f"[Trainer] Dataset final muy pequeño para {asset}. Abortando.")
        return False
        
    for w in windows:
        logger.info(f"[Trainer] Calibrando {asset} {w}m con {len(df_samples)} samples...")
        
        # 1. Target Labeling
        df = df_samples.copy()
        df['target_time'] = df['timestamp_ms'] + (w * 60 * 1000)
        
        df_future = df[['timestamp_ms', 'mid_price']].copy()
        df_future = df_future.rename(columns={'timestamp_ms': 'future_ts', 'mid_price': 'future_price'})
        
        df_merged = pd.merge_asof(
            df, df_future,
            left_on='target_time', right_on='future_ts',
            direction='forward',
            tolerance=120000 # 2 minutos de tolerancia
        )
        
        df_merged['future_price'] = df_merged['future_price'].fillna(df_merged['mid_price'])
        y = (df_merged['future_price'] > df_merged['mid_price']).astype(int).values
        
        if len(np.unique(y)) < 2:
            logger.warning(f"[Trainer] Única clase detectada en {asset} {w}m. Omitiendo.")
            continue
            
        # 2. Extract X (10 features)
        feature_cols = [f'f{i}' for i in range(10)]
        X = df_merged[feature_cols].values
        
        # 3. Fit pipeline
        pipeline = Pipeline([
            ('scaler', StandardScaler()),
            ('model', CalibratedClassifierCV(
                LogisticRegression(C=0.1, max_iter=1000, class_weight='balanced'),
                method='sigmoid', cv=3
            ))
        ])
        
        pipeline.fit(X, y)
        
        # 4. Save
        os.makedirs("models", exist_ok=True)
        model_path = f"models/ofi_model_{asset.lower()}_{w}m.pkl"
        joblib.dump(pipeline, model_path)
        logger.success(f"[Trainer] Modelo {asset} {w}m re-entrenado y guardado (10 features).")
        
    return True

async def replay_events_and_train(asset: str, windows: list[int], cutoff_ms: int):
    """
    Extrae eventos desde PostgreSQL, los reproduce usando SignalAggregator 
    y entrena los modelos.
    """
    logger.info(f"[Trainer] Extrayendo eventos históricos para {asset}...")
    
    async with db.pool.acquire() as conn:
        snapshots = await conn.fetch(
            "SELECT timestamp_ms, mid_price, bids_json, asks_json FROM orderbook_snapshots WHERE symbol = $1 AND timestamp_ms >= $2",
            asset.upper(), cutoff_ms
        )
        trades = await conn.fetch(
            "SELECT timestamp_ms, price, quantity, is_buyer_maker FROM trade_events WHERE symbol = $1 AND timestamp_ms >= $2",
            asset.upper(), cutoff_ms
        )
        
    logger.info(f"[Trainer] {asset}: {len(snapshots)} snapshots, {len(trades)} trades extraídos.")
    
    if len(snapshots) < 100:
        logger.warning(f"[Trainer] Datos insuficientes para {asset}.")
        return
        
    # Unificar y ordenar eventos cronológicamente (tipo 0 = snapshot, tipo 1 = trade)
    events = []
    for s in snapshots:
        events.append((s['timestamp_ms'], 0, s))
    for t in trades:
        events.append((t['timestamp_ms'], 1, t))
        
    events.sort(key=lambda x: x[0])
    
    # Event Replay
    aggregator = SignalAggregator()
    samples = []
    
    last_sample_time = 0
    last_snapshot_time = 0
    current_mid_price = 0.0
    
    logger.info(f"[Trainer] Ejecutando replay de {len(events)} eventos para {asset}...")
    
    for ts, ev_type, data in events:
        if ev_type == 0:
            # Optimizacion de CPU: Solo procesar 1 snapshot cada 500ms (descarta micro-ruido y acelera JSON parse x5)
            if ts - last_snapshot_time < 500:
                continue
            last_snapshot_time = ts
            current_mid_price = data['mid_price']
            
            try:
                bids_raw = json.loads(data['bids_json'])
                asks_raw = json.loads(data['asks_json'])
                
                bids = [OrderBookLevel(price=float(p), size=float(s)) for p, s in bids_raw]
                asks = [OrderBookLevel(price=float(p), size=float(s)) for p, s in asks_raw]
                
                snapshot = OrderBookSnapshot(
                    symbol=asset.upper(),
                    timestamp_ms=ts,
                    bids=bids,
                    asks=asks
                )
                aggregator.process_orderbook(snapshot)
            except Exception as e:
                # Tolerar datos sucios
                continue
            
        else:
            trade = TradeEvent(
                symbol=asset.upper(),
                timestamp_ms=ts,
                price=data['price'],
                quantity=data['quantity'],
                is_buyer_maker=data['is_buyer_maker']
            )
            aggregator.process_trade(trade)
            
        # Sample cada 1 segundo (1000 ms) para generar el dataset
        if ts - last_sample_time >= 1000:
            fv = aggregator.get_feature_vector()
            if fv:
                row = [ts, current_mid_price] + fv.to_numpy()
                samples.append(row)
                last_sample_time = ts
                
    if not samples:
        logger.warning(f"[Trainer] No se generaron samples para {asset}.")
        return
        
    columns = ['timestamp_ms', 'mid_price'] + [f'f{i}' for i in range(10)]
    df_samples = pd.DataFrame(samples, columns=columns)
    
    # Entrenar sincrónicamente en un thread
    await asyncio.to_thread(train_models_from_events, asset, windows, df_samples)

async def auto_retrain_models(models_ready_event: asyncio.Event = None):
    """Bucle infinito que extrae datos de DB y reentrena los modelos cada 10 min."""
    # Esperar 10 segundos antes de la primera pasada para asegurar que la BD esté lista
    await asyncio.sleep(10)
    
    while True:
        try:
            if not db.pool:
                await asyncio.sleep(5)
                continue
                
            assets = ["btcusdt", "xrpusdt", "solusdt", "ethusdt"]
            windows = [5, 15, 60]
            
            # Obtener datos de la última 1.5 horas para acelerar ejecución
            now_ms = int(time.time() * 1000)
            cutoff_ms = now_ms - int(1.5 * 3600 * 1000)
            
            for asset in assets:
                await replay_events_and_train(asset, windows, cutoff_ms)
                        
        except Exception as e:
            logger.exception(f"[Trainer] Error general: {e}")
            
        if models_ready_event and not models_ready_event.is_set():
            models_ready_event.set()
            
        # Esperar 10 minutos antes del próximo ciclo de entrenamiento
        await asyncio.sleep(600)

async def run_manual_update():
    """Punto de entrada manual para forzar un re-entrenamiento inicial."""
    await db.connect()
    assets = ["btcusdt", "xrpusdt", "solusdt", "ethusdt"]
    windows = [5, 15, 60]
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - int(0.25 * 3600 * 1000) # Últimos 15 mins para inicialización rápida
    
    for asset in assets:
        await replay_events_and_train(asset, windows, cutoff_ms)
        
    await db.close()

if __name__ == "__main__":
    logger.info("Iniciando actualización manual de modelos (Event Replay 100% Producción)...")
    asyncio.run(run_manual_update())
