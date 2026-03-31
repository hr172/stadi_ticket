import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'stadi-soka-dev-secret-change-in-production'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///stadi_soka.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # M-Pesa Daraja
    MPESA_ENV = os.environ.get('MPESA_ENV', 'sandbox')   # 'sandbox' | 'production'
    MPESA_CONSUMER_KEY = os.environ.get('MPESA_CONSUMER_KEY', '')
    MPESA_CONSUMER_SECRET = os.environ.get('MPESA_CONSUMER_SECRET', '')
    MPESA_SHORTCODE = os.environ.get('MPESA_SHORTCODE', '174379')
    MPESA_PASSKEY = os.environ.get('MPESA_PASSKEY', '')
    MPESA_CALLBACK_URL = os.environ.get('MPESA_CALLBACK_URL', 'https://your-domain.com/mpesa/callback')

    # Platform economics (all integer percent or rate)
    BUYBACK_THRESHOLD = 0.80        # 80% sell-through required to activate buyback
    BUYBACK_CAP = 0.15              # Max 15% of inventory can be returned
    MAX_BUYBACKS_PER_USER = 2       # Per event
    WITHDRAWAL_FEE_PERCENT = 2      # 2% platform fee on wallet withdrawals
    RESALE_PREMIUM = 1.10           # Returned tickets relisted at 110%
    SEAT_HOLD_MINUTES = 10          # Interactive map seat hold duration

    # Internal
    CLEANUP_SECRET = os.environ.get('CLEANUP_SECRET', 'cleanup-secret')
