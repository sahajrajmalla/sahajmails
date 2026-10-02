"""Settings resolution.

Precedence, highest first:

1. an explicit argument
2. ``SAHAJMAILS_*`` environment variables
3. ``./sahajmails.toml``
4. ``~/.sahajmails/config.toml``
5. the settings table in the local database
6. the provider preset's defaults

Passwords are the exception: they are **session-only by default**. A password
reaches disk only if the user opts in, and then either through the OS keyring or
into a ``0600`` file with a warning shown first. 1.x offered nowhere to put a
credential except a text box, which is why people ended up with app passwords in
stray ``.env`` files.
"""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .presets import Preset, Security, get_preset, guess_preset
from .storage.db import default_data_dir

__all__ = ["Settings", "config_search_paths", "load_settings"]

ENV_PREFIX = "SAHAJMAILS_"


@dataclass(slots=True)
class Settings:
    """Everything needed to send, resolved from all sources."""

    # -- identity ---------------------------------------------------------
    sender_email: str = ""
    sender_name: str = ""
    reply_to: str = ""

    # -- transport --------------------------------------------------------
    provider: str = "gmail"
    smtp_host: str = ""
    smtp_port: int = 0
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False)
    security: Security = Security.STARTTLS
    timeout: float = 30.0

    # -- pacing -----------------------------------------------------------
    rate_per_minute: int = 0
    concurrency: int = 0
    daily_limit: int | None = None

    # -- behaviour --------------------------------------------------------
    unsubscribe_mailto: str = ""
    unsubscribe_url: str = ""
    missing_policy: str = "error"
    footer: str = ""

    # -- ai ---------------------------------------------------------------
    ai_provider: str = ""
    ai_model: str = ""
    ai_api_key: str = field(default="", repr=False)
    ai_base_url: str = ""

    # -- storage ----------------------------------------------------------
    data_dir: Path = field(default_factory=default_data_dir)

    def __post_init__(self) -> None:
        preset = self.preset
        # Fill anything still unset from the provider preset, so choosing
        # "Gmail" really is the only decision most users make.
        if not self.smtp_host:
            self.smtp_host = preset.host
        if not self.smtp_port:
            self.smtp_port = preset.port
        if not self.rate_per_minute:
            self.rate_per_minute = preset.rate_per_minute
        if not self.concurrency:
            self.concurrency = preset.max_concurrency
        if self.daily_limit is None:
            self.daily_limit = preset.daily_limit
        if not self.smtp_username and self.sender_email:
            self.smtp_username = self.sender_email
        if self.provider != "custom":
            self.security = preset.security

    @property
    def preset(self) -> Preset:
        return get_preset(self.provider)

    @property
    def from_address(self) -> str:
        return self.sender_email or self.smtp_username

    def with_password(self, password: str) -> Settings:
        return replace(self, smtp_password=password)

    def transport_kwargs(self) -> dict[str, Any]:
        return {
            "host": self.smtp_host,
            "port": self.smtp_port,
            "username": self.smtp_username,
            "password": self.smtp_password,
            "security": self.security,
            "timeout": self.timeout,
            "rate_per_minute": self.rate_per_minute,
        }

    def validate(self) -> None:
        """Raise if the settings could not possibly send anything."""
        if not self.from_address:
            raise ConfigError(
                "No sending address configured.",
                hint="Set it in Settings, or export SAHAJMAILS_SENDER_EMAIL.",
            )
        if not self.smtp_host:
            raise ConfigError(
                "No SMTP host configured.",
                hint="Choose a provider preset or set SAHAJMAILS_SMTP_HOST.",
            )
        if not self.smtp_password:
            raise ConfigError(
                "No password configured.",
                hint=self.preset.setup_hint or "Set SAHAJMAILS_SMTP_PASSWORD.",
            )

    def redacted(self) -> dict[str, Any]:
        """Serialisable view with every secret removed. Safe to log or send to the UI."""
        return {
            "sender_email": self.sender_email,
            "sender_name": self.sender_name,
            "reply_to": self.reply_to,
            "provider": self.provider,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_username": self.smtp_username,
            "smtp_password": "••••••••" if self.smtp_password else "",
            "security": str(self.security),
            "rate_per_minute": self.rate_per_minute,
            "concurrency": self.concurrency,
            "daily_limit": self.daily_limit,
            "unsubscribe_mailto": self.unsubscribe_mailto,
            "unsubscribe_url": self.unsubscribe_url,
            "missing_policy": self.missing_policy,
            "footer": self.footer,
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_api_key": "••••••••" if self.ai_api_key else "",
            "ai_base_url": self.ai_base_url,
        }


