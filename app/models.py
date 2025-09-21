from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.database import Base
from sqlalchemy import Enum as SQLEnum
import uuid


class User(Base):
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    phone = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(SQLEnum('admin', 'investor', name='user_role'), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# Shares offerings table
class SharesOffering(Base):
    __tablename__ = "shares_offering"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_name = Column(String, nullable=False)
    total_shares = Column(Integer, nullable=False)
    price_per_share = Column(Float, nullable=False)
    available_shares = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Holding(Base):
    __tablename__ = "holdings"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    shares_offering_id = Column(UUID(as_uuid=True), ForeignKey("shares_offering.id"), nullable=False)
    shares_owned = Column(Integer, nullable=False)
    average_price = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    __table_args__ = (UniqueConstraint('user_id', 'shares_offering_id'),)

class Transaction(Base):
    __tablename__ = "transactions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    type = Column(SQLEnum('buy', 'sell', name='transaction_type'), nullable=False)
    shares_offering_id = Column(UUID(as_uuid=True), ForeignKey("shares_offering.id"), nullable=False)
    shares_count = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    status = Column(SQLEnum('pending', 'approved', 'rejected', 'failed', name='transaction_status'), 
                   nullable=False, default='pending')
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Payment(Base):
    __tablename__ = "payments"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    transaction_id = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False)
    amount = Column(Float, nullable=False)
    type = Column(SQLEnum('in', 'out', name='payment_type'), nullable=False)
    status = Column(SQLEnum('pending', 'completed', 'failed', name='payment_status'), 
                   nullable=False, default='pending')
    external_id = Column(String, nullable=True)
    method = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Dividend(Base):
    __tablename__ = "dividends"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shares_offering_id = Column(UUID(as_uuid=True), ForeignKey("shares_offering.id"), nullable=False)
    amount_per_share = Column(Float, nullable=False)
    declared_at = Column(DateTime(timezone=True), server_default=func.now())

class DividendPayout(Base):
    __tablename__ = "dividend_payouts"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    dividend_id = Column(UUID(as_uuid=True), ForeignKey("dividends.id"), nullable=False)
    amount_received = Column(Float, nullable=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(SQLEnum('pending', 'paid', name='payout_status'), 
                   nullable=False, default='pending')