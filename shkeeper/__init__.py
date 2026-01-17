import functools
import os
import logging
import secrets
from decimal import Decimal
import shutil
import threading

from flask import logging as flog, render_template, request

flog.default_handler.setFormatter(
    logging.Formatter(
        "%(levelname)s %(filename)s:%(lineno)s %(funcName)s(): %(message)s"
    )
)

from flask import Flask
import requests

from shkeeper.wallet_encryption import WalletEncryptionRuntimeStatus

from .utils import format_decimal, load_secret, read_env_bool
from .events import shkeeper_initialized

from flask_apscheduler import APScheduler

scheduler = APScheduler()

from sqlalchemy import MetaData
import flask_sqlalchemy

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=convention)
db = flask_sqlalchemy.SQLAlchemy(metadata=metadata)

import flask_migrate

migrate = flask_migrate.Migrate()


def _build_sqlalchemy_database_uri() -> str | None:
    explicit_uri = os.environ.get("SQLALCHEMY_DATABASE_URI")
    if explicit_uri:
        return explicit_uri

    db_user = os.environ.get("DB_USER") or os.environ.get("POSTGRES_USER")
    db_password = load_secret("DB_PASSWORD", "DB_PASSWORD_FILE") or load_secret(
        "POSTGRES_PASSWORD", "POSTGRES_PASSWORD_FILE"
    )
    db_host = os.environ.get("DB_HOST", "db")
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = os.environ.get("DB_NAME") or os.environ.get("POSTGRES_DB") or "shkeeper"

    if not (db_user and db_password and db_host and db_name):
        return None

    return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"


def internal_server_error(e):
    return render_template("500.j2", theme=request.cookies.get("theme", "light")), 500


def page_not_found_error(e):
    return render_template("404.j2", theme=request.cookies.get("theme", "light")), 404