_FIELD_TYPES: dict[str, str] = {
    "smtp_port": "int",
    "rate_per_minute": "int",
    "concurrency": "int",
    "daily_limit": "int",
    "timeout": "float",
}


def config_search_paths() -> list[Path]:
    """Config files in precedence order, nearest first."""
    return [Path.cwd() / "sahajmails.toml", default_data_dir() / "config.toml"]


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}", hint="Check the TOML syntax.") from exc

    # Flatten the two sections we care about so [smtp] host becomes smtp_host.
    flat: dict[str, Any] = {k: v for k, v in data.items() if not isinstance(v, dict)}
    for section, prefix in (("smtp", "smtp_"), ("ai", "ai_")):
        block = data.get(section)
        if isinstance(block, dict):
            for key, value in block.items():
                flat[f"{prefix}{key}" if not key.startswith(prefix) else key] = value
    return flat


def _read_env() -> dict[str, Any]:
    values: dict[str, Any] = {}
    known = set(Settings.__dataclass_fields__)
    for key, raw in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        name = key[len(ENV_PREFIX) :].casefold()
        if name in known and raw != "":
            values[name] = raw
    return values


def _coerce(name: str, value: Any) -> Any:
    kind = _FIELD_TYPES.get(name)
    try:
        if kind == "int":
            return int(value)
        if kind == "float":
            return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}") from exc
    if name == "security":
        try:
            return Security(str(value).casefold())
        except ValueError as exc:
            raise ConfigError(
                f"Unknown security mode {value!r}.",
                hint="Use starttls, ssl, or none.",
            ) from exc
    if name == "data_dir":
        return Path(str(value)).expanduser()
    return value


def load_settings(
    overrides: Mapping[str, Any] | None = None,
    *,
    stored: Mapping[str, str] | None = None,
    config_file: str | Path | None = None,
    use_env: bool = True,
) -> Settings:
    """Build :class:`Settings` from every source, honouring precedence.

    Args:
        overrides: Explicit values, highest precedence.
        stored: Values from the database settings table, lowest precedence.
        config_file: Read this instead of searching the default locations.
        use_env: Whether to consult ``SAHAJMAILS_*``.
    """
    known = set(Settings.__dataclass_fields__)
    merged: dict[str, Any] = {}

    for key, value in (stored or {}).items():
        if key in known and value != "":
            merged[key] = value

    files = [Path(config_file)] if config_file else list(reversed(config_search_paths()))
    for path in files:
        for key, value in _read_toml(path).items():
            if key in known:
                merged[key] = value

    if use_env:
        merged.update(_read_env())

    for key, value in (overrides or {}).items():
        if key in known and value not in (None, ""):
            merged[key] = value

    # Infer the provider from the address when nobody said otherwise, so
    # alice@gmail.com just works with no configuration at all.
    if "provider" not in merged:
        address = str(merged.get("sender_email") or merged.get("smtp_username") or "")
        guessed = guess_preset(address) if address else None
        if guessed:
            merged["provider"] = guessed.key

    coerced = {k: _coerce(k, v) for k, v in merged.items()}
    return Settings(**coerced)


def write_config(path: Path, values: Mapping[str, Any], *, include_secrets: bool = False) -> None:
    """Write a TOML config file with restrictive permissions.

    Refuses to persist secrets unless explicitly told to, and chmods the file to
    ``0600`` when it does — a credential readable by every account on the
    machine is barely better than no protection at all.
    """
    secret_keys = {"smtp_password", "ai_api_key"}
    lines = ["# sahajmails configuration", ""]
    smtp: list[str] = []
    ai: list[str] = []
    top: list[str] = []

    for key, value in sorted(values.items()):
        if value in (None, "") or (key in secret_keys and not include_secrets):
            continue
        rendered = _toml_value(value)
        if key.startswith("smtp_"):
            smtp.append(f"{key.removeprefix('smtp_')} = {rendered}")
        elif key.startswith("ai_"):
            ai.append(f"{key.removeprefix('ai_')} = {rendered}")
        else:
            top.append(f"{key} = {rendered}")

    lines.extend(top)
    if smtp:
        lines += ["", "[smtp]", *smtp]
    if ai:
        lines += ["", "[ai]", *ai]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if include_secrets:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
