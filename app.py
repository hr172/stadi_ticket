import os
import uuid
import random
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func

from config import Config
from models import (db, User, Match, SeatConfig, Ticket, BuybackRecord,
                    Wallet, WalletTransaction, TicketEntry, SeatHold, AuditLog)
from wallet import init_wallet, get_wallet_balance, credit_wallet, debit_wallet, process_withdrawal, request_mpesa_topup
from totp_utils import generate_totp_secret, get_totp_uri, verify_totp, generate_qr_base64
from buyback_engine import is_buyback_eligible, process_buyback
from seat_reservation import hold_seat, release_hold, is_seat_available, get_hold_expiry, cleanup_expired_holds
from group_seat_finder import find_adjacent_seats, find_adjacent_clusters_for_map

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Register M-Pesa blueprint
from mpesa_callback import mpesa_bp
app.register_blueprint(mpesa_bp)


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Admin access required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ==================== AUTHENTICATION ====================

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']
        phone = request.form['phone'].strip()

        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return redirect(url_for('register'))

        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            phone=phone,
            role='fan'
        )
        db.session.add(user)
        db.session.commit()
        init_wallet(user.id)

        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('auth/register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('admin_dashboard') if user.role == 'admin' else url_for('dashboard'))

        flash('Invalid username or password.', 'danger')

    return render_template('auth/login.html')


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username, role='admin').first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('admin_dashboard'))

        flash('Invalid admin credentials.', 'danger')

    return render_template('auth/admin_login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('login'))


# ==================== USER DASHBOARD ====================

@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard():
    upcoming_matches = (Match.query
                        .filter(Match.kickoff > datetime.now(), Match.is_active == True)
                        .order_by(Match.kickoff)
                        .all())
    wallet_balance = get_wallet_balance(current_user.id)
    return render_template('user/dashboard.html', matches=upcoming_matches, wallet_balance=wallet_balance)


@app.route('/match/<int:match_id>')
@login_required
def match_detail(match_id):
    match = Match.query.get_or_404(match_id)
    configs = {c.seating_category: c for c in match.seat_configs}
    return render_template('tickets/match_detail.html', match=match, configs=configs)


# ==================== PURCHASE FLOW ====================

@app.route('/purchase/select_type/<int:match_id>', methods=['POST'])
@login_required
def select_purchase_type(match_id):
    ticket_type = request.form.get('ticket_type', 'single')
    session['purchase_type'] = ticket_type
    session['match_id'] = match_id

    if ticket_type == 'group':
        return redirect(url_for('group_select_size', match_id=match_id))
    return redirect(url_for('select_category', match_id=match_id))


@app.route('/purchase/group/size/<int:match_id>', methods=['GET', 'POST'])
@login_required
def group_select_size(match_id):
    if request.method == 'POST':
        session['group_size'] = int(request.form['group_size'])
        return redirect(url_for('select_category', match_id=match_id))
    return render_template('tickets/group/group_size_select.html', match_id=match_id)


@app.route('/purchase/select_category/<int:match_id>', methods=['GET', 'POST'])
@login_required
def select_category(match_id):
    ticket_type = session.get('purchase_type', 'single')
    group_size = session.get('group_size')
    match = Match.query.get_or_404(match_id)

    categories = SeatConfig.query.filter_by(match_id=match_id).all()
    if ticket_type == 'group':
        categories = [c for c in categories if c.seating_category in ('Regular', 'VIP')]

    if request.method == 'POST':
        session['selected_category'] = request.form['category']
        return redirect(url_for('select_mode', match_id=match_id))

    return render_template('tickets/category_select.html', match=match, categories=categories,
                           ticket_type=ticket_type, group_size=group_size)


@app.route('/purchase/select_mode/<int:match_id>')
@login_required
def select_mode(match_id):
    ticket_type = session.get('purchase_type', 'single')
    return render_template('tickets/mode_select.html', match_id=match_id, ticket_type=ticket_type)


@app.route('/purchase/random/single/<int:match_id>')
@login_required
def random_single(match_id):
    category = session.get('selected_category')
    if not category:
        flash('Please select a category first.', 'danger')
        return redirect(url_for('select_category', match_id=match_id))

    config = SeatConfig.query.filter_by(match_id=match_id, seating_category=category).first()
    if not config:
        flash('Category not available.', 'danger')
        return redirect(url_for('select_category', match_id=match_id))

    sold = {t.seat_number for t in Ticket.query.filter_by(match_id=match_id, seat_category=category, status='active').all()}
    held = {h.seat_number for h in SeatHold.query.filter_by(match_id=match_id, category=category).filter(SeatHold.expires_at > datetime.now()).all()}
    unavailable = sold | held

    available = [s for s in range(1, config.capacity + 1) if s not in unavailable]
    if not available:
        flash('No seats available in this category.', 'warning')
        return redirect(url_for('select_category', match_id=match_id))

    # Prefer lower-numbered seats (closer to pitch / centre)
    preferred = available[:max(1, len(available) // 3)]
    chosen_seat = random.choice(preferred)

    hold = hold_seat(match_id, current_user.id, category, chosen_seat)
    if not hold:
        flash('Seat was just taken — please try again.', 'warning')
        return redirect(url_for('select_category', match_id=match_id))

    session['selected_seats'] = [chosen_seat]
    return redirect(url_for('checkout', match_id=match_id))


@app.route('/purchase/random/group/<int:match_id>')
@login_required
def random_group(match_id):
    category = session.get('selected_category')
    group_size = session.get('group_size', 3)

    if not category:
        flash('Please select a category first.', 'danger')
        return redirect(url_for('select_category', match_id=match_id))

    config = SeatConfig.query.filter_by(match_id=match_id, seating_category=category).first()
    if not config:
        flash('Category not available.', 'danger')
        return redirect(url_for('select_category', match_id=match_id))

    sold = {t.seat_number for t in Ticket.query.filter_by(match_id=match_id, seat_category=category, status='active').all()}
    held = {h.seat_number for h in SeatHold.query.filter_by(match_id=match_id, category=category).filter(SeatHold.expires_at > datetime.now()).all()}
    available = [s for s in range(1, config.capacity + 1) if s not in (sold | held)]

    adjacent = find_adjacent_seats(available, group_size)
    if not adjacent:
        flash(f'No {group_size} consecutive seats available in {category}.', 'warning')
        return redirect(url_for('select_category', match_id=match_id))

    for seat in adjacent:
        hold_seat(match_id, current_user.id, category, seat)

    session['selected_seats'] = adjacent
    discount = 0.10 if group_size == 3 else 0.14
    per_seat_price = int(config.price_kes * (1 - discount))

    return render_template('tickets/group/random_result.html',
                           seats=adjacent, group_size=group_size,
                           per_seat_price=per_seat_price,
                           total_price=group_size * per_seat_price,
                           discount_percent=int(discount * 100),
                           config=config, match_id=match_id)


@app.route('/purchase/interactive/single/<int:match_id>')
@login_required
def interactive_single(match_id):
    category = session.get('selected_category')
    config = SeatConfig.query.filter_by(match_id=match_id, seating_category=category).first_or_404()
    return render_template('tickets/single/interactive_map.html', match_id=match_id, config=config)


@app.route('/purchase/interactive/group/<int:match_id>')
@login_required
def interactive_group(match_id):
    category = session.get('selected_category')
    group_size = session.get('group_size', 3)
    config = SeatConfig.query.filter_by(match_id=match_id, seating_category=category).first_or_404()

    sold = {t.seat_number for t in Ticket.query.filter_by(match_id=match_id, seat_category=category, status='active').all()}
    held = {h.seat_number for h in SeatHold.query.filter_by(match_id=match_id, category=category).filter(SeatHold.expires_at > datetime.now()).all()}
    available = [s for s in range(1, config.capacity + 1) if s not in (sold | held)]
    clusters = find_adjacent_clusters_for_map(available, group_size)

    return render_template('tickets/group/interactive_map.html', match_id=match_id,
                           config=config, clusters=clusters, group_size=group_size)


# ==================== SEAT API ====================

@app.route('/api/seats/<int:match_id>/<string:category>')
@login_required
def get_seat_grid(match_id, category):
    config = SeatConfig.query.filter_by(match_id=match_id, seating_category=category).first()
    if not config:
        return jsonify({'error': 'Category not found'}), 404

    sold = {t.seat_number for t in Ticket.query.filter_by(match_id=match_id, seat_category=category, status='active').all()}
    all_holds = SeatHold.query.filter_by(match_id=match_id, category=category).filter(SeatHold.expires_at > datetime.now()).all()
    held = {h.seat_number for h in all_holds}
    held_by_user = {h.seat_number for h in all_holds if h.user_id == current_user.id}

    seats = []
    for n in range(1, config.capacity + 1):
        if n in sold:
            status = 'sold'
        elif n in held_by_user:
            status = 'held_by_user'
        elif n in held:
            status = 'held'
        else:
            status = 'available'
        seats.append({'number': n, 'status': status})

    return jsonify({'seats': seats, 'capacity': config.capacity, 'price_kes': config.price_kes})


@app.route('/api/hold_seat', methods=['POST'])
@login_required
def hold_seat_api():
    data = request.json or {}
    match_id = data.get('match_id')
    category = data.get('category')
    seat_number = data.get('seat_number')

    if not all([match_id, category, seat_number]):
        return jsonify({'success': False, 'message': 'Missing required fields'}), 400

    if not is_seat_available(match_id, category, seat_number):
        return jsonify({'success': False, 'message': 'Seat no longer available'})

    hold = hold_seat(match_id, current_user.id, category, seat_number)
    if hold:
        session['selected_seats'] = [seat_number]
        return jsonify({'success': True, 'hold_id': hold.id,
                        'expires_at': hold.expires_at.isoformat(),
                        'redirect': url_for('checkout', match_id=match_id)})

    return jsonify({'success': False, 'message': 'Could not hold seat — please try again'})


@app.route('/api/hold_cluster', methods=['POST'])
@login_required
def hold_cluster_api():
    data = request.json or {}
    match_id = data.get('match_id')
    category = data.get('category')
    seats = data.get('seats', [])

    holds = []
    for seat in seats:
        if not is_seat_available(match_id, category, seat):
            for h in holds:
                release_hold(h.id)
            return jsonify({'success': False, 'message': f'Seat {seat} was just taken'})
        hold = hold_seat(match_id, current_user.id, category, seat)
        holds.append(hold)

    session['selected_seats'] = seats
    return jsonify({'success': True, 'redirect': url_for('checkout', match_id=match_id)})


# ==================== CHECKOUT & PAYMENT ====================

@app.route('/checkout/<int:match_id>')
@login_required
def checkout(match_id):
    selected_seats = session.get('selected_seats', [])
    if not selected_seats:
        flash('No seats selected.', 'danger')
        return redirect(url_for('match_detail', match_id=match_id))

    category = session.get('selected_category')
    ticket_type = session.get('purchase_type', 'single')
    group_size = session.get('group_size') if ticket_type == 'group' else None

    config = SeatConfig.query.filter_by(match_id=match_id, seating_category=category).first_or_404()

    if ticket_type == 'group' and group_size:
        discount = 0.10 if group_size == 3 else 0.14
        per_seat_price = int(config.price_kes * (1 - discount))
    else:
        per_seat_price = config.price_kes

    total = len(selected_seats) * per_seat_price
    wallet_balance = get_wallet_balance(current_user.id)

    return render_template('tickets/checkout.html',
                           match_id=match_id, seats=selected_seats,
                           total=total, per_seat_price=per_seat_price,
                           config=config, seat_count=len(selected_seats),
                           ticket_type=ticket_type, group_size=group_size,
                           wallet_balance=wallet_balance,
                           buyback_rate=80 if ticket_type == 'group' else 90)


@app.route('/payment/<int:match_id>', methods=['POST'])
@login_required
def process_payment(match_id):
    selected_seats = session.get('selected_seats', [])
    category = session.get('selected_category')
    ticket_type = session.get('purchase_type', 'single')
    group_size = session.get('group_size') if ticket_type == 'group' else None

    if not selected_seats or not category:
        flash('Session expired. Please start over.', 'danger')
        return redirect(url_for('match_detail', match_id=match_id))

    config = SeatConfig.query.filter_by(match_id=match_id, seating_category=category).first_or_404()

    if ticket_type == 'group' and group_size:
        discount = 0.10 if group_size == 3 else 0.14
        per_seat_price = int(config.price_kes * (1 - discount))
    else:
        per_seat_price = config.price_kes

    total = len(selected_seats) * per_seat_price

    if get_wallet_balance(current_user.id) < total:
        flash('Insufficient wallet balance. Please top up.', 'danger')
        return redirect(url_for('wallet_page'))

    if not debit_wallet(current_user.id, total, f'Purchase {len(selected_seats)} seat(s) — Match #{match_id}'):
        flash('Payment failed. Please try again.', 'danger')
        return redirect(url_for('checkout', match_id=match_id))

    tickets = []
    for seat in selected_seats:
        ticket = Ticket(
            match_id=match_id,
            user_id=current_user.id,
            seat_category=category,
            seat_number=seat,
            price_paid=per_seat_price,
            totp_secret=generate_totp_secret(),
            ticket_type=ticket_type,
            group_size=group_size,
            status='active'
        )
        db.session.add(ticket)
        tickets.append(ticket)

    current_user.total_purchases += len(selected_seats)
    db.session.commit()

    # Clear purchase session keys
    for key in ('selected_seats', 'purchase_type', 'selected_category', 'group_size', 'match_id'):
        session.pop(key, None)

    flash(f'Successfully purchased {len(tickets)} ticket(s)!', 'success')
    return render_template('tickets/purchase_success.html', tickets=tickets)


# ==================== WALLET ====================

@app.route('/wallet')
@login_required
def wallet_page():
    balance = get_wallet_balance(current_user.id)
    wallet = Wallet.query.filter_by(user_id=current_user.id).first()
    transactions = []
    if wallet:
        transactions = (WalletTransaction.query
                        .filter_by(wallet_id=wallet.id)
                        .order_by(WalletTransaction.created_at.desc())
                        .limit(20).all())
    return render_template('user/wallet.html', balance=balance, transactions=transactions)


@app.route('/wallet/topup', methods=['POST'])
@login_required
def topup_wallet():
    try:
        amount = int(request.form['amount'])
        if amount < 10:
            flash('Minimum top-up is KES 10.', 'warning')
            return redirect(url_for('wallet_page'))
    except (ValueError, KeyError):
        flash('Invalid amount.', 'danger')
        return redirect(url_for('wallet_page'))

    success, message = request_mpesa_topup(current_user.id, amount, current_user.phone)
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('wallet_page'))


@app.route('/wallet/withdraw', methods=['POST'])
@login_required
def withdraw_wallet():
    try:
        amount = int(request.form['amount'])
        if amount < 10:
            flash('Minimum withdrawal is KES 10.', 'warning')
            return redirect(url_for('wallet_page'))
    except (ValueError, KeyError):
        flash('Invalid amount.', 'danger')
        return redirect(url_for('wallet_page'))

    success, msg = process_withdrawal(current_user.id, amount)
    flash(msg, 'success' if success else 'danger')
    return redirect(url_for('wallet_page'))


# ==================== MY TICKETS & TOTP ====================

@app.route('/my_tickets')
@login_required
def my_tickets():
    tickets = (Ticket.query
               .filter_by(user_id=current_user.id, status='active')
               .order_by(Ticket.purchased_at.desc())
               .all())
    return render_template('user/my_tickets.html', tickets=tickets)


@app.route('/ticket/<int:ticket_id>/totp')
@login_required
def ticket_totp(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.user_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('my_tickets'))

    totp_uri = get_totp_uri(ticket.totp_secret, account_name=current_user.username)
    qr_base64 = generate_qr_base64(totp_uri)
    return render_template('tickets/totp_display.html', ticket=ticket, qr_base64=qr_base64)


# ==================== BUYBACK ====================

@app.route('/buyback/<int:ticket_id>', methods=['GET', 'POST'])
@login_required
def request_buyback(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)

    if ticket.user_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('my_tickets'))

    if ticket.status != 'active':
        flash('This ticket has already been returned or used.', 'warning')
        return redirect(url_for('my_tickets'))

    eligible, msg = is_buyback_eligible(ticket.match_id, current_user.id, ticket)

    if request.method == 'GET':
        from buyback_engine import calculate_buyback_savings
        refund_preview = calculate_buyback_savings(ticket)
        return render_template('tickets/buyback_confirm.html', ticket=ticket,
                               eligible=eligible, msg=msg, refund_preview=refund_preview)

    if not eligible:
        flash(msg, 'warning')
        return redirect(url_for('my_tickets'))

    refund_amount = process_buyback(ticket)
    if refund_amount:
        flash(f'Buyback successful! KES {refund_amount} credited to your wallet.', 'success')
    else:
        flash('Buyback failed. Please try again.', 'danger')

    return redirect(url_for('my_tickets'))


# ==================== GATE VALIDATION ====================

@app.route('/gate')
@login_required
def gate_terminal():
    gate_id = request.args.get('gate_id', 1, type=int)
    return render_template('gate/gate_terminal.html', gate_id=gate_id)


@app.route('/gate/validate', methods=['POST'])
def gate_validate():
    """
    Validate a TOTP code at the gate.

    FIX (O(n) problem): The original iterated ALL active tickets in the database.
    Optimisation: restrict the search to tickets for matches happening today.
    At Kasarani with ~5,500 seats per match and one active match at a time this
    reduces the scan from potentially tens of thousands of records to ~5,500.
    A further production optimisation would be an in-memory secret cache
    (Redis) loaded at match-day start.
    """
    data = request.json or {}
    code = data.get('code', '').strip()
    gate_id = data.get('gate_id', 1)

    if len(code) != 6 or not code.isdigit():
        return jsonify({'success': False, 'message': 'Code must be exactly 6 digits'})

    # Narrow to tickets for matches active today
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    todays_match_ids = [
        m.id for m in Match.query.filter(
            Match.kickoff >= today_start - timedelta(hours=6),   # allow early gate open
            Match.kickoff < today_end + timedelta(hours=3),      # allow late finish
            Match.is_active == True
        ).all()
    ]

    if not todays_match_ids:
        return jsonify({'success': False, 'message': 'No active match today'})

    candidate_tickets = (Ticket.query
                         .filter(Ticket.match_id.in_(todays_match_ids),
                                 Ticket.status == 'active')
                         .all())

    ticket = None
    for t in candidate_tickets:
        if verify_totp(t.totp_secret, code):
            ticket = t
            break

    if not ticket:
        return jsonify({'success': False, 'message': 'Invalid or expired code'})

    can_enter, reason, count = TicketEntry.can_reenter(ticket.id)

    if not can_enter:
        reason_messages = {
            'max_reentries_exceeded': 'Maximum re-entries reached for today',
            'no_initial_entry': 'No initial entry recorded — please use the main gate first',
        }
        return jsonify({'success': False, 'message': reason_messages.get(reason, 'Entry denied')})

    entry_type = 'entry' if reason == 'first_entry' else 'reentry'
    entry = TicketEntry(ticket_id=ticket.id, entry_type=entry_type, gate_id=gate_id)
    db.session.add(entry)

    if entry_type == 'entry':
        user = User.query.get(ticket.user_id)
        user.total_attended += 1

    db.session.commit()

    message = 'First entry granted. Enjoy the match!' if entry_type == 'entry' else f'Re-entry #{count} granted'
    return jsonify({
        'success': True,
        'message': message,
        'entry_type': entry_type,
        'seat': f'{ticket.seat_category} — Seat {ticket.seat_number}',
        'match': f'{ticket.match.home_team} vs {ticket.match.away_team}'
    })


# ==================== ADMIN ROUTES ====================

@app.route('/admin')
@admin_required
def admin_dashboard():
    matches = Match.query.order_by(Match.kickoff.desc()).all()
    total_users = User.query.filter_by(role='fan').count()
    total_tickets = Ticket.query.filter_by(status='active').count()
    total_revenue = db.session.query(func.sum(WalletTransaction.amount_kes)).filter(
        WalletTransaction.transaction_type == 'debit'
    ).scalar() or 0
    total_buybacks = BuybackRecord.query.count()

    return render_template('admin/admin_dashboard.html',
                           matches=matches, total_users=total_users,
                           total_tickets=total_tickets, total_revenue=total_revenue,
                           total_buybacks=total_buybacks)


@app.route('/admin/analytics')
@admin_required
def admin_analytics():
    revenue_labels, revenue_data = [], []
    buyback_labels, buyback_returns, buyback_resold = [], [], []

    for i in range(6, -1, -1):
        date = datetime.now() - timedelta(days=i)
        day_start = datetime(date.year, date.month, date.day)
        day_end = day_start + timedelta(days=1)
        label = date.strftime('%a %d')

        revenue_labels.append(label)
        day_rev = db.session.query(func.sum(WalletTransaction.amount_kes)).filter(
            WalletTransaction.transaction_type == 'debit',
            WalletTransaction.created_at >= day_start,
            WalletTransaction.created_at < day_end
        ).scalar() or 0
        revenue_data.append(day_rev)

        buyback_labels.append(label)
        buyback_returns.append(BuybackRecord.query.filter(
            BuybackRecord.created_at >= day_start,
            BuybackRecord.created_at < day_end
        ).count())
        buyback_resold.append(Ticket.query.filter(
            Ticket.purchased_at >= day_start,
            Ticket.purchased_at < day_end,
            Ticket.status == 'active'
        ).count())

    category_stats = (db.session.query(Ticket.seat_category, func.count(Ticket.id))
                      .filter_by(status='active')
                      .group_by(Ticket.seat_category).all())

    tickets_sold = Ticket.query.filter_by(status='active').count()
    buyback_count = BuybackRecord.query.count()
    buyback_rate = round((buyback_count / tickets_sold * 100), 1) if tickets_sold > 0 else 0

    return render_template('admin/analytics.html',
                           revenue_labels=revenue_labels, revenue_data=revenue_data,
                           category_labels=[s[0] for s in category_stats],
                           category_data=[s[1] for s in category_stats],
                           buyback_labels=buyback_labels,
                           buyback_returns=buyback_returns,
                           buyback_resold=buyback_resold,
                           total_revenue=sum(revenue_data),
                           tickets_sold=tickets_sold,
                           buyback_rate=buyback_rate)


@app.route('/admin/matches')
@admin_required
def manage_matches():
    matches = Match.query.order_by(Match.kickoff.desc()).all()
    return render_template('admin/manage_matches.html', matches=matches)


@app.route('/admin/add_match', methods=['GET', 'POST'])
@admin_required
def add_match():
    if request.method == 'POST':
        match = Match(
            home_team=request.form['home_team'],
            away_team=request.form['away_team'],
            kickoff=datetime.strptime(request.form['kickoff'], '%Y-%m-%dT%H:%M'),
            competition=request.form.get('competition', 'KPL'),
            venue=request.form.get('venue', 'Kasarani Stadium')
        )
        db.session.add(match)
        db.session.commit()

        for cat, default_price, default_cap in [('Regular', 250, 5000), ('VIP', 800, 500), ('VVIP', 1500, 50)]:
            price_key = f'{cat.lower()}_price'
            cap_key = f'{cat.lower()}_capacity'
            db.session.add(SeatConfig(
                match_id=match.id,
                seating_category=cat,
                capacity=int(request.form.get(cap_key, default_cap)),
                price_kes=int(request.form.get(price_key, default_price))
            ))
        db.session.commit()

        flash(f'Match added: {match.home_team} vs {match.away_team}', 'success')
        return redirect(url_for('manage_matches'))

    return render_template('admin/add_match.html')


@app.route('/admin/match/<int:match_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_match(match_id):
    match = Match.query.get_or_404(match_id)
    if request.method == 'POST':
        match.home_team = request.form['home_team']
        match.away_team = request.form['away_team']
        match.kickoff = datetime.strptime(request.form['kickoff'], '%Y-%m-%dT%H:%M')
        match.competition = request.form.get('competition', match.competition)
        match.venue = request.form.get('venue', match.venue)
        match.is_active = 'is_active' in request.form
        db.session.commit()
        flash('Match updated.', 'success')
        return redirect(url_for('manage_matches'))
    return render_template('admin/edit_match.html', match=match)


@app.route('/admin/match/<int:match_id>/delete', methods=['POST'])
@admin_required
def delete_match(match_id):
    match = Match.query.get_or_404(match_id)
    name = f'{match.home_team} vs {match.away_team}'
    db.session.delete(match)
    db.session.commit()
    flash(f'Match deleted: {name}', 'success')
    return redirect(url_for('manage_matches'))


@app.route('/admin/match/<int:match_id>/toggle', methods=['POST'])
@admin_required
def toggle_match(match_id):
    match = Match.query.get_or_404(match_id)
    match.is_active = not match.is_active
    db.session.commit()
    state = 'activated' if match.is_active else 'deactivated'
    flash(f'Match {state}.', 'success')
    return redirect(url_for('manage_matches'))


@app.route('/admin/users')
@admin_required
def manage_users():
    users = User.query.filter_by(role='fan').order_by(User.created_at.desc()).all()
    return render_template('admin/manage_users.html', users=users)


# ==================== LIVE TOTP API ====================

@app.route('/api/totp/<int:ticket_id>')
@login_required
def api_totp(ticket_id):
    """Return current TOTP code and seconds remaining for the ticket display page."""
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    from totp_utils import get_current_totp_code, get_totp_seconds_remaining
    return jsonify({
        'code': get_current_totp_code(ticket.totp_secret),
        'seconds_remaining': get_totp_seconds_remaining()
    })


# ==================== CLEANUP API (called by cron or background task) ====================

@app.route('/internal/cleanup_holds', methods=['POST'])
def cleanup_holds():
    """
    Remove expired seat holds.
    In production: call this from a cron job every 60 seconds.
    Protected by a shared secret header.
    """
    secret = request.headers.get('X-Cleanup-Secret', '')
    if secret != app.config.get('CLEANUP_SECRET', 'cleanup-secret'):
        return jsonify({'error': 'Unauthorized'}), 403

    released = cleanup_expired_holds()
    return jsonify({'released': released})


# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('errors/500.html'), 500


# ==================== RUN ====================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(role='admin').first():
            admin = User(
                username='admin',
                email='admin@stadi.com',
                password_hash=generate_password_hash('admin123'),
                phone='+254700000000',
                role='admin'
            )
            db.session.add(admin)
            db.session.commit()
            init_wallet(admin.id)
            print('✓ Admin created: admin / admin123')
        print('✓ Stadi Soka-Ticket ready → http://127.0.0.1:5000')

    app.run(debug=True, host='0.0.0.0', port=5000)
