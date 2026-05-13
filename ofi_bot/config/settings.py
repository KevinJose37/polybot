from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """
    Configuración central del bot HFT OFI.
    Lee automáticamente las variables desde el archivo .env o del entorno.
    """
    # Credenciales de Polymarket
    private_key: str = ""
    funder_address: str = ""
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""

    # Conexiones
    binance_ws_url: str = "wss://stream.binance.com:9443/ws"
    
    # Base de Datos PostgreSQL
    # Formato: postgresql://user:password@host:port/dbname
    database_url: str = "postgresql://postgres:postgres@localhost:5432/ofi_bot"

    # Parámetros de Trading / Riesgo
    paper_trading: bool = True
    min_edge: float = 0.04
    kelly_fraction: float = 0.25
    max_bet_pct: float = 0.05
    max_bet_usdc: float = 50.0
    max_open_positions: int = 15
    daily_drawdown_limit: float = 0.10

    # Mercados activos (CSV)
    active_markets: list[str] = ["BTC-5MIN", "SOL-15MIN"]

    # Logs
    log_level: str = "INFO"
    log_file: str = "bot.log"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

# Instancia global de configuración
config = Settings()
