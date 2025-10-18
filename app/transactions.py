from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import Transaction, SharesOffering, Holding, User, Payment
from app.auth import get_current_user, get_current_admin
from app.redis_client import redis_client
from pydantic import BaseModel
import uuid
from datetime import datetime
import json

router = APIRouter(prefix="/transactions", tags=["transactions"])

class BuySharesRequest(BaseModel):
    shares_offering_id: str
    shares_count: int

class SellSharesRequest(BaseModel):
    shares_offering_id: str
    shares_count: int

class TransactionResponse(BaseModel):
    id: str
    type: str
    shares_offering_id: str
    company_name: str
    shares_count: int
    price: float
    total_amount: float
    status: str
    created_at: str

class PaymentRequest(BaseModel):
    transaction_id: str
    payment_method: str = "placeholder"

# Redis key patterns
USER_TRANSACTIONS_KEY = "user:{user_id}:transactions"
PORTFOLIO_KEY = "user:{user_id}:portfolio"

async def invalidate_user_cache(user_id: str):
    """Invalidate all user-related cache"""
    user_transactions_key = USER_TRANSACTIONS_KEY.format(user_id=user_id)
    portfolio_key = PORTFOLIO_KEY.format(user_id=user_id)
    
    await redis_client.delete(user_transactions_key, portfolio_key)

def process_buy_transaction(transaction: Transaction, db: Session):
    """Process approved buy transaction"""
    # Update shares offering available shares
    shares_offering = db.query(SharesOffering).filter(
        SharesOffering.id == transaction.shares_offering_id
    ).first()
    
    if shares_offering.available_shares < transaction.shares_count:
        raise HTTPException(status_code=400, detail="Not enough shares available")
    
    shares_offering.available_shares -= transaction.shares_count
    
    # Update or create holding
    holding = db.query(Holding).filter(
        Holding.user_id == transaction.user_id,
        Holding.shares_offering_id == transaction.shares_offering_id
    ).first()
    
    if holding:
        # Update existing holding with new average price
        total_shares = holding.shares_owned + transaction.shares_count
        total_cost = (holding.shares_owned * holding.average_price) + \
                    (transaction.shares_count * transaction.price)
        holding.average_price = total_cost / total_shares
        holding.shares_owned = total_shares
    else:
        # Create new holding
        holding = Holding(
            user_id=transaction.user_id,
            shares_offering_id=transaction.shares_offering_id,
            shares_owned=transaction.shares_count,
            average_price=transaction.price
        )
        db.add(holding)

def process_sell_transaction(transaction: Transaction, db: Session):
    """Process approved sell transaction"""
    # Update holding
    holding = db.query(Holding).filter(
        Holding.user_id == transaction.user_id,
        Holding.shares_offering_id == transaction.shares_offering_id
    ).first()
    
    if holding.shares_owned < transaction.shares_count:
        raise HTTPException(status_code=400, detail="Not enough shares to sell")
    
    holding.shares_owned -= transaction.shares_count
    
    # Update shares offering available shares
    shares_offering = db.query(SharesOffering).filter(
        SharesOffering.id == transaction.shares_offering_id
    ).first()
    shares_offering.available_shares += transaction.shares_count
    
    # If no shares left, delete the holding
    if holding.shares_owned == 0:
        db.delete(holding)

