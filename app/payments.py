from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import Payment, Transaction, User
from app.auth import get_current_user
from app.redis_client import redis_client
from pydantic import BaseModel
import uuid
import json
from typing import Dict, Any
import httpx
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])

class PaymentResponse(BaseModel):
    id: str
    transaction_id: str
    amount: float
    type: str
    status: str
    method: str
    created_at: str

class PaymentCallback(BaseModel):
    transaction_id: str
    external_id: str
    status: str
    # Add other payment provider specific fields

# Redis key patterns
USER_PAYMENTS_KEY = "user:{user_id}:payments"
USER_KEY = "user:{user_id}"
USER_TRANSACTIONS_KEY = "user:{user_id}:transactions"
PORTFOLIO_KEY = "user:{user_id}:portfolio"
SHARES_ALL_KEY = "shares:all"
SHARES_DETAIL_KEY = "shares:{id}"

async def invalidate_user_payments_cache(user_id: str):
    """Invalidate user payments cache"""
    cache_key = USER_PAYMENTS_KEY.format(user_id=user_id)
    await redis_client.delete(cache_key)

async def invalidate_user_cache(user_id: str):
    """Invalidate all user-related cache"""
    user_transactions_key = USER_TRANSACTIONS_KEY.format(user_id=user_id)
    portfolio_key = PORTFOLIO_KEY.format(user_id=user_id)
    
    await redis_client.delete(user_transactions_key, portfolio_key)

async def invalidate_shares_cache():
    """Invalidate all shares-related cache"""
    await redis_client.delete(SHARES_ALL_KEY)
    keys = await redis_client.keys("shares:*")
    if keys:
        await redis_client.delete(*keys)

@router.get("/history", response_model=List[PaymentResponse])
async def get_payment_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get user's payment history"""
    cache_key = USER_PAYMENTS_KEY.format(user_id=str(current_user.id))
    
    # Try to get from cache first
    cached_payments = await redis_client.get(cache_key)
    if cached_payments:
        return json.loads(cached_payments)
    
    payments = db.query(Payment).join(Transaction).filter(
        Transaction.user_id == current_user.id
    ).order_by(Payment.created_at.desc()).all()
    
    response = [
        PaymentResponse(
            id=str(payment.id),
            transaction_id=str(payment.transaction_id),
            amount=payment.amount,
            type=payment.type,
            status=payment.status,
            method=payment.method or "Unknown",
            created_at=payment.created_at.isoformat()
        )
        for payment in payments
    ]
    
    # Cache for 2 minutes
    await redis_client.setex(cache_key, 120, json.dumps([item.dict() for item in response]))
    
    return response

@router.post("/callback")
async def payment_webhook(
    callback_data: Dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Receive relayed callback from Wapangaji after AzamPay processes payment.
    Expected payload includes: transaction_id (local), external_id, status, etc.
    """
    try:
        local_transaction_id = callback_data.get("transaction_id")
        external_id = callback_data.get("external_id")
        status = callback_data.get("status")
        amount = callback_data.get("amount")

        if not local_transaction_id:
            raise HTTPException(status_code=400, detail="Missing transaction_id in callback")

        # Find local payment by external_id or transaction_id
        payment = db.query(Payment).filter(
            Payment.external_id == external_id
        ).first()
        
        if not payment:
            # Fallback: try by transaction_id from context
            try:
                tx_uuid = uuid.UUID(local_transaction_id)
                payment = db.query(Payment).join(Transaction).filter(
                    Transaction.id == tx_uuid
                ).first()
            except ValueError:
                pass
        
        if not payment:
            logger.warning(f"Payment not found for callback: {callback_data}")
            return {"message": "Payment not found, ignored"}

        # Update payment
        payment.status = status
        if status == "completed":
            payment.status = "completed"
        elif status == "failed":
            payment.status = "failed"

        # Update transaction
        transaction = db.query(Transaction).filter(
            Transaction.id == payment.transaction_id
        ).first()

        if transaction and transaction.status == "pending" and status == "completed":
            transaction.status = "approved"
            
            # Execute the buy
            process_buy_transaction(transaction, db)

        db.commit()

        # Invalidate caches
        background_tasks.add_task(invalidate_user_payments_cache, str(payment.user_id))
        background_tasks.add_task(invalidate_user_cache, str(payment.user_id))
        background_tasks.add_task(invalidate_shares_cache)

        logger.info(f"Callback processed for transaction {local_transaction_id}, status: {status}")
        return {"message": "Callback processed successfully"}

    except Exception as e:
        logger.error(f"Error in shares payment callback: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal callback processing error")