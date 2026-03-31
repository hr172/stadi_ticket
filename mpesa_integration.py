"""
mpesa_integration.py — M-Pesa Daraja API integration wrapper.

In sandbox mode (MPESA_ENV=sandbox): all methods return simulated success responses.
In production mode: replace the stub bodies with real Safaricom Daraja API calls.

Daraja API reference: https://developer.safaricom.co.ke/docs
"""
import base64
import requests
from datetime import datetime


class MpesaIntegration:
    SANDBOX_BASE = 'https://sandbox.safaricom.co.ke'
    PRODUCTION_BASE = 'https://api.safaricom.co.ke'

    def __init__(self, consumer_key, consumer_secret, shortcode, passkey,
                 callback_url, env='sandbox'):
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.shortcode = shortcode
        self.passkey = passkey
        self.callback_url = callback_url
        self.env = env
        self._base = self.SANDBOX_BASE if env == 'sandbox' else self.PRODUCTION_BASE
        self._token = None

    def _get_access_token(self):
        """Fetch OAuth2 access token from Safaricom."""
        if self.env == 'sandbox':
            return 'sandbox_mock_token'

        credentials = base64.b64encode(
            f"{self.consumer_key}:{self.consumer_secret}".encode()
        ).decode()
        resp = requests.get(
            f"{self._base}/oauth/v1/generate?grant_type=client_credentials",
            headers={'Authorization': f'Basic {credentials}'},
            timeout=10
        )
        resp.raise_for_status()
        return resp.json().get('access_token')

    def _password(self):
        """Generate Lipa na M-Pesa password (base64 of shortcode+passkey+timestamp)."""
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        raw = f"{self.shortcode}{self.passkey}{timestamp}"
        return base64.b64encode(raw.encode()).decode(), timestamp

    def stk_push(self, phone_number, amount, account_reference='StadiSoka',
                 transaction_desc='Ticket Purchase'):
        """
        Initiate Lipa na M-Pesa STK Push.
        Returns dict with keys: success, checkout_request_id, response_code, response_description
        """
        if self.env == 'sandbox':
            return {
                'success': True,
                'checkout_request_id': f"ws_CO_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                'response_code': '0',
                'response_description': 'Success. Request accepted for processing'
            }

        # Production
        password, timestamp = self._password()
        token = self._get_access_token()
        payload = {
            'BusinessShortCode': self.shortcode,
            'Password': password,
            'Timestamp': timestamp,
            'TransactionType': 'CustomerPayBillOnline',
            'Amount': int(amount),
            'PartyA': phone_number,
            'PartyB': self.shortcode,
            'PhoneNumber': phone_number,
            'CallBackURL': f"{self.callback_url}/stkpush",
            'AccountReference': account_reference,
            'TransactionDesc': transaction_desc
        }
        resp = requests.post(
            f"{self._base}/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={'Authorization': f'Bearer {token}'},
            timeout=15
        )
        data = resp.json()
        return {
            'success': data.get('ResponseCode') == '0',
            'checkout_request_id': data.get('CheckoutRequestID'),
            'response_code': data.get('ResponseCode'),
            'response_description': data.get('ResponseDescription')
        }

    def b2c_payment(self, phone_number, amount, transaction_id,
                    remarks='Buyback refund'):
        """
        Initiate B2C payment (platform → fan M-Pesa).
        Returns dict with keys: success, conversation_id, transaction_id, response_code
        """
        if self.env == 'sandbox':
            return {
                'success': True,
                'conversation_id': f"AG_CONV_{transaction_id}",
                'transaction_id': f"AG_TXN_{transaction_id}",
                'response_code': '0'
            }

        # Production
        token = self._get_access_token()
        payload = {
            'InitiatorName': 'StadiSokaAPI',
            'SecurityCredential': '',   # Encrypted initiator password — set up in production
            'CommandID': 'BusinessPayment',
            'Amount': int(amount),
            'PartyA': self.shortcode,
            'PartyB': phone_number,
            'Remarks': remarks,
            'QueueTimeOutURL': f"{self.callback_url}/b2c/timeout",
            'ResultURL': f"{self.callback_url}/b2c",
            'Occasion': transaction_id
        }
        resp = requests.post(
            f"{self._base}/mpesa/b2c/v3/paymentrequest",
            json=payload,
            headers={'Authorization': f'Bearer {token}'},
            timeout=15
        )
        data = resp.json()
        return {
            'success': data.get('ResponseCode') == '0',
            'conversation_id': data.get('ConversationID'),
            'transaction_id': data.get('OriginatorConversationID'),
            'response_code': data.get('ResponseCode')
        }

    def query_stk_status(self, checkout_request_id):
        """Query the status of a pending STK Push request."""
        if self.env == 'sandbox':
            return {'success': True, 'result_code': '0', 'result_desc': 'Success'}

        password, timestamp = self._password()
        token = self._get_access_token()
        payload = {
            'BusinessShortCode': self.shortcode,
            'Password': password,
            'Timestamp': timestamp,
            'CheckoutRequestID': checkout_request_id
        }
        resp = requests.post(
            f"{self._base}/mpesa/stkpushquery/v1/query",
            json=payload,
            headers={'Authorization': f'Bearer {token}'},
            timeout=10
        )
        data = resp.json()
        return {
            'success': data.get('ResultCode') == '0',
            'result_code': data.get('ResultCode'),
            'result_desc': data.get('ResultDesc')
        }


# ── Module-level singleton ────────────────────────────────────────────────────

_mpesa = None


def init_mpesa_from_config(app_config):
    """Initialise the singleton from Flask app config. Call once at startup."""
    global _mpesa
    _mpesa = MpesaIntegration(
        consumer_key=app_config.MPESA_CONSUMER_KEY,
        consumer_secret=app_config.MPESA_CONSUMER_SECRET,
        shortcode=app_config.MPESA_SHORTCODE,
        passkey=app_config.MPESA_PASSKEY,
        callback_url=app_config.MPESA_CALLBACK_URL,
        env=app_config.MPESA_ENV
    )
    return _mpesa


def get_mpesa():
    """Return the initialised MpesaIntegration instance."""
    if _mpesa is None:
        raise RuntimeError("M-Pesa not initialised. Call init_mpesa_from_config() first.")
    return _mpesa


# Convenience wrappers kept for backward compatibility
def stk_push(phone, amount, account_ref='StadiSoka'):
    return get_mpesa().stk_push(phone, amount, account_ref)


def b2c_payment(phone, amount, transaction_id):
    return get_mpesa().b2c_payment(phone, amount, transaction_id)
