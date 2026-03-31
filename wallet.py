import math
import uuid
from models import db, Wallet, WalletTransaction


def init_wallet(user_id):
    wallet = Wallet.query.filter_by(user_id=user_id).first()
    if not wallet:
        wallet = Wallet(user_id=user_id, balance_kes=0)
        db.session.add(wallet)
        db.session.commit()
    return wallet


def get_wallet_balance(user_id):
    wallet = Wallet.query.filter_by(user_id=user_id).first()
    return wallet.balance_kes if wallet else 0


def credit_wallet(user_id, amount_kes, description):
    """Credit KES to a fan's wallet. All amounts are integer shillings."""
    wallet = Wallet.query.filter_by(user_id=user_id).first()
    if not wallet:
        wallet = init_wallet(user_id)

    wallet.balance_kes += amount_kes
    tx = WalletTransaction(
        wallet_id=wallet.id,
        amount_kes=amount_kes,
        transaction_type='credit',
        description=description
    )
    db.session.add(tx)
    db.session.commit()
    return True


def debit_wallet(user_id, amount_kes, description):
    """Debit KES from a fan's wallet atomically. Returns False if insufficient balance."""
    wallet = Wallet.query.filter_by(user_id=user_id).first()
    if not wallet or wallet.balance_kes < amount_kes:
        return False

    wallet.balance_kes -= amount_kes
    tx = WalletTransaction(
        wallet_id=wallet.id,
        amount_kes=amount_kes,
        transaction_type='debit',
        description=description
    )
    db.session.add(tx)
    db.session.commit()
    return True


def process_withdrawal(user_id, amount_kes):
    """
    Process a fan withdrawal to M-Pesa.

    FIX: The original code deducted (amount + fee) from the wallet, meaning the fan
    paid the fee on top of their requested amount — incorrect. The correct behaviour:
    - Fan requests withdrawal of X KES
    - Platform deducts X from wallet
    - Fan receives (X - fee) KES on M-Pesa
    - Platform retains fee KES as revenue
    """
    from config import Config

    if amount_kes <= 0:
        return False, "Withdrawal amount must be greater than zero"

    wallet = Wallet.query.filter_by(user_id=user_id).first()
    if not wallet or wallet.balance_kes < amount_kes:
        return False, f"Insufficient balance. Available: KES {wallet.balance_kes if wallet else 0}"

    fee = math.floor(amount_kes * Config.WITHDRAWAL_FEE_PERCENT / 100)
    payout = amount_kes - fee  # Amount fan actually receives on M-Pesa

    # Debit full requested amount from wallet
    if not debit_wallet(user_id, amount_kes, f"Withdrawal KES {amount_kes} (fee KES {fee})"):
        return False, "Withdrawal failed — please try again"

    # In production: call B2C API here with payout amount
    # mpesa.b2c_payment(user.phone, payout, str(uuid.uuid4()))

    return True, f"KES {payout} sent to M-Pesa. Platform fee: KES {fee}."


def request_mpesa_topup(user_id, amount_kes, phone_number):
    """
    Initiate M-Pesa STK Push for wallet top-up.

    FIX: Original used threading.Thread with db.session.begin() inside the thread,
    which crashes because SQLAlchemy sessions are not thread-safe and Flask's app
    context doesn't carry into new threads automatically.

    For the demo/sandbox we use a simulated synchronous callback instead.
    In production this would be a real STK Push with the callback handled by
    /mpesa/callback/stkpush.
    """
    from flask import current_app

    wallet = Wallet.query.filter_by(user_id=user_id).first()
    if not wallet:
        wallet = init_wallet(user_id)

    ref_id = str(uuid.uuid4())

    # Record pending transaction
    tx = WalletTransaction(
        wallet_id=wallet.id,
        amount_kes=amount_kes,
        transaction_type='pending',
        description='M-Pesa top-up request pending',
        reference_id=ref_id
    )
    db.session.add(tx)
    db.session.commit()

    # Sandbox: simulate immediate success synchronously (no threads, no context issues)
    if current_app.config.get('MPESA_ENV', 'sandbox') == 'sandbox':
        _simulate_topup_success(tx.id, wallet.id, amount_kes)
        return True, f"Sandbox: KES {amount_kes} credited to wallet immediately."

    # Production: STK Push is async; confirmation comes via callback endpoint
    return True, f"STK Push sent to {phone_number}. Enter PIN to complete top-up."


def _simulate_topup_success(tx_id, wallet_id, amount_kes):
    """Synchronous sandbox callback simulation — no threads, no app context issues."""
    tx = WalletTransaction.query.get(tx_id)
    wallet = Wallet.query.get(wallet_id)
    if tx and wallet and tx.transaction_type == 'pending':
        tx.transaction_type = 'credit'
        tx.description = f'M-Pesa top-up KES {amount_kes} — sandbox confirmed'
        wallet.balance_kes += amount_kes
        db.session.commit()


def process_mpesa_callback(reference_id, success, amount=None):
    """Process M-Pesa STK Push callback from Safaricom."""
    tx = WalletTransaction.query.filter_by(reference_id=reference_id).first()
    if not tx:
        return False

    if success and amount:
        tx.transaction_type = 'credit'
        tx.description = f'M-Pesa payment confirmed — KES {amount}'
        wallet = Wallet.query.get(tx.wallet_id)
        wallet.balance_kes += amount
        db.session.commit()
        return True

    tx.transaction_type = 'failed'
    tx.description = 'M-Pesa payment failed or cancelled'
    db.session.commit()
    return False
