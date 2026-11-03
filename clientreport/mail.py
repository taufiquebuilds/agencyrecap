"""Build and send emails over SMTP."""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_email: str


def build_message(*, from_email: str, to: list[str], subject: str, text: str, html: str | None = None,
                  reply_to: str | None = None, attachment: bytes | None = None,
                  filename: str = "report.pdf") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    if attachment is not None:
        msg.add_attachment(attachment, maintype="application", subtype="pdf", filename=filename)
    return msg


def send(message: EmailMessage, config: SmtpConfig, smtp_factory=None) -> None:
    if not config.host:
        raise OSError("SMTP_HOST is not set, so email cannot be sent")
    factory = smtp_factory or (smtplib.SMTP_SSL if config.port == 465 else smtplib.SMTP)
    with factory(config.host, config.port, timeout=30) as smtp:
        if config.port == 587:
            smtp.starttls()
        if config.user:
            smtp.login(config.user, config.password)
        smtp.send_message(message)
