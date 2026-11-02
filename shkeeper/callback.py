import click
import hashlib
import hmac
import json
import time
from shkeeper import requests

from flask import Blueprint, json
from flask import current_app as app

from shkeeper.modules.classes.crypto import Crypto
from shkeeper.models import *
from datetime import datetime, timedelta

bp = Blueprint("callback", __name__)

DEFAULT_CURRENCY = 'USD'


def _post_signed_callback(url, notification, apikey):
    timestamp = str(int(time.time()))
    body = json.dumps(notification, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = hmac.new(
        apikey.encode("utf-8"),
        timestamp.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return requests.post(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Shkeeper-Api-Key": apikey,
            "X-Shkeeper-Timestamp": timestamp,
            "X-Shkeeper-Signature": signature,
        },
        timeout=app.config.get("REQUESTS_NOTIFICATION_TIMEOUT"),
    )


def send_unconfirmed_notification(utx: UnconfirmedTransaction):
    app.logger.info("Processing unconfirmed SHKeeper payment callback")

    invoice_address = InvoiceAddress.query.filter_by(
        crypto=utx.crypto, addr=utx.addr
    ).first()
    invoice = Invoice.query.filter_by(id=invoice_address.invoice_id).first()
    crypto = Crypto.instances[utx.crypto]
    apikey = crypto.wallet.apikey

    notification = {
        "status": "unconfirmed",
        "external_id": invoice.external_id,
        "crypto": utx.crypto,
        "addr": utx.addr,
        "txid": utx.txid,
        "amount": format_decimal(utx.amount_crypto, precision=crypto.precision),
    }

    app.logger.info("Posting unconfirmed SHKeeper payment callback")
    try:
        r = _post_signed_callback(invoice.callback_url, notification, apikey)
    except Exception as e:
        app.logger.error("Unconfirmed SHKeeper callback failed (%s)", type(e).__name__)
        return False

    if r.status_code != 202:
        app.logger.warning("Unconfirmed SHKeeper callback returned HTTP %s", r.status_code)
        return False

    utx.callback_confirmed = True
    db.session.commit()
    app.logger.info("Unconfirmed SHKeeper callback accepted")

    return True


def send_notification(tx):
    app.logger.info("Processing SHKeeper payment callback")

    transactions = []
    for t in tx.invoice.transactions:
        amount_fiat_without_fee = t.rate.get_orig_amount(t.amount_fiat)
        transactions.append(
            {
                "txid": t.txid,
                "date": str(t.created_at),
                "amount_crypto": remove_exponent(t.amount_crypto),
                "amount_fiat": remove_exponent(t.amount_fiat),
                "amount_fiat_without_fee": remove_exponent(amount_fiat_without_fee),
                "fee_fiat": remove_exponent(t.amount_fiat - amount_fiat_without_fee),
                "trigger": tx.id == t.id,
                "crypto": t.crypto,
            }
        )

    notification = {
        "external_id": tx.invoice.external_id,
        "crypto": tx.invoice.crypto,
        "addr": tx.invoice.addr,
        "fiat": tx.invoice.fiat,
        "balance_fiat": remove_exponent(tx.invoice.balance_fiat),
        "balance_crypto": remove_exponent(tx.invoice.balance_crypto),
        "paid": tx.invoice.status in (InvoiceStatus.PAID, InvoiceStatus.OVERPAID),
        "status": tx.invoice.status.name,
        "transactions": transactions,
        "fee_percent": remove_exponent(tx.invoice.rate.fee),
        "fee_fixed": remove_exponent(tx.invoice.rate.fixed_fee),
        "fee_policy": (
            tx.invoice.rate.fee_policy.name
            if tx.invoice.rate.fee_policy
            else FeeCalculationPolicy.PERCENT_FEE.name
        ),
    }

    overpaid_fiat = tx.invoice.balance_fiat - (
        tx.invoice.amount_fiat * (tx.invoice.wallet.ulimit / 100)
    )
    notification["overpaid_fiat"] = (
        str(round(overpaid_fiat.normalize(), 2)) if overpaid_fiat > 0 else "0.00"
    )

    apikey = Crypto.instances[tx.crypto].wallet.apikey
    app.logger.info("Posting SHKeeper payment callback")
    try:
        r = _post_signed_callback(tx.invoice.callback_url, notification, apikey)
    except Exception as e:
        app.logger.error("SHKeeper payment callback failed (%s)", type(e).__name__)
        return False

    if r.status_code != 202:
        app.logger.warning("SHKeeper payment callback returned HTTP %s", r.status_code)
        return False

    tx.callback_confirmed = True
    db.session.commit()
    app.logger.info("SHKeeper payment callback accepted")
    return True


def list_unconfirmed():
    for tx in Transaction.query.filter_by(callback_confirmed=False):
        print(tx)
    else:
        print("No unconfirmed transactions found!")


def send_callbacks():
    for utx in UnconfirmedTransaction.query.filter_by(callback_confirmed=False):
        try:
            send_unconfirmed_notification(utx)
        except Exception as e:
            app.logger.error("Payment workflow operation failed")

    for tx in Transaction.query.filter_by(
        callback_confirmed=False, need_more_confirmations=False
    ):
        try:
            delay_until_date = tx.created_at + timedelta(
                seconds=app.config.get("NOTIFICATION_TASK_DELAY")
            )
            if datetime.now() > delay_until_date:
                app.logger.info("Payment workflow event")
                if tx.invoice.status == InvoiceStatus.OUTGOING:
                    tx.callback_confirmed = True
                    db.session.commit()
                else:
                    app.logger.info("Payment workflow event")
                    send_notification(tx)
            else:
                app.logger.info("Payment workflow event")
        except Exception as e:
            app.logger.error("Payment workflow operation failed")

def poll_unconfirmed_payouts():
    app.logger.info("poll_unconfirmed_payouts start")
    cutoff = datetime.utcnow() - timedelta(days=1)
    payouts = (
        Payout.query
        .filter(
            Payout.task_id.isnot(None),
            Payout.status == PayoutStatus.IN_PROGRESS,
            Payout.created_at >= cutoff
        )
        .all()
    )
    app.logger.info("Payment workflow event")
    for payout in payouts:
        app.logger.info("Payment workflow event")
        crypto = Crypto.instances.get(payout.crypto)
        if not crypto:
            continue
        all_confirmed = False
        tx_to_notify = None
        for tx in payout.transactions:
            if not tx or not getattr(tx, "txid", None):
                app.logger.warning("Payment workflow item skipped")
                continue
            try:
                app.logger.info("Payment workflow event")
                confirmations = crypto.get_confirmations_by_txid(tx.txid)
                app.logger.info("Payment workflow event")
            except Exception:
                continue
            if confirmations > int(app.config.get("MIN_CONFIRMATION_BLOCK_FOR_PAYOUT")):
                all_confirmed = True
                if not tx_to_notify:
                    tx_to_notify = tx
        if all_confirmed:
            app.logger.info("Payment workflow event")
            app.logger.info("Payment workflow event")
            payout.status = PayoutStatus.SUCCESS
            payout.success = "Yes"
            if payout.callback_url and tx_to_notify and app.config.get("ENABLE_PAYOUT_CALLBACK"):
                app.logger.info("Payment workflow event")
                notification = Notification(
                    txid=tx_to_notify.txid,
                    object_id=payout.id,
                    type='Payout',
                    crypto=payout.crypto,
                    amount_crypto=payout.amount,
                    callback_url=payout.callback_url,
                )
                db.session.add(notification)
    db.session.commit()

def send_payout_callback_notifier():
    max_retries = app.config.get("REQUESTS_NOTIFICATION_RETRIES", 10)
    now = datetime.utcnow()
    notifs = Notification.query.filter(
        Notification.retries < max_retries,
        Notification.callback_confirmed == False
    ).all()
    for notif in notifs:
        retries = notif.retries or 0
        delay_total = sum((i + 1) ** 2 for i in range(retries + 1))
        next_try_time = notif.created_at + timedelta(seconds=delay_total)
        if now < next_try_time:
            continue
        try:
            app.logger.info("Payment workflow event")
            success = send_payout_notification(notif)
            if not success:
                notif.retries = retries + 1
                db.session.commit()
                app.logger.info("Payment workflow event")
        except Exception:
            notif.retries = retries + 1
            db.session.commit()
            app.logger.error("Payment workflow operation failed")

def send_payout_notification(notif: Notification):
    payout = Payout.query.get(notif.object_id)
    if not payout:
        notif.message = "Payout not found"
        db.session.commit()
        return False

    tx = payout.transactions[0] if payout.transactions else None
    tx_hash = tx.txid if tx else None
    if not tx_hash:
        app.logger.info("Payment workflow event")
        return False
    rate = ExchangeRate.get(DEFAULT_CURRENCY, payout.crypto).get_rate()
    amount_fiat = payout.amount * rate
    payload = {
        "payout_id": payout.id,
        "external_id": payout.external_id,
        "tx_hash": tx_hash,
        "status": "SUCCESS",
        "amount": str(payout.amount),
        "crypto": payout.crypto,
        "amount_fiat": str(amount_fiat),
        "currency_fiat": DEFAULT_CURRENCY,
        "timestamp": payout.created_at.isoformat(),
    }

    retries = getattr(notif, "retries", 0)
    wait = (retries + 1) ** 2
    app.logger.info("Payment workflow event")

    try:
        r = requests.post(
            payout.callback_url,
            json=payload,
            timeout=app.config.get("REQUESTS_NOTIFICATION_TIMEOUT", 10),
        )
    except Exception as e:
        notif.message = f"Callback delivery failed ({type(e).__name__})"
        notif.retries = retries + 1
        db.session.commit()
        return False

    if r.status_code != 202:
        notif.message = f"{r.status_code} {r.reason}"
        notif.retries = retries + 1
        db.session.commit()
        return False

    # Success
    notif.callback_confirmed = True
    db.session.commit()
    app.logger.info("Payment workflow event")
    return True

def poll_all_pending_payouts():
    cutoff = datetime.utcnow() - timedelta(days=1)
    app.logger.info("Payment workflow event")
    pending_payouts = (
        Payout.query
        .filter(
            Payout.task_id.isnot(None),
            Payout.status == PayoutStatus.IN_PROGRESS,
            Payout.created_at >= cutoff
        )
        .all()
    )
    app.logger.info("Payment workflow event")
    for payout in pending_payouts:
        app.logger.info("Payment workflow event")
        crypto = Crypto.instances.get(payout.crypto)
        if not crypto:
            continue
        task_response = crypto.get_task(payout.task_id)
        status = task_response.get("status")
        app.logger.info("Payment workflow event")
        if status in ("SUCCESS", "ERROR", "FAILED", "FAILURE"):
            app.logger.info("Payment workflow event")
            app.logger.info("Payment workflow event")
            app.logger.info("Payment workflow event")
            Payout.update_from_task(task_response, payout.task_id)
    db.session.commit()

def update_confirmations():
    for tx in Transaction.query.filter_by(
        callback_confirmed=False, need_more_confirmations=True
    ):
        try:
            app.logger.info("Payment workflow event")
            if not tx.is_more_confirmations_needed():
                app.logger.info("Payment workflow event")
            else:
                app.logger.info("Payment workflow event")
        except Exception as e:
            app.logger.error("Payment workflow operation failed")


@bp.cli.command()
def list():
    """Shows list of transaction notifications to be sent"""
    list_unconfirmed()


@bp.cli.command()
def send():
    """Send transaction notification"""
    send_callbacks()


@bp.cli.command()
def update():
    """Update number of confirmation"""
    update_confirmations()


@bp.cli.command()
@click.option("-c", "--confirmations", default=1)
def add(confirmations):
    import time

    crypto = Crypto.instances["BTC"]
    invoice = Invoice.add(
        crypto,
        {
            "external_id": str(time.time()),
            "fiat": "USD",
            "amount": 1000,
            "callback_url": "http://localhost:5000/api/v1/wp_callback",
        },
    )
    tx = Transaction.add(
        crypto,
        {
            "txid": invoice.id * 100,
            "addr": invoice.addr,
            "amount": invoice.amount_crypto,
            "confirmations": confirmations,
        },
    )
