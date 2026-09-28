"""Security regressions.

This process can send mail as the user, so every one of these is a real
consequence, not a theoretical one.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from sahajmails.contacts import load_contacts
from sahajmails.message import build_message, prepare_attachments
from sahajmails.models import Attachment, Contact
from sahajmails.server.app import create_app
from sahajmails.server.security import SecurityConfig, is_loopback
from sahajmails.template import BodyFormat, EmailTemplate

TOKEN = "test-token-0123456789abcdef"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Drive the ASGI app in-process.

    httpx's ASGITransport rather than starlette's TestClient: the latter now
    requires an extra package, and this exercises the real middleware stack
    without a socket. The base URL matters — the Host allowlist refuses
    anything that does not look like the real server.
    """
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(security=SecurityConfig(token=TOKEN, port=8000), data_dir=Path(tmp))
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost:8000"
            ) as http:
                yield http
        finally:
            # try/finally, not a trailing statement: when a test fails pytest
            # closes the generator, GeneratorExit propagates from the yield, and
            # anything after the with-block would never run — leaking the
            # SQLite connection into the next test.
            app.state.app_state.close()


def auth(extra: dict[str, str] | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-SahajMails": "1", **(extra or {})}


class TestAuthentication:
    async def test_api_requires_a_token(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/api/state")).status_code == 401

    async def test_wrong_token_is_refused(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/state", headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401

    async def test_valid_token_works(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/api/state", headers=auth())).status_code == 200

    async def test_the_shell_loads_without_a_token(self, client: httpx.AsyncClient) -> None:
        # The page has to render before it can authenticate.
        assert (await client.get("/")).status_code == 200

    async def test_error_body_never_leaks_the_token(self, client: httpx.AsyncClient) -> None:
        assert TOKEN not in (await client.get("/api/state")).text


class TestHostHeader:
    """DNS rebinding: a hostile site can point its own domain at 127.0.0.1."""

    async def test_foreign_host_is_refused(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/state", headers=auth({"Host": "evil.example.com"}))
        assert response.status_code == 400
        assert "localhost" in response.json()["error"]

    async def test_localhost_is_accepted(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/state", headers=auth({"Host": "localhost:8000"}))
        assert response.status_code == 200

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("localhost", True),
            ("127.0.0.1", True),
            ("::1", True),
            ("0.0.0.0", False),  # noqa: S104 - asserting this is NOT loopback
            ("192.168.1.5", False),
            ("example.com", False),
        ],
    )
    def test_loopback_detection(self, host: str, expected: bool) -> None:
        assert is_loopback(host) is expected


class TestCrossOriginAndCsrf:
    async def test_cross_origin_is_refused(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/api/state", headers=auth({"Origin": "https://evil.example.com"})
        )
        assert response.status_code == 403

    async def test_mutation_without_the_csrf_header_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        # A cross-site form post cannot set a custom header, which is what makes
        # requiring one an effective CSRF defence.
        response = await client.post(
            "/api/settings", json={}, headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 403


class TestPathTraversal:
    @pytest.mark.parametrize(
        "token",
        ["../../../../etc/passwd", "..%2f..%2fetc%2fpasswd", "/etc/passwd", "....//etc/passwd"],
    )
    async def test_contact_tokens_cannot_escape_the_upload_folder(
        self, client: httpx.AsyncClient, token: str
    ) -> None:
        response = await client.get(f"/api/contacts/{token}", headers=auth())
        assert response.status_code in {400, 404}
        assert "root:" not in response.text

    async def test_upload_filename_cannot_escape(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/api/contacts/upload",
            headers=auth(),
            files={"file": ("../../evil.csv", b"email\na@b.com\n", "text/csv")},
        )
        assert response.status_code == 200
        # The stored token is generated by us, never taken from the upload.
        assert ".." not in response.json()["token"]


class TestSecretsAreNotLeaked:
    async def test_settings_never_return_the_password(self, client: httpx.AsyncClient) -> None:
        await client.post(
            "/api/settings",
            headers=auth(),
            json={"sender_email": "a@b.com", "smtp_password": "hunter2"},
        )
        body = (await client.get("/api/state", headers=auth())).text
        assert "hunter2" not in body

    async def test_ai_key_is_never_returned(self, client: httpx.AsyncClient) -> None:
        await client.post("/api/settings", headers=auth(), json={"ai_api_key": "sk-secret-value"})
        assert "sk-secret-value" not in (await client.get("/api/state", headers=auth())).text


class TestEmailInjection:
    """Contact data is untrusted. It must never become markup or headers."""

    def test_script_in_a_cell_is_escaped(self) -> None:
        template = EmailTemplate(subject="Hi", body="Hello {{ note }}")
        rendered = template.render(
            Contact(email="a@b.com", fields={"note": "<script>alert(1)</script>"})
        )
        assert "<script>" not in rendered.html
        assert "&lt;script&gt;" in rendered.html

    def test_link_injection_is_escaped(self) -> None:
        template = EmailTemplate(subject="Hi", body="See {{ note }}")
        html = template.render(
            Contact(email="a@b.com", fields={"note": '<a href="https://evil.test">click</a>'})
        ).html
        assert "evil.test" in html  # the text survives
        assert "<a href" not in html  # but not as a live link

    def test_backslashes_survive_verbatim(self) -> None:
        # 1.x fed values to re.sub as a replacement template, so a Windows path
        # was mangled and a value containing \1 raised re.error.
        template = EmailTemplate(subject="Hi", body="Path {{ p }}")
        assert (
            r"C:\temp" in template.render(Contact(email="a@b.com", fields={"p": r"C:\temp"})).text
        )
        assert r"\1" in template.render(Contact(email="a@b.com", fields={"p": r"\1"})).text

    def test_header_injection_via_subject_is_neutralised(self) -> None:
        template = EmailTemplate(subject="Hi {{ n }}", body="x")
        rendered = template.render(
            Contact(email="a@b.com", fields={"n": "X\r\nBcc: evil@test.com"})
        )
        assert "\n" not in rendered.subject and "\r" not in rendered.subject

    def test_custom_headers_cannot_inject_newlines(self) -> None:
        message = build_message(
            sender="me@example.com",
            recipient="you@example.com",
            rendered=EmailTemplate(subject="s", body="b").render(Contact(email="you@example.com")),
            headers={"X-Thing": "value\r\nBcc: evil@test.com"},
        )
        assert "evil@test.com" not in str(message.get("Bcc", ""))
        assert "\r" not in str(message["X-Thing"])

    def test_custom_headers_cannot_shadow_controlled_ones(self) -> None:
        message = build_message(
            sender="me@example.com",
            recipient="you@example.com",
            rendered=EmailTemplate(subject="real", body="b").render(
                Contact(email="you@example.com")
            ),
            headers={"From": "spoofed@evil.test", "Subject": "spoofed"},
        )
        assert "spoofed" not in str(message["From"])
        assert str(message["Subject"]) == "real"

    def test_attachment_filenames_are_stripped_of_paths(self) -> None:
        [prepared] = prepare_attachments(
            [Attachment(filename="../../../etc/passwd", content=b"x", content_type="text/plain")]
        )
        assert prepared.filename == "passwd"

    def test_template_sandbox_blocks_attribute_escapes(self) -> None:
        # A template author is trusted, but a shared or generated template must
        # not reach the interpreter. The sandbox yields undefined rather than
        # raising, so the check is that nothing internal reaches the output.
        template = EmailTemplate(
            subject="s", body="{{ ''.__class__.__mro__ }}", missing_policy="blank"
        )
        rendered = template.render(Contact(email="a@b.com"))
        assert "class" not in rendered.text
        assert "object" not in rendered.text

    def test_missing_policy_is_coerced_from_a_string(self) -> None:
        from sahajmails.errors import TemplateError
        from sahajmails.template import MissingPolicy

        # A plain string must behave identically to the enum, or the policy
        # silently degrades to "blank".
        template = EmailTemplate(subject="s", body="Hi {{ nope }}", missing_policy="error")
        assert template.missing_policy is MissingPolicy.ERROR
        with pytest.raises(TemplateError):
            template.render(Contact(email="a@b.com"))


class TestRichHtmlStaysAuthorControlled:
    def test_author_html_is_kept_but_data_is_still_escaped(self) -> None:
        template = EmailTemplate(
            subject="s",
            body="<p>Hi <b>{{ name }}</b></p>",
            body_format=BodyFormat.RICH,
        )
        html = template.render(
            Contact(email="a@b.com", fields={"name": "<img src=x onerror=alert(1)>"})
        ).html
        assert "<b>" in html, "the author's own markup is intentional"
        # The payload survives as visible text, but never as a live tag.
        assert "<img" not in html, "contact data must never become markup"
        assert "&lt;img" in html


class TestUploadLimits:
    async def test_unsupported_file_type_is_refused(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/api/contacts/upload",
            headers=auth(),
            files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
        )
        assert response.status_code == 400


def test_contacts_reject_a_file_with_no_addresses(tmp_path: Path) -> None:
    from sahajmails.errors import ContactsError

    bad = tmp_path / "bad.csv"
    bad.write_text("name,city\nAlice,Paris\n")
    with pytest.raises(ContactsError):
        load_contacts(bad)
