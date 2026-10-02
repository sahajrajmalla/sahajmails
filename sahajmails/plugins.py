"""Plugins.

A plugin is a Python module exposing a ``register(registry)`` function. It can
add a transport, an AI provider, a contact source, template filters, UI panels
and CLI commands, and it can observe or veto every message on its way out.

Two discovery routes:

* **Installed packages** declaring a ``sahajmails.plugins`` entry point, so
  ``pip install sahajmails-hubspot`` is all a user does.
* **A local folder** (``~/.sahajmails/plugins/*.py``) for personal scripts.
  Disabled unless explicitly enabled, because dropping a file in a folder is a
  much lower bar than installing a package.

A plugin runs with your full privileges. That is the same trust model as any
``pip install``, and the docs say so plainly rather than implying a sandbox that
does not exist.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from email.message import EmailMessage
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

from .errors import PluginError
from .models import Contact, RenderedEmail, SendResult

__all__ = ["ENTRY_POINT_GROUP", "PluginInfo", "PluginRegistry", "load_plugins"]

ENTRY_POINT_GROUP = "sahajmails.plugins"


class Plugin(Protocol):
    """What a plugin module must expose."""

    def register(self, registry: PluginRegistry) -> None: ...


@dataclass(slots=True)
class PluginInfo:
    """A discovered plugin, whether or not it loaded."""

    name: str
    source: str
    """``entry-point`` or the path it was loaded from."""

    summary: str = ""
    enabled: bool = True
    loaded: bool = False
    error: str = ""
    hooks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "summary": self.summary,
            "enabled": self.enabled,
            "loaded": self.loaded,
            "error": self.error,
            "hooks": self.hooks,
        }


class PluginRegistry:
    """What plugins register into, and what the app reads back out."""

    def __init__(self) -> None:
        self.plugins: list[PluginInfo] = []
        self._current: PluginInfo | None = None

        self.contact_sources: dict[str, Callable[..., Any]] = {}
        self.template_filters: dict[str, Callable[..., Any]] = {}
        self.ui_panels: list[dict[str, str]] = []
        self.cli_commands: list[Any] = []

        self._before_render: list[Callable[[Contact, dict[str, Any]], None]] = []
        self._after_render: list[Callable[[Contact, RenderedEmail], RenderedEmail | None]] = []
        self._before_send: list[Callable[[Contact, EmailMessage], bool | None]] = []
        self._after_send: list[Callable[[SendResult], None]] = []

    # -- registration API used by plugins ---------------------------------

    def _note(self, hook: str) -> None:
        if self._current is not None and hook not in self._current.hooks:
            self._current.hooks.append(hook)

    def add_transport(self, name: str, factory: Callable[..., Any]) -> None:
        from .transport import register_transport

        register_transport(name, factory)
        self._note(f"transport:{name}")

    def add_ai_backend(self, name: str, factory: Callable[..., Any]) -> None:
        from .ai.base import register_backend

        register_backend(name, factory)
        self._note(f"ai:{name}")

    def add_contact_source(self, name: str, loader: Callable[..., Any]) -> None:
        self.contact_sources[name] = loader
        self._note(f"contacts:{name}")

    def add_template_filter(self, name: str, fn: Callable[..., Any]) -> None:
        self.template_filters[name] = fn
        self._note(f"filter:{name}")

    def add_ui_panel(self, *, id: str, title: str, url: str, icon: str = "i-plug") -> None:  # noqa: A002
        self.ui_panels.append({"id": id, "title": title, "url": url, "icon": icon})
        self._note(f"panel:{id}")

    def add_cli_command(self, command: Any) -> None:
        self.cli_commands.append(command)
        self._note("cli")

    def on_before_render(self, fn: Callable[[Contact, dict[str, Any]], None]) -> None:
        self._before_render.append(fn)
        self._note("before_render")

    def on_after_render(self, fn: Callable[[Contact, RenderedEmail], RenderedEmail | None]) -> None:
        self._after_render.append(fn)
        self._note("after_render")

    def on_before_send(self, fn: Callable[[Contact, EmailMessage], bool | None]) -> None:
        """Inspect, mutate, or veto by returning ``False``."""
        self._before_send.append(fn)
        self._note("before_send")

    def on_after_send(self, fn: Callable[[SendResult], None]) -> None:
        self._after_send.append(fn)
        self._note("after_send")

    # -- dispatch used by the app -----------------------------------------

    def fire_before_render(self, contact: Contact, context: dict[str, Any]) -> None:
        for fn in self._before_render:
            _safely(fn, contact, context)

    def fire_after_render(self, contact: Contact, rendered: RenderedEmail) -> RenderedEmail:
        for fn in self._after_render:
            replaced = _safely(fn, contact, rendered)
            if isinstance(replaced, RenderedEmail):
                rendered = replaced
        return rendered

    def fire_before_send(self, contact: Contact, message: EmailMessage) -> bool:
        """Returns ``False`` if any plugin vetoed this message."""
        return all(_safely(fn, contact, message) is not False for fn in self._before_send)

    def fire_after_send(self, result: SendResult) -> None:
        for fn in self._after_send:
            _safely(fn, result)

    @property
    def has_hooks(self) -> bool:
        return bool(
            self._before_render or self._after_render or self._before_send or self._after_send
        )

    def describe(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.plugins]


def _safely(fn: Callable[..., Any], *args: Any) -> Any:
    """Run a plugin hook without letting it take the send down with it.

    A misbehaving third-party hook should not abort a thousand-message run.
    """
    try:
        return fn(*args)
    except Exception as exc:
        import logging

        logging.getLogger("sahajmails.plugins").warning(
            "plugin hook %s failed: %s", getattr(fn, "__qualname__", fn), exc
        )
        return None


# ─────────────────────────────────────────────────────────────── discovery ──


def _entry_points() -> Iterator[metadata.EntryPoint]:
    try:
        yield from metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # pragma: no cover - broken installed metadata
        return


def local_plugin_dir(data_dir: Path) -> Path:
    return data_dir / "plugins"


def discover(data_dir: Path, *, enabled: Sequence[str] | None = None) -> list[PluginInfo]:
    """List every plugin we can see, without importing local ones."""
    allow = None if enabled is None else {n.casefold() for n in enabled}
    found: list[PluginInfo] = []

    for entry in _entry_points():
        found.append(
            PluginInfo(
                name=entry.name,
                source="entry-point",
                summary=_summary_of(entry),
                # An installed package was a deliberate act; a dropped file was not.
                enabled=allow is None or entry.name.casefold() in allow,
            )
        )

    folder = local_plugin_dir(data_dir)
    if folder.is_dir():
        for path in sorted(folder.glob("*.py")):
            if path.name.startswith("_"):
                continue
            found.append(
                PluginInfo(
                    name=path.stem,
                    source=str(path),
                    enabled=bool(allow and path.stem.casefold() in allow),
                )
            )
    return found


def _summary_of(entry: metadata.EntryPoint) -> str:
    try:
        dist = entry.dist
        return str(dist.metadata["Summary"]) if dist else ""
    except Exception:  # pragma: no cover
        return ""


def load_plugins(data_dir: Path, *, enabled: Sequence[str] | None = None) -> PluginRegistry:
    """Discover and load plugins into a fresh registry.

    A plugin that fails to import is recorded and skipped; one broken package
    must not stop the application from starting.
    """
    registry = PluginRegistry()
    for info in discover(data_dir, enabled=enabled):
        registry.plugins.append(info)
        if not info.enabled:
            continue
        registry._current = info
        try:
            module = (
                _load_entry_point(info.name)
                if info.source == "entry-point"
                else _load_path(Path(info.source))
            )
            register = getattr(module, "register", None)
            if register is None:
                raise PluginError(f"{info.name} has no register() function.")
            register(registry)
            info.loaded = True
        except Exception as exc:
            info.error = str(exc)
        finally:
            registry._current = None
    return registry


def _load_entry_point(name: str) -> Any:
    for entry in _entry_points():
        if entry.name == name:
            return entry.load()
    raise PluginError(f"Entry point {name!r} disappeared.")


def _load_path(path: Path) -> Any:
    """Import a single-file plugin from disk."""
    if not path.is_file():
        raise PluginError(f"{path} no longer exists.")
    module_name = f"sahajmails_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PluginError(f"Could not load {path}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
