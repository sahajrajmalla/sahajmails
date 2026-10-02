"""Template rendering.

Jinja2 in a sandbox, with three properties that matter:

1. **Contact data is escaped as data.** Autoescape is on for the HTML part, so a
   spreadsheet cell containing ``<script>`` or ``\\1`` is inert. The 1.x engine
   fed values to ``re.sub`` as a *replacement template*, which both corrupted
   backslashes and let arbitrary HTML into outbound mail.

2. **Placeholders don't care about casing.** ``{{ firstName }}``,
   ``{{ first_name }}`` and ``{{ FIRSTNAME }}`` all resolve to the same column,
   because :class:`NormalizingContext` normalizes on lookup.

3. **Missing values are never silent.** 1.x blanked unknown placeholders, so a
   typo shipped "Hi ," to a thousand people. Here they are collected and, by
   default, raised.

The plain-text part is generated from the Markdown *source* rather than by
stripping tags out of the HTML, which produces text a human would actually
write — Markdown is designed to read well unrendered.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from html.parser import HTMLParser
from typing import Any

import markdown as markdown_lib
from jinja2 import ChainableUndefined, meta, nodes
from jinja2.exceptions import (
    SecurityError,
    TemplateRuntimeError,
    TemplateSyntaxError,
    UndefinedError,
)
from jinja2.exceptions import TemplateError as JinjaTemplateError
from jinja2.ext import Extension
from jinja2.parser import Parser
from jinja2.runtime import Context, Macro
from jinja2.runtime import missing as _missing  # type: ignore[attr-defined]
from jinja2.sandbox import SandboxedEnvironment

from .errors import TemplateError
from .models import Contact, RenderedEmail, normalize_key

__all__ = [
    "AISlot",
    "BodyFormat",
    "EmailTemplate",
    "MissingPolicy",
    "RenderTrace",
    "find_slots",
    "html_to_text",
    "wrap_html",
]


class BodyFormat(StrEnum):
    """How to interpret the message body.

    Markdown is a fine default for people who know it and a trap for people who
    don't — a formal letter should not sprout headings because a line happened
    to start with ``#``. So the format is explicit and the editor offers all
    four.
    """

    MARKDOWN = "markdown"
    """Markdown syntax is rendered. The source doubles as the text part."""

    RICH = "rich"
    """An HTML fragment from the rich-text editor. Wrapped in the email shell."""

    PLAIN = "plain"
    """No formatting at all. Paragraphs and line breaks are preserved, nothing else."""

    HTML = "html"
    """A complete HTML document, passed through untouched."""


class MissingPolicy(StrEnum):
    """What to do when a template references a column that does not exist."""

    ERROR = "error"
    """Raise. The default: loud beats wrong when you are mailing strangers."""

    BLANK = "blank"
    """Render nothing. Matches 1.x behaviour."""

    KEEP = "keep"
    """Render the placeholder back out, so the gap is visible in the preview."""


@dataclass(slots=True)
class RenderTrace:
    """Per-render state.

    Held in a :class:`~contextvars.ContextVar` rather than on the environment so
    that concurrent renders of the same template — which the parallel send path
    does — cannot see each other's slots or missing-variable sets.
    """

    missing: set[str] = field(default_factory=set)
    slots_seen: dict[str, str] = field(default_factory=dict)
    """Slot name -> the instruction with its own placeholders resolved."""

    filled: Mapping[str, str] = field(default_factory=dict)
    """Approved AI text to emit, by slot name."""

    policy: MissingPolicy = MissingPolicy.ERROR


_trace: ContextVar[RenderTrace | None] = ContextVar("sahajmails_render_trace", default=None)


def _current_trace() -> RenderTrace:
    trace = _trace.get()
    if trace is None:  # pragma: no cover - render always installs one
        return RenderTrace()
    return trace


# --------------------------------------------------------------------- undefined


class _CollectingUndefined(ChainableUndefined):
    """Records every unresolved name, then applies the configured policy.

    Extends ``ChainableUndefined`` so that ``{{ a.b.c }}`` on a missing ``a``
    reports one clean miss instead of an attribute error, and so ``{% if x %}``
    stays falsy rather than raising.
    """

    __slots__ = ()

    def __init__(
        self,
        hint: str | None = None,
        obj: object = _missing,
        name: str | None = None,
        exc: type[TemplateRuntimeError] = UndefinedError,
    ) -> None:
        super().__init__(hint=hint, obj=obj, name=name, exc=exc)
        _current_trace().missing.add(self._label)

    @property
    def _label(self) -> str:
        return str(self._undefined_name or self._undefined_hint or "value")

    def _resolve(self) -> str:
        trace = _current_trace()
        name = self._label
        if trace.policy is MissingPolicy.ERROR:
            raise TemplateError(
                f"Template uses {{{{ {name} }}}} but your contact file has no such column.",
                hint=(
                    f"Add a {name!r} column, fix the spelling, or write "
                    f"{{{{ {name} | default('...') }}}} to supply a fallback."
                ),
            )
        if trace.policy is MissingPolicy.KEEP:
            return "{{ " + name + " }}"
        return ""

    # Jinja reaches undefined values through all of these paths.
    def __str__(self) -> str:
        return self._resolve()

    def __html__(self) -> str:
        return self._resolve()

    def __iter__(self) -> Iterator[Any]:
        self._resolve()
        return iter(())

    def __len__(self) -> int:
        self._resolve()
        return 0

    def __add__(self, other: Any) -> str:  # type: ignore[override]
        return self._resolve() + str(other)

    def __radd__(self, other: Any) -> str:  # type: ignore[override]
        return str(other) + self._resolve()


# ----------------------------------------------------------------------- context


class NormalizingContext(Context):
    """Resolves ``{{ firstName }}`` against a ``first_name`` column.

    Jinja's generated code routes every name through ``resolve_or_missing``, so
    normalizing here covers plain output, ``if``, filters and ``default()``
    alike, with no per-variable aliasing.
    """

    def resolve_or_missing(self, key: str) -> Any:
        found = super().resolve_or_missing(key)
        if found is not _missing:
            return found
        normalized = normalize_key(key)
        if normalized and normalized != key:
            return super().resolve_or_missing(normalized)
        return _missing


# -------------------------------------------------------------------- ai slots


@dataclass(frozen=True, slots=True)
class AISlot:
    """One ``{% ai %}`` block declared in a template."""

    name: str
    instruction: str
    """Raw instruction source, before its own placeholders are resolved."""

    fallback: str = ""
    max_words: int | None = None
    max_chars: int | None = None
    temperature: float | None = None
    model: str | None = None
    banned_phrases: tuple[str, ...] = ()

    @property
    def has_fallback(self) -> bool:
        return bool(self.fallback.strip())


_SLOT_OPTIONS = frozenset(
    {"fallback", "max_words", "max_chars", "temperature", "model", "banned_phrases"}
)


class AISlotExtension(Extension):
    """``{% ai "name" max_words=30 fallback="..." %}instruction{% endai %}``

    The block body is the *instruction*, never the output. At render time the
    tag emits the approved text for that slot, or the fallback when there isn't
    one — so a template renders correctly with the AI layer switched off
    entirely, and a failed generation degrades to the fallback rather than
    blocking a send.
    """

    tags = {"ai"}  # noqa: RUF012 - Jinja's Extension API requires a plain set

    def parse(self, parser: Parser) -> nodes.Node:
        lineno = next(parser.stream).lineno

        if parser.stream.current.type != "string":
            parser.fail('the ai tag needs a slot name, e.g. {% ai "opener" %}', lineno)
        name_node = parser.parse_expression()

        kwargs: list[nodes.Keyword] = []
        while parser.stream.current.type != "block_end":
            parser.stream.skip_if("comma")
            key_token = parser.stream.expect("name")
            if key_token.value not in _SLOT_OPTIONS:
                parser.fail(
                    f"unknown ai option {key_token.value!r}; "
                    f"valid options are {', '.join(sorted(_SLOT_OPTIONS))}",
                    key_token.lineno,
                )
            parser.stream.expect("assign")
            kwargs.append(nodes.Keyword(key_token.value, parser.parse_expression()))

        body = parser.parse_statements(("name:endai",), drop_needle=True)
        call = self.call_method("_emit", args=[name_node], kwargs=kwargs, lineno=lineno)
        return nodes.CallBlock(call, [], [], body, lineno=lineno)

    def _emit(self, name: str, *, caller: Macro, **options: Any) -> str:
        trace = _current_trace()
        # Rendering the body resolves the instruction's own placeholders, which
        # is exactly what an AI backend needs to see.
        trace.slots_seen[name] = str(caller()).strip()

        approved = trace.filled.get(name, "")
        if approved.strip():
            return approved
        fallback = options.get("fallback") or ""
        return str(fallback)


# ------------------------------------------------------------------- html/text


class _TextExtractor(HTMLParser):
    """Turns HTML into readable plain text. Used only for HTML-authored bodies."""

    _BLOCK = frozenset(
        {
            "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
            "blockquote", "section", "article", "header", "footer", "table",
        }
    )  # fmt: skip

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head"}:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")
        elif tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"}:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")
        elif tag == "a" and self._href:
            # Keep the destination. A text part where every link vanished is
            # useless, and looks like cloaking to a spam filter.
            if self._href not in "".join(self.parts[-3:]):
                self.parts.append(f" <{self._href}>")
            self._href = None

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Best-effort plain-text rendering of an HTML body."""
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    text = "".join(extractor.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_like_html(body: str) -> bool:
    return body.lstrip()[:200].casefold().startswith(("<!doctype html", "<html"))


# The layout uses inline styles on a table skeleton rather than a <style> block.
# Gmail's mobile apps drop <style> for non-Gmail accounts and Outlook's Word
# renderer ignores most of it, so a stylesheet is the one thing guaranteed not
# to survive. This renders consistently without a CSS-inlining dependency.
_LAYOUT = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{title}</title>
</head>
<body style="margin:0; padding:0; width:100%; background-color:#f5f6f8;">
{preheader}
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" \
style="background-color:#f5f6f8;">
<tr>
<td align="center" style="padding:24px 12px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" \
style="max-width:600px; width:100%; background-color:#ffffff; border-radius:12px; \
border:1px solid #e6e8eb;">
<tr>
<td style="padding:32px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',\
Roboto,Helvetica,Arial,sans-serif; font-size:16px; line-height:1.65; color:#1a1a1a;">
{content}
</td>
</tr>
</table>
{footer}
</td>
</tr>
</table>
</body>
</html>"""

# Zero-width joiners pad the snippet so the client shows the preheader alone
# rather than trailing the first line of body copy after it.
_PREHEADER = (
    '<div style="display:none; max-height:0; overflow:hidden; mso-hide:all;'
    ' font-size:1px; line-height:1px; color:#f5f6f8; opacity:0;">'
    "{text}" + "&#8199;&#65279;&#847;" * 8 + "</div>"
)

_FOOTER = (
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600"'
    ' style="max-width:600px; width:100%;"><tr><td align="center"'
    " style=\"padding:16px 8px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',"
    'Roboto,Helvetica,Arial,sans-serif; font-size:12px; line-height:1.5; color:#8a9099;">'
    "{content}</td></tr></table>"
)


def wrap_html(content: str, *, title: str = "", preheader: str = "", footer: str = "") -> str:
    """Wrap rendered body content in the responsive email shell."""
    return _LAYOUT.format(
        title=_escape_text(title) or "Message",
        content=content,
        preheader=_PREHEADER.format(text=_escape_text(preheader)) if preheader else "",
        footer=_FOOTER.format(content=footer) if footer else "",
    )


def _escape_text(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


# ------------------------------------------------------------------- template


def _build_environment(*, autoescape: bool) -> SandboxedEnvironment:
    env = SandboxedEnvironment(
        autoescape=autoescape,
        undefined=_CollectingUndefined,
        extensions=[AISlotExtension],
        keep_trailing_newline=True,
    )
    env.context_class = NormalizingContext
    return env


@dataclass(slots=True)
class EmailTemplate:
    """A subject line and body, compiled once and rendered per contact.

    The body may be Markdown (the common case) or a complete HTML document. A
    body starting with ``<html`` or ``<!doctype html>`` is passed through
    untouched, so hand-built HTML emails survive intact.
    """

    subject: str
    body: str
    preheader: str = ""
    footer: str = ""
    body_format: BodyFormat | str = BodyFormat.MARKDOWN
    """Coerced to :class:`BodyFormat` on construction; a plain string is fine."""

    missing_policy: MissingPolicy | str = MissingPolicy.ERROR
    """Coerced to :class:`MissingPolicy` on construction."""

    _html_env: SandboxedEnvironment = field(init=False, repr=False)
    _text_env: SandboxedEnvironment = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Coerce the enum fields. Without this a caller passing the plain string
        # "error" gets a str, every `is MissingPolicy.ERROR` comparison is False,
        # and missing placeholders silently blank instead of raising — the exact
        # 1.x behaviour this policy exists to prevent.
        self.missing_policy = MissingPolicy(self.missing_policy)
        self.body_format = BodyFormat(self.body_format)
        self._html_env = _build_environment(autoescape=True)
        self._text_env = _build_environment(autoescape=False)
        # Compile eagerly so syntax errors surface on construction rather than
        # on the first recipient of a thousand-contact run.
        self._compile()

    def _sources(self) -> tuple[tuple[SandboxedEnvironment, str, str], ...]:
        return (
            (self._text_env, self.subject, "subject"),
            (self._html_env, self.body, "body"),
            (self._text_env, self.preheader, "preheader"),
        )

    def _compile(self) -> None:
        for env, source, what in self._sources():
            if not source:
                continue
            try:
                env.from_string(source)
            except TemplateSyntaxError as exc:
                raise TemplateError(
                    f"Syntax error in the {what} on line {exc.lineno}: {exc.message}",
                    hint="Check that every {{ }} and {% %} is closed and balanced.",
                ) from exc

    # -- introspection ----------------------------------------------------

    def variables(self) -> set[str]:
        """Every column name this template references."""
        found: set[str] = set()
        for env, source, _ in self._sources():
            if source:
                found |= meta.find_undeclared_variables(env.parse(source))
        return {v for v in found if not v.startswith("_")}

    def slots(self) -> list[AISlot]:
        """Every ``{% ai %}`` block declared in the body."""
        return find_slots(self.body, self._html_env)

    # -- rendering --------------------------------------------------------

    def render(
        self,
        contact: Contact,
        *,
        ai_slots: Mapping[str, str] | None = None,
        trace: RenderTrace | None = None,
    ) -> RenderedEmail:
        """Render this template for one contact.

        Args:
            contact: Supplies the template variables.
            ai_slots: Approved text for ``{% ai %}`` blocks, keyed by slot name.
            trace: Collects missing variables and resolved slot instructions.

        Raises:
            TemplateError: Rendering failed, or a placeholder was missing and
                the policy is :attr:`MissingPolicy.ERROR`.
        """
        active = trace if trace is not None else RenderTrace()
        active.policy = MissingPolicy(self.missing_policy)
        active.filled = dict(ai_slots or {})
        token = _trace.set(active)

        try:
            context: dict[str, Any] = dict(contact.fields)
            context["email"] = contact.email
            context["_row"] = contact.row

            subject = " ".join(
                self._render_one(self._text_env, self.subject, context, "subject").split()
            )
            preheader = self._render_one(
                self._text_env, self.preheader, context, "preheader"
            ).strip()

            html, text = self._render_body(context, subject, preheader)
            return RenderedEmail(subject=subject, html=html, text=text, preheader=preheader)
        finally:
            _trace.reset(token)

    def _render_body(
        self, context: Mapping[str, Any], subject: str, preheader: str
    ) -> tuple[str, str]:
        """Produce the HTML and text parts according to :attr:`body_format`.

        Both parts always come from two separate renders: the autoescaped one
        for HTML (so contact data cannot inject markup) and the raw one for
        text (where escaping would be wrong).
        """
        fmt = self.body_format
        if fmt is BodyFormat.HTML or (fmt is BodyFormat.RICH and _looks_like_html(self.body)):
            html = self._render_one(self._html_env, self.body, context, "body")
            return html, html_to_text(html)

        if fmt is BodyFormat.RICH:
            fragment = self._render_one(self._html_env, self.body, context, "body")
            return (
                wrap_html(fragment, title=subject, preheader=preheader, footer=self.footer),
                html_to_text(fragment),
            )

        if fmt is BodyFormat.PLAIN:
            # A formal letter, sent exactly as typed. Paragraphs become <p> and
            # single newlines become <br> purely so the HTML part mirrors the
            # text part; nothing else is interpreted.
            text = self._render_one(self._text_env, self.body, context, "body").strip()
            escaped = self._render_one(self._html_env, self.body, context, "body").strip()
            blocks = [b.strip() for b in re.split(r"\n\s*\n", escaped) if b.strip()]
            body_html = "".join(
                f'<p style="margin:0 0 16px;">{b.replace(chr(10), "<br>")}</p>' for b in blocks
            )
            return (
                wrap_html(body_html, title=subject, preheader=preheader, footer=self.footer),
                text,
            )

        # Markdown. The autoescaped pass keeps the template's own syntax while
        # substituted values arrive inert; the raw pass doubles as the text part.
        escaped = self._render_one(self._html_env, self.body, context, "body")
        html = wrap_html(
            markdown_lib.markdown(escaped, extensions=["extra", "sane_lists", "nl2br"]),
            title=subject,
            preheader=preheader,
            footer=self.footer,
        )
        text = self._render_one(self._text_env, self.body, context, "body").strip()
        return html, text

    def _render_one(
        self,
        env: SandboxedEnvironment,
        source: str,
        context: Mapping[str, Any],
        what: str,
    ) -> str:
        if not source:
            return ""
        try:
            # render() returns a plain str with escaping already applied to the
            # substituted values. Do not unescape it — that would undo exactly
            # the protection autoescape provides.
            return env.from_string(source).render(context)
        except TemplateError:
            raise
        except SecurityError as exc:
            raise TemplateError(
                f"The {what} tried to do something the sandbox does not allow: {exc}",
                hint="Templates can read your columns and use filters, nothing more.",
            ) from exc
        except JinjaTemplateError as exc:
            raise TemplateError(f"Could not render the {what}: {exc}") from exc


_AI_BLOCK_RE = re.compile(
    r"\{%-?\s*ai\s.*?-?%\}(?P<body>.*?)\{%-?\s*endai\s*-?%\}",
    re.DOTALL | re.IGNORECASE,
)


def find_slots(body: str, env: SandboxedEnvironment | None = None) -> list[AISlot]:
    """Extract every ``{% ai %}`` declaration from a template body."""
    environment = env or _build_environment(autoescape=True)
    try:
        ast = environment.parse(body)
    except TemplateSyntaxError as exc:
        raise TemplateError(f"Syntax error on line {exc.lineno}: {exc.message}") from exc

    # Jinja's AST records line numbers but not source spans, so instructions are
    # matched positionally. Both sequences are in document order, so they line up.
    instructions = [m.group("body").strip() for m in _AI_BLOCK_RE.finditer(body)]

    slots: list[AISlot] = []
    seen: set[str] = set()

    for node in ast.find_all(nodes.CallBlock):
        call = node.call
        if not isinstance(call, nodes.Call) or not isinstance(call.node, nodes.ExtensionAttribute):
            continue
        if not call.node.name.endswith("_emit"):
            continue

        name = _literal(call.args[0]) if call.args else None
        if not isinstance(name, str) or not name:
            continue
        if name in seen:
            raise TemplateError(
                f"Two {{% ai %}} blocks are both named {name!r}.",
                hint="Slot names must be unique — they are how approved text is matched back.",
            )
        seen.add(name)

        options = {kw.key: _literal(kw.value) for kw in call.kwargs}
        banned = options.get("banned_phrases") or ()
        if isinstance(banned, str):
            banned = (banned,)

        index = len(slots)
        slots.append(
            AISlot(
                name=name,
                instruction=instructions[index] if index < len(instructions) else "",
                fallback=str(options.get("fallback") or ""),
                max_words=_as_int(options.get("max_words")),
                max_chars=_as_int(options.get("max_chars")),
                temperature=_as_float(options.get("temperature")),
                model=str(options["model"]) if options.get("model") else None,
                banned_phrases=tuple(str(b) for b in banned),
            )
        )
    return slots


def _literal(node: nodes.Node) -> Any:
    """Evaluate a constant AST node, or return None if it is not constant."""
    as_const = getattr(node, "as_const", None)
    if as_const is None:
        return None
    try:
        return as_const()
    except nodes.Impossible:
        return None


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None
