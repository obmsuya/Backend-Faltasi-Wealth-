from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import Payment, Transaction, User
from app.auth import get_current_user
from pydantic import BaseModel
import uuid

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

@router.get("/history", response_model=List[PaymentResponse])
def get_payment_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get user's payment history"""
    payments = db.query(Payment).join(Transaction).filter(
        Transaction.user_id == current_user.id
    ).order_by(Payment.created_at.desc()).all()
    
    return [
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

@router.post("/callback")
def payment_webhook(
    callback_data: PaymentCallback,
    db: Session = Depends(get_db)
):
    """Webhook for payment providers to update payment status"""
    try:
        transaction_uuid = uuid.UUID(callback_data.transaction_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid transaction ID")
    
    # Find payment by transaction ID
    payment = db.query(Payment).filter(
        Payment.transaction_id == transaction_uuid
    ).first()
    
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    # Update payment status
    payment.status = callback_data.status
    payment.external_id = callback_data.external_id
    
    # If payment completed, update transaction status
    if callback_data.status == 'completed':
        transaction = db.query(Transaction).filter(
            Transaction.id == transaction_uuid
        ).first()
        
        if transaction and transaction.status == 'pending':
            transaction.status = 'approved'
            
            # Process the transaction (buy/sell)
            from .transactions import process_buy_transaction, process_sell_transaction
            if transaction.type == 'buy':
                process_buy_transaction(transaction, db)
            elif transaction.type == 'sell':
                process_sell_transaction(transaction, db)
    
    db.commit()
    
    return {"message": "Payment status updated"}