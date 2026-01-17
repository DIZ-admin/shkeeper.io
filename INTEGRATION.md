# SHKeeper Integration Guide

## Overview

SHKeeper is integrated as a self-hosted cryptocurrency payment gateway for the Telegram Shop Bot. It supports multiple cryptocurrencies including BTC, ETH, LTC, DOGE, XMR, USDT, USDC and more.

## Architecture

```
User (Telegram) → Bot (eshop_bot)
                     ↓ REST API
              SHKeeper (eshop_shkeeper)
                     ↓ PostgreSQL
            Shared Database (eshop_db)
                     ↓
            Blockchain Networks (BTC, LTC, DOGE, XMR)
```

**Components:**
- **Telegram Bot**: User interface, order management, webhook receiver
- **SHKeeper**: Payment gateway, invoice management, blockchain monitoring
- **PostgreSQL**: Two databases in one instance:
  - `telegram_shop`: Bot's main database with Order table
  - `shkeeper`: SHKeeper's internal database for wallets and invoices
- **Blockchain Networks**: External networks for cryptocurrency transactions

## Quick Start

### 1. Configure Environment Variables

Copy `.env.example` to `.env` and set SHKeeper variables:

```bash
# Generate secret keys
python -c "import secrets; print('SHKEEPER_SECRET_KEY=' + secrets.token_urlsafe(32))"
python -c "import secrets; print('SHKEEPER_WEBHOOK_SECRET=' + secrets.token_hex(32))"
```

Add to `.env`:
```bash
SHKEEPER_PORT=5000
SHKEEPER_SECRET_KEY=<generated_secret>
SHKEEPER_DEV_MODE=1
SHKEEPER_WEBHOOK_SECRET=<generated_secret>
```

### 2. Initialize SHKeeper Database

```bash
# Start PostgreSQL container
docker compose up -d db

# Wait for database to be ready
docker compose exec db pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}

# Create SHKeeper database (if not already created)
docker compose exec db psql -U ${POSTGRES_USER} -d postgres -c "CREATE DATABASE shkeeper OWNER ${POSTGRES_USER};"

# Verify database was created
docker compose exec db psql -U ${POSTGRES_USER} -c "\l" | grep shkeeper
```

### 3. Build and Start SHKeeper

```bash
# Build SHKeeper container
docker compose build shkeeper

# Start SHKeeper
docker compose up -d shkeeper

# Check logs
docker compose logs -f shkeeper
```

### 4. Access SHKeeper

- **API**: http://localhost:5001/api/v1/crypto (port 5001 by default, see .env)
- **Web Interface**: http://localhost:5001
- **Default Credentials**: admin/admin (change in production!)
- **Webhook Endpoint**: http://localhost:9090/webhooks/shkeeper (bot receives notifications here)

## API Endpoints Used by Bot

### Get Supported Cryptocurrencies
```bash
curl http://localhost:5001/api/v1/crypto
```

Response:
```json
{
  "crypto": [
    {"name": "Bitcoin", "ticker": "BTC", "enabled": true},
    {"name": "Litecoin", "ticker": "LTC", "enabled": true},
    {"name": "Dogecoin", "ticker": "DOGE", "enabled": true},
    {"name": "Monero", "ticker": "XMR", "enabled": true}
  ],
  "status": "success"
}
```

### Create Invoice
```bash
curl -X POST http://localhost:5001/api/v1/invoice \
  -H "X-Shkeeper-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "ORDER-ABC123",
    "fiat": "USD",
    "amount": "100.00",
    "crypto": "BTC",
    "callback_url": "http://bot:9090/webhooks/shkeeper"
  }'
```

Response:
```json
{
  "invoice_id": "inv_abc123",
  "crypto_address": "bc1q...",
  "crypto_amount": "0.00123456",
  "crypto_currency": "BTC",
  "status": "pending",
  "expires_at": "2026-01-19T12:00:00Z"
}
```

### Get Invoice Status
```bash
curl http://localhost:5001/api/v1/invoice/inv_abc123 \
  -H "X-Shkeeper-API-Key: your_api_key"
```

