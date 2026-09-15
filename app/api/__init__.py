"""API package — import submodules directly to avoid import cycles.

Use `from app.api.routes import register_routes` and
`from app.api.schemas import WebhookSignal`.
"""

__all__ = ["routes", "schemas"]
