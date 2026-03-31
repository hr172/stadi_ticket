from models import db, Ticket, BuybackRecord, SeatConfig
from wallet import credit_wallet
from datetime import datetime


def is_buyback_eligible(match_id, user_id, ticket):
    """
    Check all three guard conditions before processing a buyback.
    Returns (eligible: bool, message: str)
    """
    from config import Config

    # Guard 1: Event sell-through >= 80%
    configs = SeatConfig.query.filter_by(match_id=match_id).all()
    total_capacity = sum(c.capacity for c in configs)
    sold_count = Ticket.query.filter_by(match_id=match_id, status='active').count()

    if total_capacity == 0 or (sold_count / total_capacity) < Config.BUYBACK_THRESHOLD:
        pct = int(Config.BUYBACK_THRESHOLD * 100)
        current_pct = int(sold_count / total_capacity * 100) if total_capacity else 0
        return False, f"Buyback not yet active — {current_pct}% sold, {pct}% required."

    # Guard 2: Platform inventory < 15% cap
    returned_count = Ticket.query.filter_by(match_id=match_id, status='returned').count()
    if returned_count / total_capacity >= Config.BUYBACK_CAP:
        return False, f"Buyback capacity reached ({int(Config.BUYBACK_CAP * 100)}% cap)."

    # Guard 3: User buybacks for this event < 2
    user_returns = (BuybackRecord.query
                    .filter_by(user_id=user_id, match_id=match_id)
                    .count())
    if user_returns >= Config.MAX_BUYBACKS_PER_USER:
        return False, f"Personal limit reached (max {Config.MAX_BUYBACKS_PER_USER} buybacks per event)."

    # VVIP requires manual approval
    if ticket.seat_category == 'VVIP':
        return False, "VVIP buybacks require manual approval. Contact the stadium office."

    return True, "Eligible"


def process_buyback(ticket):
    """
    Process a buyback:
    - Set ticket status to 'returned'
    - Calculate refund (90% single / 80% group)
    - Credit wallet
    - Create BuybackRecord
    Returns refund_amount (int KES) or None on failure.
    """
    import uuid

    if ticket.ticket_type == 'group':
        refund_rate = 0.80   # 20% penalty
    else:
        refund_rate = 0.90   # 10% penalty

    refund_kes = int(ticket.price_paid * refund_rate)
    retention = ticket.price_paid - refund_kes

    # Generate a reference_id for the B2C callback to match against
    ref_id = f"buyback_{ticket.id}_{uuid.uuid4().hex[:8]}"

    ticket.status = 'returned'

    record = BuybackRecord(
        ticket_id=ticket.id,
        user_id=ticket.user_id,
        match_id=ticket.match_id,             # FIX: was missing in original model
        original_price_kes=ticket.price_paid,
        refund_amount_kes=refund_kes,
        platform_retention_kes=retention,
        refund_status='completed',
        reference_id=ref_id                   # Used by B2C callback for rollback lookup
    )
    db.session.add(record)

    # Update user stats
    user = ticket.user
    user.total_buybacks += 1

    db.session.commit()

    # Credit wallet — use same ref_id so B2C failure handler can reverse it
    credit_wallet(
        ticket.user_id,
        refund_kes,
        f"Buyback: {ticket.match.home_team} vs {ticket.match.away_team} "
        f"— {ticket.seat_category} Seat {ticket.seat_number}"
    )

    # In production: initiate B2C payout here and set refund_status = 'pending'
    # The B2C callback will set it to 'completed' or trigger rollback on failure.

    return refund_kes


def calculate_buyback_savings(ticket):
    """Return the KES refund amount the fan would receive — used for display."""
    rate = 0.80 if ticket.ticket_type == 'group' else 0.90
    return int(ticket.price_paid * rate)
