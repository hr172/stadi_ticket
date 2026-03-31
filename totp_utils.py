import pyotp
import qrcode
from io import BytesIO
import base64
import time


def generate_totp_secret():
    """Generate a cryptographically random base32 TOTP secret."""
    return pyotp.random_base32()


def get_totp_uri(secret, account_name="Fan", issuer_name="StadiSoka"):
    """Generate provisioning URI for authenticator app QR code."""
    return pyotp.TOTP(secret).provisioning_uri(
        name=account_name,
        issuer_name=issuer_name
    )


def verify_totp(secret, code, valid_window=1):
    """
    Verify a 6-digit TOTP code with ±1 window (90-second effective acceptance).
    valid_window=1 tolerates one 30-second step in either direction, covering
    realistic clock drift between fan device and gate terminal.
    """
    totp = pyotp.TOTP(secret)
    return totp.verify(str(code), valid_window=valid_window)


def get_current_totp_code(secret):
    """Return the current 6-digit TOTP code for a ticket secret."""
    return pyotp.TOTP(secret).now()


def get_totp_seconds_remaining():
    """Return seconds until the current 30-second TOTP window expires."""
    return 30 - (int(time.time()) % 30)


def generate_qr_base64(uri):
    """Generate QR code as a base64-encoded PNG data URI for HTML embedding."""
    qr = qrcode.make(uri)
    buf = BytesIO()
    qr.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
