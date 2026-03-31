from flask import Blueprint, request, jsonify
from models import db, WalletTransaction, BuybackRecord, Ticket, User, AuditLog
from wallet import credit_wallet
from datetime import datetime

mpesa_bp = Blueprint('mpesa', __name__)


@mpesa_bp.route('/mpesa/callback/stkpush', methods=['POST'])
def stk_push_callback():
    """Handle STK Push payment confirmation from Safaricom."""
    data = request.json or {}

    stk_callback = data.get('Body', {}).get('stkCallback', {})
    result_code = stk_callback.get('ResultCode')
    checkout_request_id = stk_callback.get('CheckoutRequestID')
    callback_metadata = stk_callback.get('CallbackMetadata', {})

    # Extract confirmed amount from metadata items
    amount = None
    for item in callback_metadata.get('Item', []):
        if item.get('Name') == 'Amount':
            amount = int(item.get('Value', 0))
            break

    pending_tx = WalletTransaction.query.filter_by(
        reference_id=checkout_request_id,
        transaction_type='pending'
    ).first()

    if result_code == '0' and pending_tx and amount:
        # FIX: use pending_tx.wallet (backref now defined on Wallet model)
        wallet = pending_tx.wallet
        wallet.balance_kes += amount
        pending_tx.transaction_type = 'credit'
        pending_tx.description = f'M-Pesa STK Push confirmed — KES {amount}'
        db.session.commit()
    elif pending_tx:
        pending_tx.transaction_type = 'failed'
        pending_tx.description = f'M-Pesa STK Push failed — code {result_code}'
        db.session.commit()

    return jsonify({'ResultCode': 0, 'ResultDesc': 'Accepted'})


@mpesa_bp.route('/mpesa/callback/b2c', methods=['POST'])
def b2c_callback():
    """
    Handle B2C payment result from Safaricom.
    On failure: ticket is restored to active, buyback count is decremented.
    FIX: removed duplicate `ticket.status = 'active'` line from original.
    """
    data = request.json or {}
    result = data.get('Result', {})
    result_code = result.get('ResultCode')
    conversation_id = result.get('ConversationID')
    transaction_id = result.get('TransactionID')

    buyback = BuybackRecord.query.filter_by(reference_id=conversation_id).first()
    if not buyback:
        return jsonify({'ResultCode': 0, 'ResultDesc': 'No matching record'})

    if result_code == '0':  # Success
        buyback.refund_status = 'completed'
        buyback.mpesa_transaction_id = transaction_id
        db.session.add(AuditLog(
            event_type='b2c_success',
            user_id=buyback.user_id,
            ticket_id=buyback.ticket_id,
            details=f'B2C payment completed: {transaction_id}'
        ))
        db.session.commit()

    else:  # Failure — full rollback
        error_msg = result.get('ResultDesc', 'Unknown B2C error')

        # Restore ticket to active
        ticket = Ticket.query.get(buyback.ticket_id)
        if ticket:
            ticket.status = 'active'  # FIX: was duplicated in original

        # Reverse the wallet credit that was given optimistically
        wallet_credit = WalletTransaction.query.filter_by(
            reference_id=f'buyback_{buyback.id}'
        ).first()
        if wallet_credit:
            from wallet import debit_wallet
            debit_wallet(buyback.user_id, buyback.refund_amount_kes,
                         f'Buyback rollback — B2C failed: {error_msg}')

        # Decrement user buyback counter
        user = User.query.get(buyback.user_id)
        if user and user.total_buybacks > 0:
            user.total_buybacks -= 1

        buyback.refund_status = 'failed'
        buyback.error_message = error_msg

        db.session.add(AuditLog(
            event_type='buyback_rollback',
            user_id=buyback.user_id,
            ticket_id=buyback.ticket_id,
            details=f'B2C failed: {error_msg}. Ticket restored to active.'
        ))
        db.session.commit()

    return jsonify({'ResultCode': 0, 'ResultDesc': 'Processed'})


@mpesa_bp.route('/mpesa/callback/timeout', methods=['POST'])
def timeout_callback():
    """Handle STK Push timeout — user did not enter PIN in time."""
    data = request.json or {}
    checkout_request_id = data.get('CheckoutRequestID')

    pending_tx = WalletTransaction.query.filter_by(
        reference_id=checkout_request_id,
        transaction_type='pending'
    ).first()

    if pending_tx:
        pending_tx.transaction_type = 'failed'
        pending_tx.description = 'M-Pesa STK Push timed out — PIN not entered'
        db.session.commit()

    return jsonify({'ResultCode': 0, 'ResultDesc': 'Timeout processed'})
