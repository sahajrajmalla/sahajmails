"""Send orchestration: the ledger, resume, retries, and pacing."""

from __future__ import annotations

from pathlib import Path

import pytest

from sahajmails.config import Settings
from sahajmails.contacts import ContactList
from sahajmails.errors import AuthenticationError
from sahajmails.models import SendResult
from sahajmails.sender import BulkSender, Progress, SendOptions
from sahajmails.storage.repo import Repository
from sahajmails.template import EmailTemplate

from .smtpstub import SMTPStub


def make_sender(
    contacts: ContactList,
    settings: Settings,
    repo: Repository,
    **options: object,
) -> BulkSender:
    return BulkSender(
        template=EmailTemplate(subject="Hello {{ first_name }}", body="Hi {{ first_name }}."),
        contacts=contacts,
        settings=settings,
        repo=repo,
        options=SendOptions(rate_per_minute=6000, max_retries=0, **options),  # type: ignore[arg-type]
    )


class TestBasicSend:
    def test_delivers_to_everyone(
        self,
        contacts_50: ContactList,
        settings: Settings,
        repo: Repository,
        smtp: SMTPStub,
    ) -> None:
        report = make_sender(contacts_50, settings, repo).send()

        assert len(report.sent) == 50
        assert report.ok
        assert smtp.count == 50
        assert len(smtp.unique_recipients()) == 50

    def test_personalizes_each_message(
        self,
        contacts_50: ContactList,
        settings: Settings,
        repo: Repository,
        smtp: SMTPStub,
    ) -> None:
        make_sender(contacts_50, settings, repo).send()

        subjects = {str(m["Subject"]) for m in smtp.messages}
        assert len(subjects) == 50
        assert "Hello Person0" in subjects

    def test_every_message_is_multipart_alternative(
        self,
        contacts_50: ContactList,
        settings: Settings,
        repo: Repository,
        smtp: SMTPStub,
    ) -> None:
        make_sender(contacts_50, settings, repo, limit=3).send()

        for message in smtp.messages:
            types = {part.get_content_type() for part in message.walk()}
            assert "text/plain" in types, "HTML-only mail is a major spam signal"
            assert "text/html" in types

    def test_records_every_attempt_in_the_ledger(
        self, contacts_50: ContactList, settings: Settings, repo: Repository
    ) -> None:
        report = make_sender(contacts_50, settings, repo).send()

        rows = repo.run_results(report.run_id)
        assert len(rows) == 50
        assert repo.run_counts(report.run_id) == {"sent": 50}


class TestResume:
    """The guarantee: a killed run resumes without ever double-sending."""

    def test_resume_after_crash_sends_each_contact_exactly_once(
        self,
        contacts_50: ContactList,
        settings: Settings,
        repo: Repository,
        smtp: SMTPStub,
    ) -> None:
        first = make_sender(contacts_50, settings, repo)

        # Simulate the laptop lid closing at message 25.
        def stop_at_25(result: SendResult) -> None:
            if len(repo.already_sent(run_id)) >= 25:
                first.cancel()

        run_id = repo.create_run(total=50)
        first.send(run_id=run_id, on_result=stop_at_25)

        partial = repo.already_sent(run_id)
        assert 25 <= len(partial) < 50, "test setup should stop partway through"
        assert smtp.count == len(partial)

        # A completely fresh sender, resuming the same run.
        second = make_sender(contacts_50, settings, repo)
        second.send(run_id=run_id)

        assert smtp.count == 50
        assert smtp.duplicates() == [], "a resumed run must never re-send"
        assert len(smtp.unique_recipients()) == 50
        assert len(repo.already_sent(run_id)) == 50

    def test_resuming_a_finished_run_sends_nothing(
        self,
        contacts_50: ContactList,
        settings: Settings,
        repo: Repository,
        smtp: SMTPStub,
    ) -> None:
        report = make_sender(contacts_50, settings, repo).send()
        assert smtp.count == 50

        make_sender(contacts_50, settings, repo).send(run_id=report.run_id)

        assert smtp.count == 50, "nothing left to do means nothing sent"


