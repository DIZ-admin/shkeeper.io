import logging
import os
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)


def load_secret(env_var_name: str, secret_file_var: str = None) -> str | None:
    if secret_file_var:
        secret_path = os.getenv(secret_file_var)
        if secret_path:
            secret_file = Path(secret_path)
            if secret_file.exists():
                try:
                    return secret_file.read_text().strip()
                except OSError as exc:
                    logger.warning(
                        "Failed to read secret from %s: %s. Falling back to environment variable.",
                        secret_path,
                        exc,
                    )
    return os.getenv(env_var_name)


def read_env_bool(env_var_name: str, default: bool = False) -> bool:
    value = os.getenv(env_var_name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def remove_exponent(d: Decimal) -> str:
    # return d.quantize(Decimal(1)) if d == d.to_integral() else d.normalize()
    try:
        return ("%.10f" % d).rstrip("0").rstrip(".")
    except TypeError:
        return "0"


def format_decimal(d: Decimal, precision: int = 8, st: bool = False) -> str:
    # separate thousands
    if st:
        return f"{remove_exponent(d):,}"
    else:
        return str(remove_exponent(d))
