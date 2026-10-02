"""Shared fixtures.

Transport tests run against a real SMTP server on a loopback port (see
:mod:`tests.smtpstub`) rather than a mocked ``smtplib``. A mock happily accepts
things a real server rejects, and the behaviour worth testing — dropped
connections, 4xx versus 5xx, auth refusal — only exists at the protocol level.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sahajmails.config import Settings
from sahajmails.contacts import ContactList, load_contacts
from sahajmails.models import Contact
from sahajmails.presets import Security
from sahajmails.storage.db import Database
from sahajmails.storage.repo import Repository

from .smtpstub import SMTPStub

TEST_USER = "sender@example.com"
TEST_PASSWORD = "correct horse battery staple"


@pytest.fixture
def smtp() -> Iterator[SMTPStub]:
    with SMTPStub(username=TEST_USER, password=TEST_PASSWORD) as stub:
        yield stub


@pytest.fixture
def settings(smtp: SMTPStub) -> Settings:
    return Settings(
        sender_email=TEST_USER,
        sender_name="Test Sender",
        provider="custom",
        smtp_host=smtp.host,
        smtp_port=smtp.port,
        smtp_username=TEST_USER,
        smtp_password=TEST_PASSWORD,
        security=Security.NONE,
        rate_per_minute=60_000,  # effectively unthrottled, so tests stay fast
        concurrency=1,
    )


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "test.db")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def repo(db: Database) -> Repository:
    return Repository(db)


@pytest.fixture
def contact() -> Contact:
    return Contact(
        email="alice@example.com",
        fields={"email": "alice@example.com", "first_name": "Alice", "company": "Acme"},
        row=2,
    )


def make_csv(path: Path, rows: int, *, prefix: str = "user") -> Path:
    """Write a contact CSV with ``rows`` distinct addresses."""
    lines = ["email,first_name,company"]
    lines += [f"{prefix}{i}@example.com,Person{i},Company{i}" for i in range(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def contacts_50(tmp_path: Path) -> ContactList:
    return load_contacts(make_csv(tmp_path / "contacts.csv", 50))