Response:
```json
{
  "invoice_id": "inv_abc123",
  "external_id": "ORDER-ABC123",
  "status": "paid",
  "crypto_currency": "BTC",
  "crypto_amount": "0.00123456",
  "tx_hash": "abc123...",
  "confirmations": 3
}
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SHKEEPER_PORT` | API port | 5000 |
| `SHKEEPER_SECRET_KEY` | Flask secret key | Required |
| `SHKEEPER_DEV_MODE` | Development mode | 1 |
| `SHKEEPER_API_KEY` | API key | Auto-generated |
| `SHKEEPER_WEBHOOK_SECRET` | Webhook HMAC secret | Required |
| `SQLALCHEMY_DATABASE_URI` | PostgreSQL connection | Auto-configured |

### Database

SHKeeper uses a separate PostgreSQL database named `shkeeper` in the same PostgreSQL instance as the bot database.

**Connection String**: `postgresql://eshop_user:password@db:5432/shkeeper`

## Integration with Bot

### 1. SHKeeper Client

The bot uses `bot/payments/shkeeper_client.py` to interact with SHKeeper API:

```python
from bot.payments.shkeeper_client import SHKeeperClient

client = SHKeeperClient(
    api_url="http://shkeeper:5000",
    api_key=EnvKeys.SHKEEPER_API_KEY
)

# Create invoice
invoice = await client.create_invoice(
    amount=Decimal("0.001"),
    currency="BTC",
    order_id="ABC123",
    callback_url="http://bot:9090/webhooks/shkeeper"
)
```

### 2. Webhook Handler

SHKeeper sends payment notifications to: `POST http://bot:9090/webhooks/shkeeper`

Handler location: `bot/handlers/webhooks/shkeeper_webhook.py`

**Webhook Events:**
- `paid` - Payment received in mempool (0 confirmations)
- `confirmed` - Payment confirmed in blockchain (order status → confirmed)
- `expired` - Invoice expired without payment (inventory released)
- `failed` - Payment failed (underpayment, etc., inventory released)

**Security:**
- Webhook signature verification via HMAC-SHA256
- Uses `SHKEEPER_WEBHOOK_SECRET` from .env
- Signature sent in `X-SHKeeper-Signature` header (format: "sha256=<hex>")
- In dev mode (`SHKEEPER_DEV_MODE=1`), signature verification is skipped

### 3. Database Integration

Order model has been updated with SHKeeper fields:

```python
shkeeper_invoice_id      # Invoice ID from SHKeeper
crypto_currency          # BTC, LTC, DOGE, etc.
crypto_amount            # Payment amount in crypto
crypto_address           # Payment address
crypto_tx_hash           # Transaction hash
crypto_confirmations     # Blockchain confirmations
```

## Wallet Setup

### Bitcoin Testnet Wallet

For development/testing, configure Bitcoin testnet wallet:

1. Access SHKeeper admin panel: http://localhost:5000
2. Login with admin/admin
3. Navigate to Wallets → Add Wallet
4. Select "Bitcoin Testnet"
5. Configure wallet parameters:
   - **Type**: HD Wallet or Watch-only
   - **Extended Public Key (xpub)**: Your testnet xpub
   - **Derivation Path**: m/84'/1'/0'/0 (for testnet)

### Get Testnet Bitcoin

Use Bitcoin testnet faucets:
- https://testnet-faucet.mempool.co/
- https://bitcoinfaucet.uo1.net/
- https://coinfaucet.eu/en/btc-testnet/

## Troubleshooting

### SHKeeper container fails to start

```bash
# Check logs
docker compose logs shkeeper

# Common issues:
# 1. Database not initialized
docker compose exec db psql -U eshop_user -c "CREATE DATABASE shkeeper;"

# 2. Missing environment variables
cat .env | grep SHKEEPER

# 3. Port conflict
netstat -an | grep 5000
```

### Database connection errors

