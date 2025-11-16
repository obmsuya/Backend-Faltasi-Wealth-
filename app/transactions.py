from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import Transaction, SharesOffering, Holding, User, Payment
from app.auth import get_current_user, get_current_admin
from app.redis_client import redis_client
from pydantic import BaseModel
import uuid
from enum import Enum
from datetime import datetime
import json
from typing import Dict, Any
import httpx
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transactions", tags=["transactions"])

class Provider(str, Enum):
    AIRTEL = "Airtel"
    TIGO = "Tigo"
    HALOPESA = "Halopesa"
    AZAMPESA = "Azampesa"
    MPESA = "Mpesa"

class BuySharesRequest(BaseModel):
    shares_offering_id: str
    shares_count: int
    provider: Provider

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
    """Initiate buy shares transaction using Wapangaji MNO checkout"""
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
    
    # Create local transaction as pending
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
    
    # Create local payment record as pending
    payment = Payment(
        user_id=current_user.id,
        transaction_id=transaction.id,
        amount=total_amount,
        type='out',
        status='pending',
        method='mobile_money'
    )
    db.add(payment)
    db.commit()
    
    # === CALL WAPANGAGI CHECKOUT API ===
    wapangaji_checkout_url = "https://backend.wapangaji.com/api/v1/payments/azampay/mno/checkout"

    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": "api_EhUHl1hil0c6bsYQLG1oJxrJqN1ZfjM6"
    }
    
    payment_context = {
        "system": "shares",
        "transaction_id": str(transaction.id),
        "user_id": str(current_user.id),
        "shares_offering_id": str(shares_offering.id),
        "company_name": shares_offering.company_name,
        "callback_url": "https://faltasi.wapangaji.com/transactions/payments/callback" 
    }
    
    checkout_payload = {
        "accountNumber": current_user.phone,  # Use user's phone
        "amount": str(total_amount),
        "provider": buy_data.provider,
        "payment_context": payment_context
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(wapangaji_checkout_url, json=checkout_payload, headers=headers)
            
        if response.status_code != 200:
            error_msg = response.json().get("error", "Unknown error from payment gateway")
            logger.error(f"Wapangaji checkout failed: {error_msg}")
            raise HTTPException(status_code=400, detail=f"Payment initiation failed: {error_msg}")
        
        wapangaji_data = response.json()
        external_id = wapangaji_data.get("external_id")
        
        # Link external_id to local payment
        payment.external_id = external_id
        db.commit()
        
    except httpx.RequestError as e:
        logger.error(f"Network error calling Wapangaji: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to connect to payment service")
    
    # Invalidate cache
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



@router.post("/payments/callback")
async def payment_callback(
    callback_data: Dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):

    try:
        local_tx_id = callback_data.get("transaction_id")
        external_id = callback_data.get("external_id")
        status = callback_data.get("status", "").lower()
        amount = callback_data.get("amount")

        if not local_tx_id and not external_id:
            raise HTTPException(status_code=400, detail="Missing transaction_id or external_id")

        # Find Payment record
        payment = None
        if external_id:
            payment = db.query(Payment).filter(Payment.external_id == external_id).first()
        
        # Fallback: use local transaction_id from context
        if not payment and local_tx_id:
            try:
                tx_uuid = uuid.UUID(local_tx_id)
                payment = db.query(Payment).join(Transaction).filter(
                    Transaction.id == tx_uuid
                ).first()
            except ValueError:
                pass

        if not payment:
            logger.warning(f"Payment not found for callback: {callback_data}")
            return {"message": "Payment not found, ignored"}

        # Update Payment
        payment.status = "completed" if status in ["success", "completed"] else "failed"
        db.commit()

        # Update Transaction & execute buy
        transaction = db.query(Transaction).filter(
            Transaction.id == payment.transaction_id
        ).first()

        if transaction and transaction.status == "pending" and status in ["success", "completed"]:
            transaction.status = "approved"
            db.commit()

            # Execute the actual share purchase
            process_buy_transaction(transaction, db)

        # Invalidate caches
        background_tasks.add_task(invalidate_user_cache, str(payment.user_id))
        background_tasks.add_task(invalidate_shares_cache)

        logger.info(f"Buy callback processed: tx={local_tx_id}, status={status}")
        return {"message": "Callback processed successfully"}

    except Exception as e:
        logger.error(f"Buy callback error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Callback processing failed")


@router.post("/sell", response_model=TransactionResponse)
async def initiate_sell_shares(
    sell_data: SellSharesRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Initiate sell shares transaction with disbursement"""
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
    
    total_amount = sell_data.shares_count * shares_offering.price_per_share
    payment = Payment(
        user_id=current_user.id,
        transaction_id=transaction.id,
        amount=total_amount,
        type='in',
        status='pending',
        method='mobile_money'
    )
    db.add(payment)
    db.commit()
    
    wapangaji_disburse_url = "https://backend.wapangaji.com/api/v1/payments/azampay/disburse" 

    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": "api_EhUHl1hil0c6bsYQLG1oJxrJqN1ZfjM6"  
    }
    payment_context = {
        "system": "shares",
        "transaction_id": str(transaction.id),
        "user_id": str(current_user.id),
        "shares_offering_id": str(shares_offering.id),
        "type": "sell_payout",
        "callback_url": "https://faltasi.wapangaji.com/transactions/disbursements/callback"  
    }
    
    disburse_payload = {
        "destination_phone": current_user.phone,
        "amount": str(total_amount),
        "operator": sell_data.provider,  # Dynamic provider from request (assume added to SellSharesRequest)
        "recipient_name": current_user.name,
        "payment_context": payment_context
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(wapangaji_disburse_url, json=disburse_payload, headers=headers)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Disbursement failed: {response.json().get('error')}")
        
        wapangaji_data = response.json()
        external_id = wapangaji_data.get("external_reference_id")
        
        # Link to local payment
        payment.external_id = external_id
        db.commit()
    
    # Invalidate cache
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



@router.post("/disbursements/callback")
async def disbursement_callback(
    callback_data: Dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    try:
        local_transaction_id = callback_data.get("transaction_id")
        external_id = callback_data.get("external_reference_id")
        status = callback_data.get("status")

        if not local_transaction_id:
            raise HTTPException(status_code=400, detail="Missing transaction_id")

        payment = db.query(Payment).filter(Payment.external_id == external_id).first()
        if not payment:
            try:
                tx_uuid = uuid.UUID(local_transaction_id)
                payment = db.query(Payment).join(Transaction).filter(Transaction.id == tx_uuid).first()
            except ValueError:
                pass
        
        if not payment:
            logger.warning(f"Disbursement payment not found: {callback_data}")
            return {"message": "Ignored"}

        payment.status = status

        transaction = db.query(Transaction).filter(Transaction.id == payment.transaction_id).first()
        if transaction and transaction.status == "pending" and status == "completed":
            transaction.status = "approved"
            process_sell_transaction(transaction, db) 

        db.commit()

        background_tasks.add_task(invalidate_user_payments_cache, str(payment.user_id))
        background_tasks.add_task(invalidate_user_cache, str(payment.user_id))
        background_tasks.add_task(invalidate_shares_cache)

        return {"message": "Disbursement callback processed"}

    except Exception as e:
        logger.error(f"Disbursement callback error: {str(e)}")
        raise HTTPException(status_code=500, detail="Error processing disbursement callback")



@router.get("/disbursements/status/{external_id}")
async def check_disbursement_status(
    external_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Poll disbursement status from wapangaji"""
    payment = db.query(Payment).filter(
        Payment.external_id == external_id,
        Payment.user_id == current_user.id
    ).first()
    
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    # Call wapangaji status check
    wapangaji_status_url = "https://backend.wapangaji.com/api/v1/payments/azampay/transactionstatus"
    params = {
        "pgReferenceId": payment.transaction_id, 
        "bankName": "Azampesa"  
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(wapangaji_status_url, params=params)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail="Status check failed")
        
        status_data = response.json()
        new_status = status_data.get("transaction_status")
        
        if new_status and new_status != payment.status:
            payment.status = new_status
            transaction = db.query(Transaction).filter(Transaction.id == payment.transaction_id).first()
            if transaction and new_status == "completed":
                transaction.status = "approved"
                if transaction.type == "sell":
                    process_sell_transaction(transaction, db)
            
            db.commit()
    
    return {
        "status": payment.status,
        "details": status_data
    }