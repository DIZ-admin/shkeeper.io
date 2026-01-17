#!/usr/bin/env python3
"""
Set SHKeeper API key to match .env configuration
"""
import sys
import os
sys.path.insert(0, '/shkeeper.io')

from shkeeper import create_app

# Get API key from environment
API_KEY = os.environ.get('SHKEEPER_API_KEY', '36834cd9943fe981fff589878578c2d15a3fde6e1ea0b304074cef7012052e6c')

app = create_app()

with app.app_context():
    from shkeeper.models import Wallet
    from shkeeper import db

    # Update all wallets with the API key from .env
    wallets = Wallet.query.all()

    if not wallets:
        print("No wallets found - they will be created automatically")
        sys.exit(0)

    print(f"Setting API key: {API_KEY}")

    for wallet in wallets:
        wallet.apikey = API_KEY
        print(f"Updated {wallet.crypto} wallet API key")

    db.session.commit()
    print("✅ API keys updated successfully")

    # Verify
    print("\n=== Current Wallet API Keys ===")
    for w in Wallet.query.all():
        print(f"{w.crypto:10} | Enabled: {w.enabled} | API Key: {w.apikey[:20]}...")
