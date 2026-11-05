"""Real HD derivation + PostgreSQL allocation/invoice proof, without wallet/network RPC.

Only the public BIP39 test vector below is used. No seed/key generation, real
secret files, payments, app scheduler, or external crypto services are involved.
Run in the native SHKeeper image:
    python -m unittest discover -s tests -p test_hd_wallet_persistence.py -v
Set the existing SQLALCHEMY_DATABASE_URI to an isolated PostgreSQL test database.
Each test owns a unique temporary schema and drops only that schema.
"""

import base64
import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from flask import Flask, g, has_app_context
from sqlalchemy import create_engine, event, text

from shkeeper import db
from shkeeper.models import (
    ExchangeRate,
    FeeCalculationPolicy,
    Invoice,
    InvoiceAddress,
    Setting,
    Wallet,
)
from shkeeper.modules.classes.crypto import Crypto
from shkeeper.modules.classes.hd_wallet import create_hd_wallet_provider

logging.disable(logging.CRITICAL)
TEST_VECTOR = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
TEST_ENCRYPTION_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode()
ROOT = Path(__file__).resolve().parents[1]


def configure_public_vector(directory):
    path = Path(directory) / "public-test-vector.enc"
    path.write_bytes(Fernet(TEST_ENCRYPTION_KEY.encode()).encrypt(TEST_VECTOR.encode()))
    os.environ["HD_WALLET_SEED_ENCRYPTED_FILE"] = str(path)
    os.environ["HD_WALLET_ENCRYPTION_KEY"] = TEST_ENCRYPTION_KEY
    os.environ.pop("HD_WALLET_ENCRYPTION_KEY_FILE", None)
    os.environ["BTC_NETWORK"] = "testnet"
    os.environ["LTC_NETWORK"] = "testnet"


def application(schema):
    app = Flask("hd-allocation-test")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=os.environ["SQLALCHEMY_DATABASE_URI"],
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={
            "connect_args": {"options": f"-csearch_path={schema}"}
        },
    )
    db.init_app(app)
    return app


def runtime_crypto(currency):
    # Actual production wrappers, lazy provider loading, seed decryption and allocator.
    if currency == "BTC":
        from shkeeper.modules.cryptos.btc_hdwallet import BtcHDWallet

        crypto = BtcHDWallet()
    else:
        from shkeeper.modules.cryptos.ltc_hdwallet import LtcHDWallet

        crypto = LtcHDWallet()
    crypto._wallet = Wallet
    Crypto.instances[currency] = crypto
    return crypto


def create_invoice(crypto, external_id):
    return Invoice.add(
        crypto,
        {
            "external_id": external_id,
            "fiat": "USD",
            "amount": "100",
            "callback_url": "http://invalid.test/callback",
        },
    )


def worker(schema, currency, prefix, count, mode="--worker"):
    app = application(schema)
    with app.app_context():
        if mode == "--peek":
            print(
                json.dumps(
                    {
                        "addresses": [],
                        "next_index": create_hd_wallet_provider(
                            currency
                        ).get_current_index(),
                    }
                ),
                flush=True,
            )
            return
        crypto = runtime_crypto(currency)
        if mode == "--api-worker":
            from shkeeper.api_v1 import bp

            app.register_blueprint(bp)
            addresses = []
            with app.test_client() as client:
                for i in range(count):
                    payload = {
                        "external_id": f"{prefix}-{i}",
                        "fiat": "USD",
                        "amount": "100",
                        "callback_url": "http://invalid.test/callback",
                    }
                    response = client.post(
                        f"/api/v1/{currency}/payment_request",
                        json=payload,
                        headers={"X-Shkeeper-Api-Key": "synthetic-hd-test-apikey"},
                    )
                    assert (
                        response.status_code == 200
                        and response.json["status"] == "success"
                    )
                    addresses.append(response.json["wallet"])
                # Repeating the same merchant invoice must retain its address without consuming another index.
                payload["external_id"] = f"{prefix}-0"
                repeated = client.post(
                    f"/api/v1/{currency}/payment_request",
                    json=payload,
                    headers={"X-Shkeeper-Api-Key": "synthetic-hd-test-apikey"},
                )
                assert (
                    repeated.status_code == 200 and repeated.json["status"] == "success"
                )
                assert repeated.json["wallet"] == addresses[0]
        else:
            addresses = [
                create_invoice(crypto, f"{prefix}-{i}").addr for i in range(count)
            ]
        next_index = crypto.hd_provider.get_current_index()
        print(
            json.dumps({"addresses": addresses, "next_index": next_index}), flush=True
        )


