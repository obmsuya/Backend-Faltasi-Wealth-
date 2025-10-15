from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import Holding, SharesOffering, DividendPayout, Dividend, User
from app.auth import get_current_user
from pydantic import BaseModel

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

class HoldingResponse(BaseModel):
    id: str
    shares_offering_id: str
    company_name: str
    shares_owned: int
    average_price: float
    current_price: float
    current_value: float
    profit_loss: float

class PortfolioSummary(BaseModel):
    total_investment: float
    current_value: float
    total_profit_loss: float
    holdings: List[HoldingResponse]

class DividendResponse(BaseModel):
    id: str
    company_name: str
    amount_per_share: float
    shares_owned: int
    amount_received: float
    paid_at: str
    status: str

@router.get("/holdings", response_model=PortfolioSummary)
def get_portfolio_holdings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get user's portfolio holdings with current values"""
    holdings = db.query(Holding).filter(Holding.user_id == current_user.id).all()
    
    holdings_response = []
    total_investment = 0
    current_value = 0
    
    for holding in holdings:
        shares_offering = db.query(SharesOffering).filter(
            SharesOffering.id == holding.shares_offering_id
        ).first()
        
        if shares_offering:
            investment_value = holding.shares_owned * holding.average_price
            current_holding_value = holding.shares_owned * shares_offering.price_per_share
            profit_loss = current_holding_value - investment_value
            
            total_investment += investment_value
            current_value += current_holding_value
            
            holdings_response.append(HoldingResponse(
                id=str(holding.id),
                shares_offering_id=str(holding.shares_offering_id),
                company_name=shares_offering.company_name,
                shares_owned=holding.shares_owned,
                average_price=holding.average_price,
                current_price=shares_offering.price_per_share,
                current_value=current_holding_value,
                profit_loss=profit_loss
            ))
    
    return PortfolioSummary(
        total_investment=total_investment,
        current_value=current_value,
        total_profit_loss=current_value - total_investment,
        holdings=holdings_response
    )

@router.get("/dividends", response_model=List[DividendResponse])
def get_dividend_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get user's dividend payout history"""
    dividend_payouts = db.query(DividendPayout).filter(
        DividendPayout.user_id == current_user.id
    ).order_by(DividendPayout.paid_at.desc()).all()
    
    response = []
    for payout in dividend_payouts:
        dividend = db.query(Dividend).filter(Dividend.id == payout.dividend_id).first()
        shares_offering = db.query(SharesOffering).filter(
            SharesOffering.id == dividend.shares_offering_id
        ).first() if dividend else None
        
        # Get shares owned at time of dividend (simplified - using current holding)
        holding = db.query(Holding).filter(
            Holding.user_id == current_user.id,
            Holding.shares_offering_id == dividend.shares_offering_id
        ).first() if dividend else None
        
        response.append(DividendResponse(
            id=str(payout.id),
            company_name=shares_offering.company_name if shares_offering else "Unknown",
            amount_per_share=dividend.amount_per_share if dividend else 0,
            shares_owned=holding.shares_owned if holding else 0,
            amount_received=payout.amount_received,
            paid_at=payout.paid_at.isoformat() if payout.paid_at else "Pending",
            status=payout.status
        ))
    
    return response