@router.post("/buy", response_model=TransactionResponse)
async def initiate_buy_shares(
    buy_data: BuySharesRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Initiate buy shares transaction"""
    try:
        shares_uuid = uuid.UUID(buy_data.shares_offering_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid shares ID")
    
    # Verify shares offering exists and has enough shares
    shares_offering = db.query(SharesOffering).filter(
        SharesOffering.id == shares_uuid
    ).first()
    
    if not shares_offering:
        raise HTTPException(status_code=404, detail="Shares offering not found")
    
    if shares_offering.available_shares < buy_data.shares_count:
        raise HTTPException(
            status_code=400, 
            detail=f"Not enough shares available. Only {shares_offering.available_shares} shares left"
        )
    
    if buy_data.shares_count <= 0:
        raise HTTPException(status_code=400, detail="Shares count must be positive")
    
    # Calculate total amount
    total_amount = buy_data.shares_count * shares_offering.price_per_share
    
    # Create transaction
    transaction = Transaction(
        user_id=current_user.id,
        type='buy',
        shares_offering_id=shares_uuid,
        shares_count=buy_data.shares_count,
        price=shares_offering.price_per_share,
        status='pending'
    )
    
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    
    # Create payment record
    payment = Payment(
        user_id=current_user.id,
        transaction_id=transaction.id,
        amount=total_amount,
        type='out',
        status='pending',
        method='bank_transfer'
    )
    db.add(payment)
    db.commit()
    
    # Invalidate user cache in background
    background_tasks.add_task(invalidate_user_cache, str(current_user.id))
    
    return TransactionResponse(
        id=str(transaction.id),
        type=transaction.type,
        shares_offering_id=str(transaction.shares_offering_id),
        company_name=shares_offering.company_name,
        shares_count=transaction.shares_count,
        price=transaction.price,
        total_amount=total_amount,
        status=transaction.status,
        created_at=transaction.created_at.isoformat()
    )

@router.post("/sell", response_model=TransactionResponse)
async def initiate_sell_shares(
    sell_data: SellSharesRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Initiate sell shares transaction"""
    try:
        shares_uuid = uuid.UUID(sell_data.shares_offering_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid shares ID")
    
    # Check if user has enough shares to sell
    holding = db.query(Holding).filter(
        Holding.user_id == current_user.id,
        Holding.shares_offering_id == shares_uuid
    ).first()
    
    if not holding or holding.shares_owned < sell_data.shares_count:
        raise HTTPException(
            status_code=400, 
            detail="Not enough shares to sell"
        )
    
    if sell_data.shares_count <= 0:
        raise HTTPException(status_code=400, detail="Shares count must be positive")
    
    shares_offering = db.query(SharesOffering).filter(
        SharesOffering.id == shares_uuid
    ).first()
    
    # Create sell transaction
    transaction = Transaction(
        user_id=current_user.id,
        type='sell',
        shares_offering_id=shares_uuid,
        shares_count=sell_data.shares_count,
        price=shares_offering.price_per_share,
        status='pending'
    )
    
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    
    # Create payment record for incoming funds
    total_amount = sell_data.shares_count * shares_offering.price_per_share
    payment = Payment(
        user_id=current_user.id,
        transaction_id=transaction.id,
        amount=total_amount,
        type='in',
        status='pending',
        method='bank_transfer'
    )
    db.add(payment)
    db.commit()
    
    # Invalidate user cache in background
    background_tasks.add_task(invalidate_user_cache, str(current_user.id))
    
    return TransactionResponse(
        id=str(transaction.id),
        type=transaction.type,
        shares_offering_id=str(transaction.shares_offering_id),
        company_name=shares_offering.company_name,
        shares_count=transaction.shares_count,
        price=transaction.price,
        total_amount=total_amount,
        status=transaction.status,
        created_at=transaction.created_at.isoformat()
    )

@router.get("/", response_model=List[TransactionResponse])
async def get_user_transactions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get user's transaction history"""
    cache_key = USER_TRANSACTIONS_KEY.format(user_id=str(current_user.id))
    
    # Try to get from cache first
    cached_transactions = await redis_client.get(cache_key)
    if cached_transactions:
        return json.loads(cached_transactions)
    
    transactions = db.query(Transaction).filter(
        Transaction.user_id == current_user.id
    ).order_by(Transaction.created_at.desc()).all()
    
    response = []
    for transaction in transactions:
        shares_offering = db.query(SharesOffering).filter(
            SharesOffering.id == transaction.shares_offering_id
        ).first()
        
        response.append(TransactionResponse(
            id=str(transaction.id),
            type=transaction.type,
            shares_offering_id=str(transaction.shares_offering_id),
            company_name=shares_offering.company_name if shares_offering else "Unknown",
            shares_count=transaction.shares_count,
            price=transaction.price,
            total_amount=transaction.shares_count * transaction.price,
            status=transaction.status,
            created_at=transaction.created_at.isoformat()
        ))
    
    # Cache for 2 minutes
    await redis_client.setex(cache_key, 120, json.dumps([item.dict() for item in response]))
    
    return response

@router.post("/{transaction_id}/approve")
async def approve_transaction(
    transaction_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Admin only - Approve a transaction"""
    try:
        transaction_uuid = uuid.UUID(transaction_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid transaction ID")
    
    transaction = db.query(Transaction).filter(Transaction.id == transaction_uuid).first()
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    if transaction.status != 'pending':
        raise HTTPException(status_code=400, detail="Transaction already processed")
    
    # Process the transaction based on type
    if transaction.type == 'buy':
        process_buy_transaction(transaction, db)
    elif transaction.type == 'sell':
        process_sell_transaction(transaction, db)
    
    transaction.status = 'approved'
    
    # Update payment status
    payment = db.query(Payment).filter(Payment.transaction_id == transaction_uuid).first()
    if payment:
        payment.status = 'completed'
    
    db.commit()
    
    # Invalidate cache for the user and shares
    background_tasks.add_task(invalidate_user_cache, str(transaction.user_id))
    from .shares_offering import invalidate_shares_cache
    background_tasks.add_task(invalidate_shares_cache)
    
    return {"message": "Transaction approved successfully"}