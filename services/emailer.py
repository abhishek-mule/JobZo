"""Email transports — approved messages only.

Two transports:
  OutboxEmailSender  default. Writes a real .eml to logs/outbox/ and does
                     NOT claim the message was sent. The user can review or
                     send it manually.
  SMTPEmailSender    active only when SMTP env vars are configured. Works
                     with Gmail app passwords (smtp.gmail.com:587).

The pipeline never calls a transport before an ApprovalRequest is approved.
"""

from __future__ import annotations

import hashlib
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path


@dataclass
class SendResult:
    ok: bool
    transport: str
    dry_run: bool = False
    message_id: str = ""
    path: str = ""
    error: str = ""


class OutboxEmailSender:
    name = "outbox"

    def __init__(self, directory: Path | None = None):
        self.directory = directory or Path(
            os.environ.get("JOBZO_OUTBOX_DIR",
                           str(Path(__file__).parent.parent / "logs" / "outbox"))
        )

    def send(self, *, to: str, subject: str, body: str,
             from_name: str = "", from_email: str = "") -> SendResult:
        if not to or "@" not in to:
            return SendResult(ok=False, transport=self.name, error="invalid recipient")
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        digest = hashlib.sha256(f"{to}:{subject}:{body}".encode()).hexdigest()[:8]
        path = self.directory / f"{stamp}-{digest}.eml"

        message = EmailMessage()
        message["To"] = to
        message["From"] = f"{from_name} <{from_email}>" if from_email else (from_email or "")
        message["Subject"] = subject
        message["Date"] = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
        message.set_content(body)
        path.write_bytes(bytes(message))

        return SendResult(
            ok=True,
            transport=self.name,
            dry_run=True,
            message_id=f"outbox:{path.name}",
            path=str(path),
        )


class SMTPEmailSender:
    name = "smtp"

    def __init__(self, host: str, port: int = 587, user: str = "",
                 password: str = "", from_email: str = "", use_ssl: bool = False):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_email = from_email
        self.use_ssl = use_ssl

    def send(self, *, to: str, subject: str, body: str,
             from_name: str = "", from_email: str = "") -> SendResult:
        sender = from_email or self.from_email
        if not sender:
            return SendResult(ok=False, transport=self.name, error="no sender address")
        if not to or "@" not in to:
            return SendResult(ok=False, transport=self.name, error="invalid recipient")

        message = EmailMessage()
        message["To"] = to
        message["From"] = f"{from_name} <{sender}>" if from_name else sender
        message["Subject"] = subject
        message.set_content(body)

        try:
            if self.use_ssl:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=30) as server:
                    if self.user:
                        server.login(self.user, self.password)
                    server.send_message(message)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                    server.ehlo()
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                    if self.user:
                        server.login(self.user, self.password)
                    server.send_message(message)
            return SendResult(ok=True, transport=self.name,
                              message_id=message.get("Message-ID", "") or "smtp:sent")
        except Exception as exc:
            return SendResult(ok=False, transport=self.name, error=str(exc))


def get_sender():
    """Return SMTP when configured, otherwise the safe local outbox."""
    host = os.environ.get("JOBZO_SMTP_HOST", "").strip()
    if not host:
        return OutboxEmailSender()

    from services.user_profile import load_profile
    profile = load_profile()
    return SMTPEmailSender(
        host=host,
        port=int(os.environ.get("JOBZO_SMTP_PORT", "587")),
        user=os.environ.get("JOBZO_SMTP_USER", ""),
        password=os.environ.get("JOBZO_SMTP_PASSWORD", ""),
        from_email=os.environ.get("JOBZO_SMTP_FROM", profile.get("email", "")),
        use_ssl=os.environ.get("JOBZO_SMTP_SSL", "0") == "1",
    )
