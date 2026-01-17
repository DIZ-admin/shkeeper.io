# SHKeeper Integration Summary

## Overview

This directory contains the SHKeeper cryptocurrency payment gateway integration for the Telegram Shop Bot.

**SHKeeper** is a self-hosted payment processor supporting multiple cryptocurrencies:
- Bitcoin (BTC)
- Litecoin (LTC)
- Dogecoin (DOGE)
- Monero (XMR)
- Ethereum (ETH)
- USDT, USDC, and more

## Quick Links

- **Integration Guide**: `INTEGRATION.md` - Complete setup and configuration
- **API Reference**: `../docs/SHKEEPER_API_REFERENCE.md` - API methods and webhook events
- **Testing Guide**: `../docs/SHKEEPER_TESTING.md` - How to test the integration
- **Deployment**: `../docs/DEPLOYMENT.md#shkeeper-cryptocurrency-gateway` - Production deployment

## Key Features

- **Automated Payment Detection**: Real-time blockchain monitoring
- **Automatic Confirmation**: No manual intervention needed
- **Webhook Notifications**: Secure HMAC-SHA256 signed callbacks
- **Multi-Currency Support**: BTC, LTC, DOGE, XMR, and more
- **Feature Flag**: Enable/disable via CLI for gradual rollout
- **Dual Bitcoin Mode**: Manual (btc_addresses.txt) + Auto (SHKeeper)

## Quick Start

1. **Generate secrets**:
   ```bash
   python -c "import secrets; print('SHKEEPER_SECRET_KEY=' + secrets.token_urlsafe(32))"
   python -c "import secrets; print('SHKEEPER_WEBHOOK_SECRET=' + secrets.token_hex(32))"
   ```

2. **Add to .env**:
   ```bash
   SHKEEPER_PORT=5001
   SHKEEPER_SECRET_KEY=<generated_secret>
   SHKEEPER_DEV_MODE=1
   SHKEEPER_WEBHOOK_SECRET=<generated_secret>
   SHKEEPER_API_URL=http://shkeeper:5000
   ```

3. **Start SHKeeper**:
   ```bash
   docker compose up -d shkeeper
   docker logs eshop_shkeeper -f
   ```

4. **Configure wallets**: http://localhost:5001 (admin/admin)

5. **Enable feature**:
   ```bash
   docker exec eshop_bot python bot_cli.py settings set shkeeper_enabled true
   ```

## Architecture

```
User (Telegram) → Bot → SHKeeper → Blockchain
                   ↓         ↓
              Order DB  SHKeeper DB
```

**Components**:
- Bot handles user interaction and order management
- SHKeeper generates payment addresses and monitors blockchains
- PostgreSQL stores both bot orders and SHKeeper invoices
- Webhooks notify bot when payments are detected/confirmed

## Implementation Files

### Bot Integration

- `bot/payments/shkeeper_client.py` - SHKeeper API client
- `bot/handlers/webhooks/shkeeper_webhook.py` - Payment webhook handler
- `bot/handlers/user/order_handler.py:750-1100` - Checkout flow
- `bot/database/models/main.py:395-401` - Order crypto fields

### SHKeeper Container

- `Dockerfile.postgres` - SHKeeper container with PostgreSQL support
- `docker-compose.yml` - Service definition (eshop_shkeeper)
- Database: Separate `shkeeper` database in shared PostgreSQL instance

## Order Fields

When a user pays with cryptocurrency via SHKeeper, the Order record includes:

```python
shkeeper_invoice_id     # Invoice ID from SHKeeper
crypto_currency          # BTC, LTC, DOGE, XMR
crypto_amount            # Amount in crypto (Decimal 20,8)
crypto_address           # Payment address
crypto_tx_hash           # Transaction hash (after payment)
crypto_confirmations     # Blockchain confirmations
```

**Important**: These fields are ONLY used for SHKeeper payments. Manual Bitcoin uses `order.bitcoin_address`.

## Payment Flow

