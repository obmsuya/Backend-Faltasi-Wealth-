from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models import SharesOffering, User, Transaction, Holding
from app.auth import get_current_user, get_current_admin
from app.redis_client import get_redis_client 
from pydantic import BaseModel
import uuid
import json

router = APIRouter(prefix="/shares", tags=["shares"])

class SharesOfferingCreate(BaseModel):
    company_name: str
    total_shares: int
    price_per_share: float

class SharesOfferingResponse(BaseModel):
    id: str
    company_name: str
    total_shares: int
    price_per_share: float
    available_shares: int
    created_at: str

class SharesOfferingUpdate(BaseModel):
    price_per_share: Optional[float] = None
    available_shares: Optional[int] = None

# Redis key patterns
SHARES_ALL_KEY = "shares:all"
SHARES_DETAIL_KEY = "shares:{id}"

async def invalidate_shares_cache():
    """Invalidate all shares-related cache"""
    redis = await get_redis_client() 
    await redis.delete(SHARES_ALL_KEY)

    keys = await redis.keys("shares:*")
    if keys:
        await redis.delete(*keys)

@router.post("/", response_model=SharesOfferingResponse)
async def create_shares_offering(
    shares_data: SharesOfferingCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Admin only - Create new shares offering"""
    db_shares = SharesOffering(
        company_name=shares_data.company_name,
        total_shares=shares_data.total_shares,
        price_per_share=shares_data.price_per_share,
        available_shares=shares_data.total_shares
    )
    
    db.add(db_shares)
    db.commit()
    db.refresh(db_shares)
    
    # Invalidate cache after creation
    await invalidate_shares_cache()
    
    return SharesOfferingResponse(
        id=str(db_shares.id),
        company_name=db_shares.company_name,
        total_shares=db_shares.total_shares,
        price_per_share=db_shares.price_per_share,
        available_shares=db_shares.available_shares,
        created_at=db_shares.created_at.isoformat()
    )

@router.get("/", response_model=List[SharesOfferingResponse])
async def get_available_shares(
    db: Session = Depends(get_db),
):
    """Get all available shares offerings"""
    redis = await get_redis_client()  

    # Try to get from cache first
    cached_shares = await redis.get(SHARES_ALL_KEY)
    if cached_shares:
        return json.loads(cached_shares)
    
    shares = db.query(SharesOffering).filter(
        SharesOffering.available_shares > 0
    ).all()
    
    response = [
        SharesOfferingResponse(
            id=str(share.id),
            company_name=share.company_name,
            total_shares=share.total_shares,
            price_per_share=share.price_per_share,
            available_shares=share.available_shares,
            created_at=share.created_at.isoformat()
        )
        for share in shares
    ]
    

    await redis.setex(SHARES_ALL_KEY, 300, json.dumps([item.dict() for item in response]))
    
    return response

@router.get("/{shares_id}", response_model=SharesOfferingResponse)
async def get_shares_details(
    shares_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get specific shares offering details"""
    try:
        share_uuid = uuid.UUID(shares_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid shares ID")
    
    redis = await get_redis_client() 
    cache_key = SHARES_DETAIL_KEY.format(id=shares_id)
    
    # Try to get from cache first
    cached_share = await redis.get(cache_key)
    if cached_share:
        return json.loads(cached_share)
    
    share = db.query(SharesOffering).filter(SharesOffering.id == share_uuid).first()
    if not share:
        raise HTTPException(status_code=404, detail="Shares offering not found")
    
    response = SharesOfferingResponse(
        id=str(share.id),
        company_name=share.company_name,
        total_shares=share.total_shares,
        price_per_share=share.price_per_share,
        available_shares=share.available_shares,
        created_at=share.created_at.isoformat()
    )
    

    await redis.setex(cache_key, 300, json.dumps(response.dict()))
    
    return response

@router.put("/{shares_id}", response_model=SharesOfferingResponse)
async def update_shares_offering(
    shares_id: str,
    update_data: SharesOfferingUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Admin only - Update shares offering"""
    try:
        share_uuid = uuid.UUID(shares_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid shares ID")
    
    share = db.query(SharesOffering).filter(SharesOffering.id == share_uuid).first()
    if not share:
        raise HTTPException(status_code=404, detail="Shares offering not found")
    
    if update_data.price_per_share is not None:
        share.price_per_share = update_data.price_per_share
    
    if update_data.available_shares is not None:
        if update_data.available_shares > share.total_shares:
            raise HTTPException(status_code=400, detail="Available shares cannot exceed total shares")
        share.available_shares = update_data.available_shares
    
    db.commit()
    db.refresh(share)
    
    # Invalidate cache after update
    await invalidate_shares_cache()
    
    return SharesOfferingResponse(
        id=str(share.id),
        company_name=share.company_name,
        total_shares=share.total_shares,
        price_per_share=share.price_per_share,
        available_shares=share.available_shares,
        created_at=share.created_at.isoformat()
    )

@router.delete("/{shares_id}")
async def delete_shares_offering(
    shares_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Admin only - Delete shares offering"""
    try:
        share_uuid = uuid.UUID(shares_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid shares ID")
    
    share = db.query(SharesOffering).filter(SharesOffering.id == share_uuid).first()
    if not share:
        raise HTTPException(status_code=404, detail="Shares offering not found")

    transactions_count = db.query(Transaction).filter(Transaction.shares_offering_id == share_uuid).count()
    holdings_count = db.query(Holding).filter(Holding.shares_offering_id == share_uuid).count()
    
    if transactions_count > 0 or holdings_count > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete shares offering with existing transactions or holdings"
        )
    
    db.delete(share)
    db.commit()
    
    # Invalidate cache after deletion
    await invalidate_shares_cache()
    
    return {"message": "Shares offering deleted successfully"}