class TestFailureHandling:
    def test_permanent_rejection_is_not_retried(
        self,
        contacts_50: ContactList,
        settings: Settings,
        repo: Repository,
        smtp: SMTPStub,
    ) -> None:
        smtp.reject_recipients = {"user3@example.com": 550}

        sender = BulkSender(
            template=EmailTemplate(subject="s", body="b"),
            contacts=contacts_50,
            settings=settings,
            repo=repo,
            options=SendOptions(rate_per_minute=6000, max_retries=3, retry_delay=0.01, limit=5),
        )
        report = sender.send()

        failed = report.failed
        assert len(failed) == 1
        assert failed[0].email == "user3@example.com"
        # One attempt only: a 5xx means the mailbox is gone, so retrying it
        # burns quota and hurts sender reputation.
        assert smtp.count == 4

    def test_one_bad_address_does_not_stop_the_batch(
        self,
        contacts_50: ContactList,
        settings: Settings,
        repo: Repository,
        smtp: SMTPStub,
    ) -> None:
        smtp.reject_recipients = {"user10@example.com": 550}

        report = make_sender(contacts_50, settings, repo).send()

        assert len(report.results) == 50
        assert len(report.sent) == 49
        assert len(report.failed) == 1
        assert report.failed[0].email == "user10@example.com"

    def test_bad_credentials_raise_before_anything_is_sent(
        self,
        contacts_50: ContactList,
        settings: Settings,
        repo: Repository,
        smtp: SMTPStub,
    ) -> None:
        # A rejected password is a configuration problem, not a delivery
        # outcome. Raising beats returning an empty report the caller could
        # mistake for success.
        settings.smtp_password = "wrong"

        with pytest.raises(AuthenticationError) as caught:
            make_sender(contacts_50, settings, repo).send()

        assert smtp.count == 0
        assert caught.value.hint, "an auth failure must tell the user what to do"

    def test_failures_midway_preserve_the_successful_results(
        self,
        contacts_50: ContactList,
        settings: Settings,
        repo: Repository,
        smtp: SMTPStub,
    ) -> None:
        # The other half of the rule: once real deliveries exist, they matter
        # more than the exception, so the run reports rather than raising.
        sent_before_failure = 5

        def break_the_server(result: SendResult) -> None:
            if smtp.count >= sent_before_failure:
                smtp.reject_with = (550, "mailbox unavailable")

        report = make_sender(contacts_50, settings, repo).send(on_result=break_the_server)

        assert len(report.sent) == sent_before_failure
        assert len(report.failed) == 50 - sent_before_failure
        assert len(report.results) == 50, "every contact gets a recorded outcome"

        # And the ledger agrees, so nothing is lost on a later resume.
        assert repo.run_counts(report.run_id) == {
            "sent": sent_before_failure,
            "failed": 50 - sent_before_failure,
        }

    def test_reconnects_after_the_provider_drops_the_connection(
        self,
        contacts_50: ContactList,
        settings: Settings,
        repo: Repository,
        smtp: SMTPStub,
    ) -> None:
        # Gmail closes long-lived connections; 1.x abandoned the whole batch.
        smtp.disconnect_after = 10

        sender = BulkSender(
            template=EmailTemplate(subject="s", body="b"),
            contacts=contacts_50,
            settings=settings,
            repo=repo,
            options=SendOptions(rate_per_minute=6000, max_retries=2, retry_delay=0.01, limit=20),
        )
        report = sender.send()

        assert len(report.sent) >= 19, f"expected recovery, got {report.summary()}"


class TestProgress:
    def test_fraction_never_exceeds_one(self) -> None:
        # 1.x divided by the DataFrame index label, so a filtered CSV whose
        # labels were [0, 2] produced progress(1.5) and crashed the UI.
        progress = Progress(total=2)
        progress.sent = 2
        assert progress.fraction == 1.0

        progress.sent = 99
        assert progress.fraction == 1.0

    def test_zero_contacts_is_not_a_division_error(self) -> None:
        assert Progress(total=0).fraction == 1.0

    def test_reports_eta_once_underway(self) -> None:
        progress = Progress(total=10)
        assert progress.eta_seconds is None
        progress.sent = 5
        assert progress.eta_seconds is not None


class TestDryRun:
    def test_writes_eml_files_and_sends_nothing(
        self,
        contacts_50: ContactList,
        settings: Settings,
        repo: Repository,
        smtp: SMTPStub,
        tmp_path: Path,
    ) -> None:
        outdir = tmp_path / "outbox"
        sender = BulkSender(
            template=EmailTemplate(subject="Hi {{ first_name }}", body="Hello."),
            contacts=contacts_50,
            settings=settings,
            repo=repo,
            options=SendOptions(dry_run=True, outdir=str(outdir), limit=5),
        )
        report = sender.send()

        assert len(report.sent) == 5
        assert smtp.count == 0, "dry run must not touch the network"

        files = sorted(outdir.glob("*.eml"))
        assert len(files) == 5
        assert "Subject: Hi Person0" in files[0].read_text(encoding="utf-8")


class TestSuppressionAndDuplicates:
    def test_recently_mailed_finds_prior_sends(
        self, contacts_50: ContactList, settings: Settings, repo: Repository
    ) -> None:
        make_sender(contacts_50, settings, repo, limit=5).send()

        recent = repo.recently_mailed([c.email for c in contacts_50], days=30)
        assert len(recent) == 5
        assert "user0@example.com" in recent

    def test_filter_out_removes_suppressed_contacts(self, contacts_50: ContactList) -> None:
        remaining = contacts_50.filter_out(["USER1@example.com", "user2@example.com"])
        assert len(remaining) == 48
        assert "user1@example.com" not in remaining.emails()


@pytest.mark.parametrize("workers", [2, 4])
def test_parallel_workers_deliver_each_contact_once(
    contacts_50: ContactList,
    settings: Settings,
    repo: Repository,
    smtp: SMTPStub,
    workers: int,
) -> None:
    sender = BulkSender(
        template=EmailTemplate(subject="s", body="Hi {{ first_name }}"),
        contacts=contacts_50,
        settings=settings,
        repo=repo,
        options=SendOptions(rate_per_minute=6000, concurrency=workers, max_retries=0),
    )
    report = sender.send()

    assert len(report.sent) == 50
    assert smtp.count == 50
    assert smtp.duplicates() == []
    assert len(smtp.unique_recipients()) == 50
