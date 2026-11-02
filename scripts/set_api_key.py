#!/usr/bin/env python3
"""Set SHKeeper wallet API keys from a protected environment variable."""

import os
import sys

sys.path.insert(0, "/shkeeper.io")

api_key = os.environ.get("SHKEEPER_API_KEY")
if not api_key:
    raise SystemExit("SHKEEPER_API_KEY must be supplied through the protected runtime environment")

from shkeeper import create_app

app = create_app()
with app.app_context():
    from shkeeper import db
    from shkeeper.models import Wallet

    wallets = Wallet.query.all()
    if not wallets:
        print("No wallets are configured; no keys were changed")
        raise SystemExit(0)

    for wallet in wallets:
        wallet.apikey = api_key
    db.session.commit()
    print(f"Updated API authentication for {len(wallets)} wallet configuration(s)")
