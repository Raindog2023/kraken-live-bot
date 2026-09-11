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
    ml_max_order_quote: float = 25.0
    ml_max_position_quote: float = 100.0
    ml_daily_loss_limit: float = 25.0
    paused: bool = False
    webhook_secret: str = "CHANGE_ME"

    kraken_api_key: str = ""
    kraken_api_secret: str = ""
    kraken_base_url: str = "https://api.kraken.com"
    kraken_pair: str = "XBTUSD"

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

    max_order_quote: float = 100.0
    request_timeout_seconds: int = 10

    autonomous_enabled: bool = False
    autonomous_product_id: str = "BTC-USD"
    autonomous_quote_amount: float = 25.0
    autonomous_min_confidence: int = 70
    autonomous_scan_seconds: int = 300
    autonomous_trade_cooldown_seconds: int = 900

    taker_fee_bps: float = 26.0
    slippage_bps: float = 2.0
    min_edge_multiple: float = 2.0
    stop_loss_pct: float = 1.5
    take_profit_pct: float = 3.0
    daily_loss_limit: float = 25.0
    trend_filter_enabled: bool = True
    trend_filter_days: int = 100

    # Scheduled accumulation: the one thing in scripts/portfolio_lab.py that
    # made money out of sample was owning BTC, so buying it on a clock is the
    # only mode here with evidence behind it. It never sells.
    accumulate_enabled: bool = False
    accumulate_product_id: str = "BTC-USD"
    accumulate_quote_amount: float = 25.0
    accumulate_interval_hours: float = 168.0


settings = Settings()
