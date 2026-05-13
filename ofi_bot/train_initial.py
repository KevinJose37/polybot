import asyncio
import pandas as pd
from loguru import logger
from data.database import DatabaseManager
from models.trainer import train_model_sync

async def main():
    """Ejecuta un entrenamiento inicial masivo utilizando todo el historial de PostgreSQL."""
    logger.info("==================================================")
    logger.info("Iniciando entrenamiento inicial masivo (Batch Training)")
    logger.info("==================================================")
    
    db = DatabaseManager()
    await db.connect()
    
    if not db.pool:
        logger.error("No se pudo conectar a PostgreSQL. Saliendo.")
        return
        
    assets = ["btcusdt", "xrpusdt", "solusdt", "ethusdt"]
    windows = [5, 15, 60]
    
    try:
        async with db.pool.acquire() as conn:
            for asset in assets:
                logger.info(f"Extrayendo datos históricos para {asset.upper()}...")
                # Extraemos absolutamente todo el historial ordenado cronológicamente
                rows = await conn.fetch(
                    "SELECT timestamp_ms, mid_price, bids_json as bids, asks_json as asks FROM orderbook_snapshots WHERE symbol = $1 ORDER BY timestamp_ms ASC",
                    asset.upper()
                )
                
                if len(rows) < 100:
                    logger.warning(f"Muy pocos datos para {asset} ({len(rows)} filas). Saltando...")
                    continue
                    
                df = pd.DataFrame(rows, columns=['timestamp_ms', 'mid_price', 'bids', 'asks'])
                logger.info(f"Se extrajeron {len(df)} ticks para {asset}. Iniciando Feature Engineering...")
                
                for w in windows:
                    # Entrenamos de forma síncrona y secuencial (esto puede tomar unos segundos/minutos)
                    success = train_model_sync(asset, w, df)
                    if not success:
                        logger.warning(f"No se pudo calibrar el modelo {asset} {w}m.")
                        
    except Exception as e:
        logger.error(f"Error fatal durante el entrenamiento: {e}")
    finally:
        await db.close()
        logger.info("Entrenamiento finalizado. Los modelos .pkl han sido sobrescritos.")

if __name__ == "__main__":
    asyncio.run(main())
