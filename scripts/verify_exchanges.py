"""Verify exchange credentials without exposing secrets.

    python scripts/verify_exchanges.py            # both
    python scripts/verify_exchanges.py kraken
    python scripts/verify_exchanges.py coinbase
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

from app.config import settings  # noqa: E402


def _fp(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return "<empty>"
    return f"set (sha256 {hashlib.sha256(value.encode()).hexdigest()[:8]}, len {len(value)})"


def check_kraken() -> bool:
    from app.exchanges.kraken import KrakenClient

    print("== Kraken")
    print("  key    :", _fp(settings.kraken_api_key))
    print("  secret :", _fp(settings.kraken_api_secret))
    if not settings.kraken_api_key.strip():
        print("  -> no key configured")
        return False
    try:
        acct = KrakenClient().get_account()
    except Exception as exc:
        print(f"  -> AUTH FAIL: {exc}")
        return False
    bal = {k: v for k, v in acct["balances"].items()
           if abs(float(v)) > 1e-9}
    print(f"  -> AUTH OK. non-zero balances: {bal or '{}'}")
    return True


def check_coinbase() -> bool:
    from app.exchanges.coinbase import CoinbaseClient

    print("== Coinbase")
    print("  key name:", _fp(settings.coinbase_api_key_name))
    print("  priv key:", _fp(settings.coinbase_private_key))
    if not settings.coinbase_api_key_name.strip():
        print("  -> no key configured")
        return False
    try:
        acct = CoinbaseClient().get_account()
    except Exception as exc:
        print(f"  -> AUTH FAIL: {exc}")
        return False
    bal = {k: v for k, v in acct.get("balances", {}).items()
           if abs(float(v)) > 1e-9}
    print(f"  -> AUTH OK. non-zero balances: {bal or '{}'}")
    return True


if __name__ == "__main__":
    which = sys.argv[1].lower() if len(sys.argv) > 1 else "both"
    results = {}
    if which in ("both", "kraken"):
        results["kraken"] = check_kraken()
    if which in ("both", "coinbase"):
        results["coinbase"] = check_coinbase()
    print("== active exchange:", settings.active_exchange)
    print("== summary:", results)