def create_app(test_config=None):
    """Create and configure an instance of the Flask application."""
    app = Flask(__name__, instance_relative_config=True)
    secret_key = load_secret("SECRET_KEY", "SECRET_KEY_FILE")
    if not secret_key:
        secret_key = load_secret("SHKEEPER_SECRET_KEY", "SHKEEPER_SECRET_KEY_FILE")

    database_uri = _build_sqlalchemy_database_uri()
    app.config.from_mapping(
        # a default secret that should be overridden by instance config
        SECRET_KEY=secret_key or "dev",
        # store the database in the instance folder
        DATABASE=os.path.join(app.instance_path, "shkeeper.sqlite"),
        SQLALCHEMY_DATABASE_URI=database_uri
        or "sqlite:///" + os.path.join(app.instance_path, "shkeeper.sqlite"),
        SUGGESTED_WALLET_APIKEY=secrets.token_urlsafe(16),
        SESSION_TYPE="filesystem",
        SESSION_FILE_DIR=os.path.join(app.instance_path, "flask_session"),
        TRON_MULTISERVER_GUI=read_env_bool("TRON_MULTISERVER_GUI"),
        TRON_STAKING_GUI=read_env_bool("TRON_STAKING_GUI"),
        FORCE_WALLET_ENCRYPTION=read_env_bool("FORCE_WALLET_ENCRYPTION"),
        UNCONFIRMED_TX_NOTIFICATION=read_env_bool("UNCONFIRMED_TX_NOTIFICATION"),
        REQUESTS_TIMEOUT=int(os.environ.get("REQUESTS_TIMEOUT", 10)),
        REQUESTS_NOTIFICATION_RETRIES=int(os.environ.get("MAX_RETRIES", 7)),
        REQUESTS_NOTIFICATION_TIMEOUT=int(
            os.environ.get("REQUESTS_NOTIFICATION_TIMEOUT", 30)
        ),
        DEV_MODE=read_env_bool("DEV_MODE", default=False),
        DEV_MODE_ENC_PW=os.environ.get("DEV_MODE_ENC_PW"),
        ENABLE_PAYOUT_CALLBACK=read_env_bool("ENABLE_PAYOUT_CALLBACK"),
        MIN_CONFIRMATION_BLOCK_FOR_PAYOUT=os.environ.get("MIN_CONFIRMATION_BLOCK_FOR_PAYOUT", 1),
        NOTIFICATION_TASK_DELAY=int(os.environ.get("NOTIFICATION_TASK_DELAY", 60)),
        TEMPLATES_AUTO_RELOAD=True,
        DISABLE_CRYPTO_WHEN_LAGS=read_env_bool("DISABLE_CRYPTO_WHEN_LAGS", default=False),
    )

    if test_config is None:
        # load the instance config, if it exists, when not testing
        app.config.from_pyfile("config.py", silent=True)
    else:
        # load the test config if passed in
        app.config.update(test_config)

    # ensure the instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    # clear all session on app restart
    if sess_dir := app.config.get("SESSION_FILE_DIR"):
        if app.config.get("DEV_MODE"):
            pass
        else:
            shutil.rmtree(sess_dir, ignore_errors=True)
    from flask_session import Session

    Session(app)

    scheduler.init_app(app)

    if app.debug or app.config.get("DEV_MODE"):
        logging.getLogger("apscheduler").setLevel(logging.DEBUG)
        app.logger.setLevel(logging.DEBUG)
    else:
        logging.getLogger("apscheduler").setLevel(logging.INFO)
        app.logger.setLevel(logging.INFO)

    app.logger.propagate = False

    from flask.json import JSONDecoder, JSONEncoder
    from decimal import Decimal

    class ShkeeperJSONDecoder(JSONDecoder):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, parse_float=Decimal, **kwargs)

    class ShkeeperJSONEncoder(JSONEncoder):
        def default(self, obj):
            if isinstance(obj, Decimal):
                return str(obj)
            return super().default(self, obj)

    app.json_decoder = ShkeeperJSONDecoder
    app.json_encoder = ShkeeperJSONEncoder

    for method in ("get", "options", "head", "post", "put", "patch", "delete"):
        setattr(
            requests,
            method,
            functools.partial(
                getattr(requests, method), timeout=app.config.get("REQUESTS_TIMEOUT")
            ),
        )

    db.init_app(app)
    migrate.init_app(app, db)
    with app.app_context():
        # Create tables according to models
        from .models import (
            Wallet,
            User,
            PayoutDestination,
            Invoice,
            ExchangeRate,
            Setting,
        )

        db.create_all()

        # Create default user
        default_user = "admin"
        if (
            not User.query.with_entities(User.id)
            .filter_by(username=default_user)
            .first()
        ):
            admin = User(username=default_user)
            db.session.add(admin)
            db.session.commit()

            flask_migrate.stamp(revision="head")
        else:
            flask_migrate.upgrade()

        # Register rate sources
        import shkeeper.modules.rates

        # Register crypto
        from .modules import cryptos
        from .modules.classes.crypto import Crypto

        for crypto in Crypto.instances.values():
            Wallet.register_currency(crypto)
            crypto._wallet = Wallet
            ExchangeRate.register_currency(crypto)

        from .wallet_encryption import WalletEncryptionPersistentStatus

        if setting := Setting.query.get("WalletEncryptionPersistentStatus"):
            app.logger.info(
                f"WalletEncryptionPersistentStatus: {WalletEncryptionPersistentStatus(int(setting.value))}"
            )
        else:  # this is a fresh instance or upgrade
            admin = User.query.get(1)
            if not admin.passhash or app.config.get("FORCE_WALLET_ENCRYPTION"):
                # this is a fresh instance or FORCE_WALLET_ENCRYPTION is set
                status = WalletEncryptionPersistentStatus.pending
            else:  # this is not a fresh instance, disabling wallet encryption
                status = WalletEncryptionPersistentStatus.disabled
            setting = Setting(
                name="WalletEncryptionPersistentStatus", value=status.value
            )
            db.session.add(setting)
            db.session.commit()
            app.logger.info(
                f"WalletEncryptionPersistentStatus is set to {WalletEncryptionPersistentStatus(int(setting.value))}"
            )

        if app.config.get("DEV_MODE"):
            if (
                wallet_encryption.wallet_encryption.persistent_status()
                is WalletEncryptionPersistentStatus.enabled
            ):
                if key := app.config.get("DEV_MODE_ENC_PW"):
                    wallet_encryption.wallet_encryption.set_key(key)
                    wallet_encryption.wallet_encryption.set_runtime_status(
                        WalletEncryptionRuntimeStatus.success
                    )

        from . import tasks

        scheduler.start()

        # end of with app.app_context():

    # template filters
    app.jinja_env.filters["format_decimal"] = format_decimal

    # apply the blueprints to the app
    from . import auth, wallet, api_v1, callback

    app.register_blueprint(auth.bp)
    app.register_blueprint(wallet.bp)
    app.register_blueprint(api_v1.bp)
    app.register_blueprint(callback.bp)
    app.register_error_handler(500, internal_server_error)
    app.register_error_handler(404, page_not_found_error)

    shkeeper_initialized.set()

    return app
