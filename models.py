from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timedelta

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    role = db.Column(db.String(20), default='fan')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Speculation detection stats
    total_purchases = db.Column(db.Integer, default=0)
    total_buybacks = db.Column(db.Integer, default=0)
    total_attended = db.Column(db.Integer, default=0)

    # Relationships
    wallet = db.relationship('Wallet', backref='user', uselist=False)
    tickets = db.relationship('Ticket', backref='user', lazy=True)
    buybacks = db.relationship('BuybackRecord', backref='user', lazy=True)

    @property
    def buyback_rate(self):
        if self.total_purchases == 0:
            return 0.0
        return self.total_buybacks / self.total_purchases

    @property
    def attendance_rate(self):
        if self.total_purchases == 0:
            return 0.0
        return self.total_attended / self.total_purchases

    @property
    def buyback_rate_pct(self):
        return round(self.buyback_rate * 100, 1)

    @property
    def attendance_rate_pct(self):
        return round(self.attendance_rate * 100, 1)


class Match(db.Model):
    __tablename__ = 'matches'

    id = db.Column(db.Integer, primary_key=True)
    home_team = db.Column(db.String(100), nullable=False)
    away_team = db.Column(db.String(100), nullable=False)
    kickoff = db.Column(db.DateTime, nullable=False)
    competition = db.Column(db.String(50), default='KPL')
    venue = db.Column(db.String(100), default='Kasarani Stadium')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    seat_configs = db.relationship('SeatConfig', backref='match', lazy=True,
                                   cascade='all, delete-orphan')
    tickets = db.relationship('Ticket', backref='match', lazy=True)
    seat_holds = db.relationship('SeatHold', backref='match', lazy=True)

    @property
    def total_capacity(self):
        return sum(c.capacity for c in self.seat_configs)

    @property
    def sold_count(self):
        return Ticket.query.filter_by(match_id=self.id, status='active').count()

    @property
    def sell_through_rate(self):
        if self.total_capacity == 0:
            return 0.0
        return self.sold_count / self.total_capacity

    @property
    def sell_through_pct(self):
        return round(self.sell_through_rate * 100, 1)

    @property
    def returned_count(self):
        return Ticket.query.filter_by(match_id=self.id, status='returned').count()

    @property
    def buyback_available(self):
        if self.total_capacity == 0:
            return False
        return (self.sell_through_rate >= 0.80 and
                (self.returned_count / self.total_capacity) < 0.15)


class SeatConfig(db.Model):
    __tablename__ = 'seat_configs'

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey('matches.id'), nullable=False)
    seating_category = db.Column(db.String(20), nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    price_kes = db.Column(db.Integer, nullable=False)
    section_name = db.Column(db.String(50), nullable=True)
    svg_coordinates = db.Column(db.Text, nullable=True)


class Ticket(db.Model):
    __tablename__ = 'tickets'
    # Index on totp_secret for fast gate lookups
    __table_args__ = (
        db.Index('ix_ticket_status_match', 'status', 'match_id'),
        db.Index('ix_ticket_user_status', 'user_id', 'status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey('matches.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    seat_category = db.Column(db.String(20), nullable=False)
    seat_number = db.Column(db.Integer, nullable=False)
    price_paid = db.Column(db.Integer, nullable=False)  # Always integer KES
    totp_secret = db.Column(db.String(200), nullable=False)
    ticket_type = db.Column(db.String(20), default='single')  # 'single' or 'group'
    group_size = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default='active')  # active | returned
    purchased_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    entries = db.relationship('TicketEntry', backref='ticket', lazy=True)
    buyback_record = db.relationship('BuybackRecord', backref='ticket', uselist=False)


class BuybackRecord(db.Model):
    __tablename__ = 'buyback_records'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    # FIX: match_id FK was missing from the original — needed for per-event queries
    match_id = db.Column(db.Integer, db.ForeignKey('matches.id'), nullable=False)
    original_price_kes = db.Column(db.Integer, nullable=False)
    refund_amount_kes = db.Column(db.Integer, nullable=False)
    platform_retention_kes = db.Column(db.Integer, nullable=False)
    refund_status = db.Column(db.String(20), default='pending')  # pending | completed | failed
    reference_id = db.Column(db.String(100), nullable=True)      # M-Pesa ConversationID
    mpesa_transaction_id = db.Column(db.String(100), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Wallet(db.Model):
    __tablename__ = 'wallets'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    balance_kes = db.Column(db.Integer, default=0)  # Always integer — no fractional shillings
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # FIX: backref so WalletTransaction.wallet works
    transactions = db.relationship('WalletTransaction', backref='wallet', lazy=True)


class WalletTransaction(db.Model):
    __tablename__ = 'wallet_transactions'

    id = db.Column(db.Integer, primary_key=True)
    wallet_id = db.Column(db.Integer, db.ForeignKey('wallets.id'), nullable=False)
    amount_kes = db.Column(db.Integer, nullable=False)
    transaction_type = db.Column(db.String(20), nullable=False)  # credit|debit|pending|failed
    description = db.Column(db.String(200))
    reference_id = db.Column(db.String(100))   # M-Pesa checkout request ID
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TicketEntry(db.Model):
    __tablename__ = 'ticket_entries'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False)
    entry_type = db.Column(db.String(20), nullable=False)  # entry | reentry
    gate_id = db.Column(db.Integer, nullable=False)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

    @staticmethod
    def can_reenter(ticket_id, current_time=None):
        """
        Returns (can_enter: bool, reason: str, count: int)
        Reasons: first_entry | reentry_allowed | max_reentries_exceeded | no_initial_entry
        """
        if current_time is None:
            current_time = datetime.now()

        today_start = datetime(current_time.year, current_time.month, current_time.day)
        today_end = today_start + timedelta(days=1)

        entries_today = TicketEntry.query.filter(
            TicketEntry.ticket_id == ticket_id,
            TicketEntry.recorded_at >= today_start,
            TicketEntry.recorded_at < today_end
        ).all()

        if not entries_today:
            return True, 'first_entry', 0

        has_initial = any(e.entry_type == 'entry' for e in entries_today)
        reentry_count = sum(1 for e in entries_today if e.entry_type == 'reentry')
        max_reentries = 1

        if not has_initial:
            return False, 'no_initial_entry', 0
        if reentry_count < max_reentries:
            return True, 'reentry_allowed', reentry_count + 1
        return False, 'max_reentries_exceeded', reentry_count


class SeatHold(db.Model):
    __tablename__ = 'seat_holds'

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey('matches.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    category = db.Column(db.String(20), nullable=False)
    seat_number = db.Column(db.Integer, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(50), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=True)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
