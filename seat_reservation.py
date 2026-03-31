from models import db, SeatHold, Ticket
from datetime import datetime, timedelta
from config import Config


def hold_seat(match_id, user_id, category, seat_number):
    """
    Create a 10-minute exclusive hold on a seat for a specific user.
    Returns the SeatHold object, or None if the seat is no longer available.
    """
    if not is_seat_available(match_id, category, seat_number):
        return None

    expires_at = datetime.now() + timedelta(minutes=Config.SEAT_HOLD_MINUTES)
    hold = SeatHold(
        match_id=match_id,
        user_id=user_id,
        category=category,
        seat_number=seat_number,
        expires_at=expires_at
    )
    db.session.add(hold)
    db.session.commit()
    return hold


def is_seat_available(match_id, category, seat_number):
    """
    Return True if a seat is neither sold nor currently held by any user.
    """
    sold = Ticket.query.filter_by(
        match_id=match_id,
        seat_category=category,
        seat_number=seat_number,
        status='active'
    ).first()
    if sold:
        return False

    hold = (SeatHold.query
            .filter_by(match_id=match_id, category=category, seat_number=seat_number)
            .filter(SeatHold.expires_at > datetime.now())
            .first())
    if hold:
        return False

    return True


def release_hold(hold_id):
    """Manually release a seat hold before it expires."""
    hold = SeatHold.query.get(hold_id)
    if hold:
        db.session.delete(hold)
        db.session.commit()
        return True
    return False


def get_hold_expiry(hold_id):
    """Return the expiration datetime of a hold, or None if not found."""
    hold = SeatHold.query.get(hold_id)
    return hold.expires_at if hold else None


def cleanup_expired_holds():
    """
    Delete all SeatHold records whose expires_at is in the past.
    Called by the cleanup_worker every 60 seconds.
    Returns the count of released holds.
    """
    expired = SeatHold.query.filter(SeatHold.expires_at < datetime.now()).all()
    count = len(expired)
    for hold in expired:
        db.session.delete(hold)
    if count:
        db.session.commit()
    return count
