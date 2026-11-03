"""Build and send emails over SMTP."""

import smtplib
import ssl
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
    context = ssl.create_default_context()
    implicit_tls = config.port == 465
    factory = smtp_factory or (smtplib.SMTP_SSL if implicit_tls else smtplib.SMTP)
    options = {"context": context} if implicit_tls else {}
    with factory(config.host, config.port, timeout=30, **options) as smtp:
        encrypted = implicit_tls
        if not implicit_tls:
            smtp.ehlo()
            # 587 must upgrade (an error if the server cannot); other ports upgrade when offered
            if config.port == 587 or smtp.has_extn("starttls"):
                smtp.starttls(context=context)
                smtp.ehlo()
                encrypted = True
        if config.user:
            if not encrypted:
                raise OSError("SMTP server does not support TLS; refusing to send the password in clear text.")
            smtp.login(config.user, config.password)
        smtp.send_message(message)