class OfflineDerivationTests(unittest.TestCase):
    def test_pure_derivation_without_flask_app_or_database(self):
        self.assertFalse(has_app_context())
        with tempfile.TemporaryDirectory() as directory:
            configure_public_vector(directory)
            addresses = {}
            for currency in ("BTC", "LTC"):
                provider = create_hd_wallet_provider(currency)
                first = provider._derive_address_at_index(0)
                self.assertEqual(first, provider.derive_address_at_index(0))
                self.assertNotEqual(first, provider._derive_address_at_index(1))
                addresses[currency] = first
            self.assertNotEqual(addresses["BTC"], addresses["LTC"])


@unittest.skipUnless(
    os.environ.get("SQLALCHEMY_DATABASE_URI", "").startswith("postgresql"),
    "Requires an explicitly selected isolated PostgreSQL database",
)
class PersistentAllocationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        configure_public_vector(self.directory.name)
        self.schema = "test_hd_" + uuid4().hex
        self.admin = create_engine(os.environ["SQLALCHEMY_DATABASE_URI"])
        with self.admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.app = application(self.schema)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        for currency in ("BTC", "LTC"):
            db.session.add(Wallet(crypto=currency, apikey="synthetic-hd-test-apikey"))
            db.session.add(
                ExchangeRate(
                    crypto=currency,
                    fiat="USD",
                    source="manual",
                    rate=Decimal("100000"),
                    fee_policy=FeeCalculationPolicy.NO_FEE,
                )
            )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        with self.admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin.dispose()
        self.directory.cleanup()

    def processes(self, currency, count, workers=1, mode="--worker"):
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    mode,
                    self.schema,
                    currency,
                    f"worker-{uuid4().hex}",
                    str(count),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                cwd=ROOT,
            )
            for _ in range(workers)
        ]
        results = []
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=90)
                self.assertEqual(process.returncode, 0, stderr[-2000:])
                results.append(json.loads(stdout.splitlines()[-1]))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        db.session.remove()
        return results

    def assert_restart(self, currency):
        first = self.processes(currency, 1)[0]
        second = self.processes(currency, 1)[0]
        self.assertNotEqual(first["addresses"][0], second["addresses"][0])
        self.assertEqual(first["next_index"], 1)
        self.assertEqual(second["next_index"], 2)
        provider = create_hd_wallet_provider(currency)
        self.assertEqual(provider.get_current_index(), 2)
        self.assertEqual(first["addresses"], [provider._derive_address_at_index(0)])
        self.assertEqual(second["addresses"], [provider._derive_address_at_index(1)])
        self.assertEqual(Invoice.query.filter_by(crypto=currency).count(), 2)
        self.assertEqual(InvoiceAddress.query.filter_by(crypto=currency).count(), 2)

    def test_btc_new_process_resumes_committed_cursor(self):
        self.assert_restart("BTC")

    def test_ltc_new_process_resumes_committed_cursor(self):
        self.assert_restart("LTC")

    def test_btc_and_ltc_parallel_workers_issue_unique_real_addresses(self):
        for currency in ("BTC", "LTC"):
            with self.subTest(currency=currency):
                result = self.processes(currency, count=4, workers=2)
                issued = [address for row in result for address in row["addresses"]]
                self.assertEqual(len(issued), 8)
                self.assertEqual(len(set(issued)), 8)
                provider = create_hd_wallet_provider(currency)
                self.assertEqual(provider.get_current_index(), 8)
                self.assertEqual(
                    set(issued),
                    {provider._derive_address_at_index(i) for i in range(8)},
                )
                self.assertEqual(
                    InvoiceAddress.query.filter_by(crypto=currency).count(), 8
                )

    def test_actual_api_invoices_zero_one_then_new_process_reads_two(self):
        for currency in ("BTC", "LTC"):
            with self.subTest(currency=currency):
                issued = self.processes(currency, 2, mode="--api-worker")[0]
                provider = create_hd_wallet_provider(currency)
                self.assertEqual(
                    issued["addresses"],
                    [
                        provider._derive_address_at_index(0),
                        provider._derive_address_at_index(1),
                    ],
                )
                self.assertEqual(issued["next_index"], 2)
                self.assertEqual(
                    self.processes(currency, 0, mode="--peek")[0]["next_index"], 2
                )
                self.assertEqual(Invoice.query.filter_by(crypto=currency).count(), 2)
                self.assertEqual(
                    InvoiceAddress.query.filter_by(crypto=currency).count(), 2
                )

    def test_invoice_failure_rolls_back_counter_invoice_and_address_together(self):
        crypto = runtime_crypto("BTC")

        def fail_on_address(session, context, instances):
            if any(isinstance(item, InvoiceAddress) for item in session.new):
                raise RuntimeError("Synthetic invoice address persistence failure")

        session = db.session()
        event.listen(session, "before_flush", fail_on_address)
        try:
            from shkeeper.api_v1 import payment_request

            with self.app.test_request_context(
                "/api/v1/BTC/payment_request",
                method="POST",
                json={
                    "external_id": "rollback",
                    "fiat": "USD",
                    "amount": "100",
                    "callback_url": "http://invalid.test/callback",
                },
                headers={"X-Shkeeper-Api-Key": "synthetic-hd-test-apikey"},
            ):
                response = payment_request(crypto_name="BTC")
                self.assertEqual(response["status"], "error")
                self.assertNotIn("wallet", response)
        finally:
            event.remove(session, "before_flush", fail_on_address)
            db.session.rollback()
        db.session.remove()
        self.assertEqual(Invoice.query.count(), 0)
        self.assertEqual(InvoiceAddress.query.count(), 0)
        self.assertEqual(Setting.query.count(), 0)

        invoice = create_invoice(crypto, "rollback")
        self.assertEqual(Invoice.query.filter_by(external_id="rollback").count(), 1)
        self.assertEqual(invoice.addr, crypto.hd_provider._derive_address_at_index(0))
        self.assertEqual(crypto.hd_provider.get_current_index(), 1)

    def test_standalone_generate_address_commits_before_return(self):
        from shkeeper.api_v1 import generate_address

        crypto = runtime_crypto("BTC")
        with self.app.test_request_context("/api/v1/BTC/generate-address"):
            # Test the authenticated view's allocation/commit boundary, not login middleware.
            g.user = object()
            result = generate_address(crypto_name="BTC")
            self.assertEqual(result["status"], "success")
            first = result["addr"]
        db.session.remove()
        second = self.processes("BTC", 1)[0]
        self.assertNotEqual(first, second["addresses"][0])
        self.assertEqual(second["next_index"], 2)
        self.assertEqual(Invoice.query.count(), 1)

    def test_initialization_skips_legacy_invoice_and_address_records(self):
        provider = create_hd_wallet_provider("BTC")
        legacy = Invoice(
            crypto="BTC",
            addr=provider._derive_address_at_index(0),
            external_id="legacy",
        )
        db.session.add(legacy)
        db.session.flush()
        db.session.add(
            InvoiceAddress(
                invoice_id=legacy.id,
                crypto="BTC",
                addr=provider._derive_address_at_index(2),
            )
        )
        db.session.commit()
        result = self.processes("BTC", 2)[0]
        self.assertEqual(
            result["addresses"],
            [
                provider._derive_address_at_index(1),
                provider._derive_address_at_index(3),
            ],
        )
        self.assertEqual(result["next_index"], 4)

    def test_networks_use_separate_durable_cursors(self):
        testnet = runtime_crypto("BTC")
        testnet_address = testnet.mkaddr()
        db.session.commit()
        os.environ["BTC_NETWORK"] = "mainnet"
        try:
            mainnet = runtime_crypto("BTC")
            mainnet_address = mainnet.mkaddr()
            db.session.commit()
        finally:
            os.environ["BTC_NETWORK"] = "testnet"
        self.assertNotEqual(testnet_address, mainnet_address)
        self.assertEqual(testnet.hd_provider.get_current_index(), 1)
        self.assertEqual(mainnet.hd_provider.get_current_index(), 1)
        self.assertEqual(Setting.query.count(), 2)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in {"--worker", "--api-worker", "--peek"}:
        worker(
            sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), mode=sys.argv[1]
        )
    else:
        unittest.main()