1. User selects cryptocurrency (BTC, LTC, DOGE, XMR)
2. Bot calls `SHKeeperClient.create_invoice()`
3. SHKeeper generates unique payment address
4. Bot shows QR code and address to user
5. User sends payment to address
6. SHKeeper detects payment → sends webhook to bot
7. Bot receives webhook, verifies signature, updates order
8. Bot sends notifications to user and admin

## Webhook Events

- **paid** - Payment detected in mempool (0 confirmations)
- **confirmed** - Payment confirmed in blockchain (3+ confirmations)
- **expired** - Invoice expired without payment
- **failed** - Payment failed (underpayment, etc.)

All webhooks are verified using HMAC-SHA256 signatures.

## Feature Flag

Enable/disable cryptocurrency payments without restarting:

```bash
# Enable
docker exec eshop_bot python bot_cli.py settings set shkeeper_enabled true

# Disable (fallback to manual Bitcoin only)
docker exec eshop_bot python bot_cli.py settings set shkeeper_enabled false
```

When disabled, users see only manual Bitcoin and Cash on Delivery options.

## Testing

```bash
# Unit tests
pytest tests/unit/payments/test_shkeeper_client.py -v

# Integration tests
pytest tests/integration/webhooks/test_shkeeper_webhook.py -v

# E2E tests
pytest tests/integration/test_crypto_checkout.py -v
```

See `docs/SHKEEPER_TESTING.md` for manual testing procedures.

## Security

- **Webhook Signature Verification**: HMAC-SHA256 using `SHKEEPER_WEBHOOK_SECRET`
- **Dev Mode Bypass**: In dev mode (`SHKEEPER_DEV_MODE=1`), signature verification is skipped
- **Production Mode**: Set `SHKEEPER_DEV_MODE=0` and configure strong secrets
- **Row-Level Locking**: Prevents race conditions with concurrent webhooks
- **Address Encryption**: Sensitive data encrypted at rest

## Monitoring

- **Dashboard**: http://localhost:9090/dashboard
- **Logs**: `docker logs eshop_shkeeper -f`
- **Health Check**: `curl http://localhost:5001/api/v1/crypto`
- **Metrics**: Prometheus metrics at `http://localhost:5001/metrics`

## Troubleshooting

### SHKeeper won't start
```bash
docker logs eshop_shkeeper
# Check: database initialized, env vars set, port available
```

### Webhook returns 401
```bash
# Verify SHKEEPER_WEBHOOK_SECRET is set and matches in .env
grep SHKEEPER_WEBHOOK_SECRET .env
```

### Payments not confirming
```bash
# Check wallet configuration in SHKeeper UI
# Verify blockchain connectivity
docker exec eshop_shkeeper curl -sf http://localhost:5000/api/v1/wallets
```

### Feature flag not working
```bash
# Verify setting
docker exec eshop_bot python bot_cli.py settings list | grep shkeeper_enabled

# Restart bot
docker compose restart bot
```

## Documentation Index

1. **INTEGRATION.md** - Complete integration guide with:
   - Architecture overview
   - Quick start setup
   - API endpoints
   - Configuration
   - Troubleshooting

2. **docs/SHKEEPER_API_REFERENCE.md** - API documentation with:
   - SHKeeperClient methods
   - Webhook event formats
   - Database schema
   - Error handling
   - Configuration

3. **docs/SHKEEPER_TESTING.md** - Testing guide with:
   - Unit tests
   - Integration tests
   - E2E tests
   - Manual testing procedures
   - CI/CD integration

4. **docs/DEPLOYMENT.md** - Production deployment with:
   - Initial setup
   - Health checks
   - Monitoring
   - Security checklist
   - Troubleshooting

5. **CLAUDE.md** - Development guide with:
   - SHKeeper architecture
   - Key files
   - Feature flag usage
   - Common pitfalls

## Support

- **SHKeeper Documentation**: https://shkeeper.io/kb/
- **SHKeeper API**: https://shkeeper.io/api/
- **GitHub**: https://github.com/vsys-host/shkeeper.io
- **Demo**: https://demo.shkeeper.io (admin/admin)

## License

SHKeeper is open-source software. Check the SHKeeper repository for license details.
Bot integration code is subject to the main project license.
