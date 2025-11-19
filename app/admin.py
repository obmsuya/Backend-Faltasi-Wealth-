from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models import User, SharesOffering, Transaction, Holding, Dividend, DividendPayout, Payment
from app.auth import get_current_admin, hash_password
from app.redis_client import redis_client, get_redis_client
from pydantic import BaseModel
import uuid
import json
import httpx

router = APIRouter(prefix="/admin", tags=["admin"])


# Response Models
class UserResponse(BaseModel):
    id: str
    name: str
    phone: str
    role: str
    is_active: bool
    created_at: str

class AdminSharesOfferingResponse(BaseModel):
    id: str
    company_name: str
    total_shares: int
    price_per_share: float
    available_shares: int
    created_at: str

class AdminTransactionResponse(BaseModel):
    id: str
    user_id: str
    user_name: str
    type: str
    shares_offering_id: str
    company_name: str
    shares_count: int
    price: float
    total_amount: float
    status: str
    created_at: str

class AdminHoldingResponse(BaseModel):
    id: str
    user_id: str
    user_name: str
    shares_offering_id: str
    company_name: str
    shares_owned: int
    average_price: float
    current_value: float
    created_at: str

class DividendCreate(BaseModel):
    shares_offering_id: str
    amount_per_share: float

class DividendResponse(BaseModel):
    id: str
    shares_offering_id: str
    company_name: str
    amount_per_share: float
    declared_at: str

class DividendPayoutResponse(BaseModel):
    id: str
    user_id: str
    user_name: str
    dividend_id: str
    company_name: str
    amount_per_share: float
    shares_owned: int
    amount_received: float
    status: str
    paid_at: Optional[str]

class CreateAdminRequest(BaseModel):
    phone: str
    password: str

# Redis key patterns for admin
ADMIN_USERS_KEY = "admin:users"
ADMIN_SHARES_KEY = "admin:shares"
ADMIN_TRANSACTIONS_KEY = "admin:transactions"
ADMIN_HOLDINGS_KEY = "admin:holdings"
ADMIN_DIVIDENDS_KEY = "admin:dividends"

async def invalidate_admin_cache():
    """Invalidate all admin-related cache"""
    keys = [
        ADMIN_USERS_KEY,
        ADMIN_SHARES_KEY,
        ADMIN_TRANSACTIONS_KEY,
        ADMIN_HOLDINGS_KEY,
        ADMIN_DIVIDENDS_KEY
    ]
    # Also delete pattern-based keys
    pattern_keys = await redis_client.keys("admin:*")
    if pattern_keys:
        keys.extend(pattern_keys)
    
    if keys:
        await redis_client.delete(*keys)

# Users Management
@router.get("/users", response_model=List[UserResponse])
async def get_all_users(
    db: Session = Depends(get_db),
    # admin: User = Depends(get_current_admin)
):
    """Get all users in the system"""
    redis_client = await get_redis_client()
    cached_users = await redis_client.get(ADMIN_USERS_KEY)
    if cached_users:
        return json.loads(cached_users)
    
    users = db.query(User).order_by(User.created_at.desc()).all()
    
    response = [
        UserResponse(
            id=str(user.id),
            name=user.name,
            phone=user.phone,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at.isoformat()
        )
        for user in users
    ]
    
    await redis_client.setex(ADMIN_USERS_KEY, 300, json.dumps([item.dict() for item in response]))
    return response

