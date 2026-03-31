import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from models import User, Match, SeatConfig, Ticket, BuybackRecord, Wallet, WalletTransaction, TicketEntry, SeatHold, AuditLog
from werkzeug.security import generate_password_hash

def init_database():
    with app.app_context():
        db.drop_all()
        db.create_all()
        print(" Database tables created")
        
        # Create admin user
        admin = User(
            username='admin',
            email='admin@stadi.com',
            password_hash=generate_password_hash('admin123'),
            phone='+254700000000',
            role='admin'
        )
        db.session.add(admin)
        db.session.commit()
        print(" Admin user created (admin/admin123)")
        
        # Create test fan user
        fan = User(
            username='testfan',
            email='fan@test.com',
            password_hash=generate_password_hash('test123'),
            phone='+254711111111',
            role='fan'
        )
        db.session.add(fan)
        db.session.commit()
        print(" Test fan user created (testfan/test123)")
        
        print(" Database initialization complete!")

if __name__ == '__main__':
    init_database()