```bash
# Test PostgreSQL connection
docker compose exec shkeeper psql $SQLALCHEMY_DATABASE_URI -c "SELECT version();"

# Check database exists
docker compose exec db psql -U eshop_user -c "\l" | grep shkeeper
```

### API returns 401 Unauthorized

Check API key configuration:
1. SHKeeper generates API key on first run
2. Copy from SHKeeper logs or web interface
3. Add to .env: `SHKEEPER_API_KEY=<key>`
4. Restart bot: `docker compose restart bot`

## Checkout Flow Integration

When user selects cryptocurrency payment:

1. **Payment Method Selection**: User sees "Bitcoin (Auto)", "Litecoin", etc. (if `shkeeper_enabled=true`)
2. **Cryptocurrency Selection**: User selects specific crypto (BTC, LTC, DOGE, XMR)
3. **Invoice Creation**: Bot calls `SHKeeperClient.create_invoice()` with:
   - Order total in fiat (USD/RUB)
   - Selected cryptocurrency
   - Order code as `external_id`
   - Webhook URL: `http://bot:9090/webhooks/shkeeper`
4. **Payment Display**: Bot shows QR code and payment address to user
5. **Order Creation**: Bot creates Order record with:
   - `shkeeper_invoice_id` = invoice ID
   - `crypto_currency` = BTC/LTC/DOGE/XMR
   - `crypto_amount` = amount in crypto (Decimal 20,8)
   - `crypto_address` = payment address
   - `order_status` = "reserved"
   - `reserved_until` = 7 days from now (longer timeout for crypto)
6. **Inventory Reservation**: Bot reserves inventory for 7 days (crypto payment timeout)
7. **Webhook Processing**: When payment arrives, SHKeeper sends webhook:
   - `paid` → Update tx_hash and confirmations
   - `confirmed` → Update order_status to "confirmed", send notifications
   - `expired` → Release inventory, mark order as expired
   - `failed` → Release inventory, mark order as cancelled

## Feature Flag System

The bot supports gradual rollout via `shkeeper_enabled` setting:

```bash
# Enable cryptocurrency payments
python bot_cli.py settings set shkeeper_enabled true

# Disable (fallback to manual Bitcoin only)
python bot_cli.py settings set shkeeper_enabled false
```

**When enabled** (`shkeeper_enabled=true`):
- Payment options: Bitcoin (Manual), Bitcoin (Auto), Litecoin, Dogecoin, Monero, Cash on Delivery

**When disabled** (`shkeeper_enabled=false`):
- Payment options: Bitcoin (Manual), Cash on Delivery
- Manual Bitcoin uses existing `btc_addresses.txt` system

**Implementation:**
- Handler: `bot/handlers/user/order_handler.py:690-777`
- Settings check: `get_bot_settings(session).shkeeper_enabled`

## Production Deployment

### Security Checklist

- [ ] Change default SHKeeper admin password (http://localhost:5001)
- [ ] Set `SHKEEPER_DEV_MODE=0` in .env
- [ ] Generate strong `SHKEEPER_SECRET_KEY` (32+ bytes)
- [ ] Generate strong `SHKEEPER_WEBHOOK_SECRET` (32+ bytes)
- [ ] Enable HTTPS for webhook callbacks (if SHKeeper is external)
- [ ] Configure wallet encryption in SHKeeper UI
- [ ] Backup wallet private keys securely (hardware wallet recommended)
- [ ] Set up monitoring and alerting (see Monitoring section)
- [ ] Configure rate limiting in SHKeeper
- [ ] Enable audit logging in SHKeeper
- [ ] Test with small amounts first
- [ ] Enable feature flag gradually: start with limited users

### Monitoring

SHKeeper exposes Prometheus metrics at `/metrics`:

```bash
curl http://localhost:5000/metrics
```

Integrate with existing monitoring dashboard at port 9090.

## References

- **SHKeeper Documentation**: https://shkeeper.io/kb/
- **SHKeeper API**: https://shkeeper.io/api/
- **GitHub Repository**: https://github.com/vsys-host/shkeeper.io
- **Demo**: https://demo.shkeeper.io (admin/admin)
