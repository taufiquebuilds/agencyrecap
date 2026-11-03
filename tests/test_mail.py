import smtplib
import ssl

import pytest

from clientreport.mail import SmtpConfig, build_message, send


def test_build_message_with_pdf():
    msg = build_message(
        from_email="reports@agency.com", to=["a@x.com", "b@x.com"], subject="S", text="hello",
        html="<p>hello</p>", reply_to="me@agency.com", attachment=b"%PDF-1.7 fake", filename="acme-2026-10.pdf",
    )
    assert msg["To"] == "a@x.com, b@x.com"
    assert msg["Reply-To"] == "me@agency.com"
    assert msg.get_body(("plain",)).get_content().strip() == "hello"
    attachment = next(msg.iter_attachments())
    assert attachment.get_filename() == "acme-2026-10.pdf"
    assert attachment.get_content() == b"%PDF-1.7 fake"


class FakeSMTP:
    instances: list["FakeSMTP"] = []
    offers_starttls = True  # whether the server advertises STARTTLS after EHLO

    def __init__(self, host, port, timeout, context=None):
        self.host, self.port, self.context, self.calls = host, port, context, []
        self.starttls_context = None
        self.tls = context is not None  # implicit TLS (port 465) is already encrypted
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def has_extn(self, name):
        return name.lower() == "starttls" and self.offers_starttls

    def starttls(self, context=None):
        if not self.offers_starttls:
            raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")
        self.starttls_context = context
        self.tls = True
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, msg):
        self.calls.append(("send", msg["Subject"]))


class NoStarttlsSMTP(FakeSMTP):
    offers_starttls = False


def message():
    return build_message(from_email="r@a.com", to=["x@y.com"], subject="Hi", text="t")


def test_send_uses_starttls_and_login():
    FakeSMTP.instances.clear()
    send(message(), SmtpConfig("smtp.example.com", 587, "user", "pw", "r@a.com"), smtp_factory=FakeSMTP)
    assert FakeSMTP.instances[0].calls == ["ehlo", "starttls", "ehlo", ("login", "user"), ("send", "Hi")]


def test_port_587_requires_starttls():
    NoStarttlsSMTP.instances.clear()
    with pytest.raises(smtplib.SMTPNotSupportedError):
        send(message(), SmtpConfig("smtp.example.com", 587, "user", "pw", "r@a.com"), smtp_factory=NoStarttlsSMTP)
    assert ("login", "user") not in NoStarttlsSMTP.instances[0].calls


def test_other_port_upgrades_with_starttls_when_offered():
    FakeSMTP.instances.clear()
    send(message(), SmtpConfig("smtp.example.com", 2525, "user", "pw", "r@a.com"), smtp_factory=FakeSMTP)
    smtp = FakeSMTP.instances[0]
    assert smtp.calls == ["ehlo", "starttls", "ehlo", ("login", "user"), ("send", "Hi")]
    assert isinstance(smtp.starttls_context, ssl.SSLContext)
    assert smtp.starttls_context.verify_mode == ssl.CERT_REQUIRED
    assert smtp.starttls_context.check_hostname is True


def test_other_port_without_tls_refuses_to_send_the_password():
    NoStarttlsSMTP.instances.clear()
    with pytest.raises(OSError, match="does not support TLS"):
        send(message(), SmtpConfig("smtp.example.com", 2525, "user", "pw", "r@a.com"), smtp_factory=NoStarttlsSMTP)
    calls = NoStarttlsSMTP.instances[0].calls
    assert ("login", "user") not in calls
    assert not any(c[0] == "send" for c in calls if isinstance(c, tuple))


def test_other_port_without_tls_and_without_login_still_sends():
    NoStarttlsSMTP.instances.clear()
    send(message(), SmtpConfig("relay.internal", 25, "", "", "r@a.com"), smtp_factory=NoStarttlsSMTP)
    assert NoStarttlsSMTP.instances[0].calls == ["ehlo", ("send", "Hi")]


def test_send_verifies_certificate_on_starttls():
    FakeSMTP.instances.clear()
    msg = build_message(from_email="r@a.com", to=["x@y.com"], subject="Hi", text="t")
    send(msg, SmtpConfig("smtp.example.com", 587, "user", "pw", "r@a.com"), smtp_factory=FakeSMTP)
    context = FakeSMTP.instances[0].starttls_context
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_send_verifies_certificate_on_implicit_tls():
    FakeSMTP.instances.clear()
    msg = build_message(from_email="r@a.com", to=["x@y.com"], subject="Hi", text="t")
    send(msg, SmtpConfig("smtp.example.com", 465, "user", "pw", "r@a.com"), smtp_factory=FakeSMTP)
    context = FakeSMTP.instances[0].context
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_send_without_host_fails():
    msg = build_message(from_email="r@a.com", to=["x@y.com"], subject="Hi", text="t")
    with pytest.raises(OSError, match="SMTP_HOST"):
        send(msg, SmtpConfig("", 587, "", "", "r@a.com"))