@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    # admin: User = Depends(get_current_admin)
):
    """Hard delete user with cascade (admin only)"""
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    
    user = db.query(User).filter(User.id == user_uuid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.role == "admin":
        raise HTTPException(status_code=400, detail="Cannot delete admin user")
    
    # Delete user and all related data (cascade delete)
    db.delete(user)
    db.commit()
    
    # Invalidate all admin cache
    await invalidate_admin_cache()
    
    return {"message": "User deleted successfully"}

@router.patch("/users/{user_id}/toggle-active")
async def toggle_user_active(
    user_id: str,
    db: Session = Depends(get_db),
    # admin: User = Depends(get_current_admin)
):
    """Toggle user active status"""
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    
    user = db.query(User).filter(User.id == user_uuid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.role == "admin":
        raise HTTPException(status_code=400, detail="Cannot deactivate admin user")
    
    user.is_active = not user.is_active
    db.commit()
    
    await invalidate_admin_cache()
    
    return {"message": f"User {'activated' if user.is_active else 'deactivated'} successfully"}




# Shares Management
@router.get("/shares", response_model=List[AdminSharesOfferingResponse])
async def get_all_shares(
    db: Session = Depends(get_db),
    # admin: User = Depends(get_current_admin)
):
    """Get all shares offerings (admin view)"""
    cached_shares = await redis_client.get(ADMIN_SHARES_KEY)
    if cached_shares:
        return json.loads(cached_shares)
    
    shares = db.query(SharesOffering).order_by(SharesOffering.created_at.desc()).all()
    
    response = [
        AdminSharesOfferingResponse(
            id=str(share.id),
            company_name=share.company_name,
            total_shares=share.total_shares,
            price_per_share=share.price_per_share,
            available_shares=share.available_shares,
            created_at=share.created_at.isoformat()
        )
        for share in shares
    ]
    
    await redis_client.setex(ADMIN_SHARES_KEY, 300, json.dumps([item.dict() for item in response]))
    return response




@router.delete("/shares/{shares_id}")
async def delete_shares_offering(
    shares_id: str,
    db: Session = Depends(get_db),
    # admin: User = Depends(get_current_admin)
):
    """Hard delete shares offering with cascade"""
    try:
        shares_uuid = uuid.UUID(shares_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid shares ID")
    
    shares = db.query(SharesOffering).filter(SharesOffering.id == shares_uuid).first()
    if not shares:
        raise HTTPException(status_code=404, detail="Shares offering not found")
    
    # Check if there are active holdings or transactions
    holdings_count = db.query(Holding).filter(Holding.shares_offering_id == shares_uuid).count()
    transactions_count = db.query(Transaction).filter(Transaction.shares_offering_id == shares_uuid).count()
    
    if holdings_count > 0 or transactions_count > 0:
        raise HTTPException(
            status_code=400, 
            detail="Cannot delete shares offering with active holdings or transactions"
        )
    
    db.delete(shares)
    db.commit()
    
    await invalidate_admin_cache()
    from app.shares_offering import invalidate_shares_cache
    await invalidate_shares_cache()
    
    return {"message": "Shares offering deleted successfully"}




# Transactions Management
@router.get("/transactions", response_model=List[AdminTransactionResponse])
async def get_all_transactions(
    db: Session = Depends(get_db),
    # admin: User = Depends(get_current_admin)
):
    """Get all transactions in the system"""
    cached_transactions = await redis_client.get(ADMIN_TRANSACTIONS_KEY)
    if cached_transactions:
        return json.loads(cached_transactions)
    
    transactions = db.query(Transaction).join(User).join(SharesOffering).order_by(Transaction.created_at.desc()).all()
    
    response = []
    for transaction in transactions:
        response.append(AdminTransactionResponse(
            id=str(transaction.id),
            user_id=str(transaction.user_id),
            user_name=transaction.user.name,
            type=transaction.type,
            shares_offering_id=str(transaction.shares_offering_id),
            company_name=transaction.shares_offering.company_name,
            shares_count=transaction.shares_count,
            price=transaction.price,
            total_amount=transaction.shares_count * transaction.price,
            status=transaction.status,
            created_at=transaction.created_at.isoformat()
        ))
    
    await redis_client.setex(ADMIN_TRANSACTIONS_KEY, 300, json.dumps([item.dict() for item in response]))
    return response





# Holdings Management
@router.get("/holdings", response_model=List[AdminHoldingResponse])
async def get_all_holdings(
    db: Session = Depends(get_db),
    # admin: User = Depends(get_current_admin)
):
    """Get all holdings in the system"""
    cached_holdings = await redis_client.get(ADMIN_HOLDINGS_KEY)
    if cached_holdings:
        return json.loads(cached_holdings)
    
    holdings = db.query(Holding).join(User).join(SharesOffering).order_by(Holding.created_at.desc()).all()
    
    response = []
    for holding in holdings:
        current_value = holding.shares_owned * holding.shares_offering.price_per_share
        response.append(AdminHoldingResponse(
            id=str(holding.id),
            user_id=str(holding.user_id),
            user_name=holding.user.name,
            shares_offering_id=str(holding.shares_offering_id),
            company_name=holding.shares_offering.company_name,
            shares_owned=holding.shares_owned,
            average_price=holding.average_price,
            current_value=current_value,
            created_at=holding.created_at.isoformat()
        ))
    
    await redis_client.setex(ADMIN_HOLDINGS_KEY, 300, json.dumps([item.dict() for item in response]))
    return response




# Dividends Management
@router.post("/dividends", response_model=DividendResponse)
async def create_dividend(
    dividend_data: DividendCreate,
    db: Session = Depends(get_db),
    # admin: User = Depends(get_current_admin)
):
    """Create dividend for shares offering"""
    try:
        shares_uuid = uuid.UUID(dividend_data.shares_offering_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid shares offering ID")
    
    shares_offering = db.query(SharesOffering).filter(SharesOffering.id == shares_uuid).first()
    if not shares_offering:
        raise HTTPException(status_code=404, detail="Shares offering not found")
    
    # Create dividend
    dividend = Dividend(
        shares_offering_id=shares_uuid,
        amount_per_share=dividend_data.amount_per_share
    )
    
    db.add(dividend)
    db.commit()
    db.refresh(dividend)
    
    # Create dividend payouts for all holders
    holdings = db.query(Holding).filter(Holding.shares_offering_id == shares_uuid).all()
    for holding in holdings:
        amount_received = holding.shares_owned * dividend_data.amount_per_share
        payout = DividendPayout(
            user_id=holding.user_id,
            dividend_id=dividend.id,
            amount_received=amount_received,
            status='pending'
        )
        db.add(payout)
    
    db.commit()
    
    await invalidate_admin_cache()
    
    return DividendResponse(
        id=str(dividend.id),
        shares_offering_id=str(dividend.shares_offering_id),
        company_name=shares_offering.company_name,
        amount_per_share=dividend.amount_per_share,
        declared_at=dividend.declared_at.isoformat()
    )




@router.get("/dividends", response_model=List[DividendResponse])
async def get_all_dividends(
    db: Session = Depends(get_db),
    # admin: User = Depends(get_current_admin)
):
    """Get all dividends"""
    cached_dividends = await redis_client.get(ADMIN_DIVIDENDS_KEY)
    if cached_dividends:
        return json.loads(cached_dividends)
    
    dividends = db.query(Dividend).join(SharesOffering).order_by(Dividend.declared_at.desc()).all()
    
    response = [
        DividendResponse(
            id=str(dividend.id),
            shares_offering_id=str(dividend.shares_offering_id),
            company_name=dividend.shares_offering.company_name,
            amount_per_share=dividend.amount_per_share,
            declared_at=dividend.declared_at.isoformat()
        )
        for dividend in dividends
    ]
    
    await redis_client.setex(ADMIN_DIVIDENDS_KEY, 300, json.dumps([item.dict() for item in response]))
    return response




@router.get("/dividends/payouts", response_model=List[DividendPayoutResponse])
async def get_dividend_payouts(
    dividend_id: Optional[str] = None,
    db: Session = Depends(get_db),
    # admin: User = Depends(get_current_admin)
):
    """Get dividend payouts, optionally filtered by dividend"""
    query = db.query(DividendPayout).join(User).join(Dividend).join(SharesOffering)
    
    if dividend_id:
        try:
            dividend_uuid = uuid.UUID(dividend_id)
            query = query.filter(DividendPayout.dividend_id == dividend_uuid)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid dividend ID")
    
    payouts = query.order_by(DividendPayout.paid_at.desc()).all()
    
    response = []
    for payout in payouts:
        response.append(DividendPayoutResponse(
            id=str(payout.id),
            user_id=str(payout.user_id),
            user_name=payout.user.name,
            dividend_id=str(payout.dividend_id),
            company_name=payout.dividend.shares_offering.company_name,
            amount_per_share=payout.dividend.amount_per_share,
            shares_owned=int(payout.amount_received / payout.dividend.amount_per_share),
            amount_received=payout.amount_received,
            status=payout.status,
            paid_at=payout.paid_at.isoformat() if payout.paid_at else None
        ))
    
    return response




@router.post("/dividends/payouts/{payout_id}/pay")
async def process_dividend_payout(
    payout_id: str,
    db: Session = Depends(get_db),
    # admin: User = Depends(get_current_admin)
):
    """Mark dividend payout as paid"""
    try:
        payout_uuid = uuid.UUID(payout_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payout ID")
    
    payout = db.query(DividendPayout).filter(DividendPayout.id == payout_uuid).first()
    if not payout:
        raise HTTPException(status_code=404, detail="Dividend payout not found")
    
    if payout.status == 'paid':
        raise HTTPException(status_code=400, detail="Payout already paid")
    
    payout.status = 'paid'
    payout.paid_at = db.func.now()
    db.commit()
    
    await invalidate_admin_cache()
    
    return {"message": "Dividend payout marked as paid"}




@router.delete("/dividends/{dividend_id}")
async def delete_dividend(
    dividend_id: str,
    db: Session = Depends(get_db),
    # admin: User = Depends(get_current_admin)
):
    """Delete dividend and its payouts"""
    try:
        dividend_uuid = uuid.UUID(dividend_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid dividend ID")
    
    dividend = db.query(Dividend).filter(Dividend.id == dividend_uuid).first()
    if not dividend:
        raise HTTPException(status_code=404, detail="Dividend not found")
    
    # Delete dividend payouts first (cascade)
    db.query(DividendPayout).filter(DividendPayout.dividend_id == dividend_uuid).delete()
    
    # Delete dividend
    db.delete(dividend)
    db.commit()
    
    await invalidate_admin_cache()
    
    return {"message": "Dividend deleted successfully"}


@router.post("/create-admin")
async def create_first_admin(
    request: CreateAdminRequest,
    db: Session = Depends(get_db)
):

    existing_admin = db.query(User).filter(User.role == "admin").first()
    if existing_admin:
        raise HTTPException(
            status_code=400,
            detail="Admin user already exists. Cannot create another."
        )

    existing_user = db.query(User).filter(User.phone == request.phone).first()
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="User with this phone already exists"
        )

    admin_user = User(
        name="Administrator",
        phone=request.phone,
        password_hash=hash_password(request.password),
        role="admin",
        is_active=True
    )
    db.add(admin_user)
    db.commit()
    db.refresh(admin_user)

    return {
        "message": "Admin created successfully",
        "phone": admin_user.phone,
        "role": admin_user.role
    }