import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from models import Match, SeatConfig, User, Wallet
from datetime import datetime, timedelta

def seed_sample_data():
    with app.app_context():
        # Create sample match
        match = Match(
            home_team='Gor Mahia',
            away_team='AFC Leopards',
            kickoff=datetime.now() + timedelta(days=7),
            competition='KPL',
            is_active=True
        )
        db.session.add(match)
        db.session.commit()
        
        # Add seat configurations
        configs = [
            SeatConfig(match_id=match.id, seating_category='Regular', capacity=5000, price_kes=250),
            SeatConfig(match_id=match.id, seating_category='VIP', capacity=500, price_kes=800),
            SeatConfig(match_id=match.id, seating_category='VVIP', capacity=50, price_kes=1500)
        ]
        for config in configs:
            db.session.add(config)
        db.session.commit()
        
        # Initialize wallet for existing users
        for user in User.query.all():
            if not user.wallet:
                wallet = Wallet(user_id=user.id, balance_kes=1000)
                db.session.add(wallet)
        db.session.commit()
        
        print(f" Sample match created: {match.home_team} vs {match.away_team}")
        print(f"   Regular seats: 5,000 @ KES 250")
        print(f"   VIP seats: 500 @ KES 800")
        print(f"   VVIP seats: 50 @ KES 1,500")

if __name__ == '__main__':
    seed_sample_data()