from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "kraken-live-bot"
    environment: str = "production"

    live_trading: bool = False
    ml_enabled: bool = False
    ml_paper_mode: bool = True
    ml_kill_switch: bool = False
    ml_confidence_threshold: float = 0.60
    ml_model_path: str = "artifacts/ml_model.joblib"
    ml_max_order_quote: float = 200.0
    ml_max_position_quote: float = 500.0
    ml_daily_loss_limit: float = 50.0
    paused: bool = False
    webhook_secret: str = "CHANGE_ME"

    kraken_api_key: str = ""
    kraken_api_secret: str = ""
    kraken_base_url: str = "https://api.kraken.com"
    kraken_pair: str = "XBTUSD"

    # Coinbase Advanced Trade (CDP API key pair; ES256 JWT per request)
    coinbase_enabled: bool = False
    coinbase_api_key_name: str = ""
    coinbase_private_key: str = ""  # PEM EC private key
    coinbase_base_url: str = "https://api.coinbase.com"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_base_url: str = "https://api.anthropic.com"

    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com"

    perplexity_api_key: str = ""
    perplexity_model: str = "sonar"
    perplexity_base_url: str = "https://api.perplexity.ai"

    godmode_base_url: str = ""
    godmode_api_key: str = ""
    godmode_model: str = "openai/gpt-4.1-mini"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-4.1-mini"

    max_order_quote: float = 200.0
    request_timeout_seconds: int = 10

    # Runtime directories (named volumes in the Docker deployment).
    data_dir: str = "data"
    log_dir: str = "logs"
    model_dir: str = "models"

    # Stop-loss and take-profit settings
    stop_loss_percentage: float = 2.0  # 2% stop loss
    take_profit_percentage: float = 3.0  # 3% take profit
    trailing_stop_percentage: float = 1.5  # 1.5% trailing stop


settings = Settings()
