# app/shares.py - Share management endpoints only
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import SharesOffering

router = APIRouter(prefix="/shares_available", tags=["shares"])

@router.get("/")
def list_available_shares(db: Session = Depends(get_db)):
    offerings = db.query(SharesOffering).filter(SharesOffering.available_shares > 0).all()
    return [
        {
            "id": str(offering.id),
            "company_name": offering.company_name,
            "price_per_share": offering.price_per_share,
            "available_shares": offering.available_shares
        }
        for offering in offerings
    ]