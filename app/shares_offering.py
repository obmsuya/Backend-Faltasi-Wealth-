from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models import SharesOffering, User
from app.auth import get_current_user, get_current_admin
from pydantic import BaseModel
import uuid
from datetime import datetime

router = APIRouter(prefix="/shares", tags=["shares"])

class SharesOfferingCreate(BaseModel):
    company_name: str
    total_shares: int
    price_per_share: float

class SharesOfferingResponse(BaseModel):
    id: uuid.UUID
    company_name: str
    total_shares: int
    price_per_share: float
    available_shares: int
    created_at: datetime

class SharesOfferingUpdate(BaseModel):  
    price_per_share: Optional[float] = None
    available_shares: Optional[int] = None

@router.post("/", response_model=SharesOfferingResponse)   
def create_shares_offering(shares_data: SharesOfferingCreate, db: Session = Depends(get_db), admin: User=Depends(get_current_admin)):
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
    
    return SharesOfferingResponse(
        id=str(db_shares.id),
        company_name=db_shares.company_name,
        total_shares=db_shares.total_shares,
        price_per_share=db_shares.price_per_share,
        available_shares=db_shares.available_shares,
        created_at=db_shares.created_at.isoformat()
    )

@router.get("/", response_model=List[SharesOfferingResponse])
def get_available_shares(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all available shares offerings"""
    shares = db.query(SharesOffering).filter(
        SharesOffering.available_shares > 0
    ).all()
    
    return [
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

@router.get("/{shares_id}", response_model=SharesOfferingResponse)
def get_shares_details(
    shares_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get specific shares offering details"""
    try:
        share_uuid = uuid.UUID(shares_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid shares ID")
    
    share = db.query(SharesOffering).filter(SharesOffering.id == share_uuid).first()
    if not share:
        raise HTTPException(status_code=404, detail="Shares offering not found")
    
    return SharesOfferingResponse(
        id=str(share.id),
        company_name=share.company_name,
        total_shares=share.total_shares,
        price_per_share=share.price_per_share,
        available_shares=share.available_shares,
        created_at=share.created_at.isoformat()
    )

@router.put("/{shares_id}", response_model=SharesOfferingResponse)
def update_shares_offering(
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
    
    return SharesOfferingResponse(
        id=str(share.id),
        company_name=share.company_name,
        total_shares=share.total_shares,
        price_per_share=share.price_per_share,
        available_shares=share.available_shares,
        created_at=share.created_at.isoformat()
    )

