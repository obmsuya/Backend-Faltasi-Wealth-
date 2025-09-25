from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from pydantic import BaseModel, ConfigDict
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
import os
import hashlib
import random
import string
import httpx
from typing import Dict, Any

router = APIRouter(prefix="/auth", tags=["auth"])

SECRET_KEY = os.getenv("SECRET_KEY", "your-super-secret-jwt-key-change-this-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

NOTIFY_AFRICA_API_TOKEN = os.getenv("NOTIFY_AFRICA_API_TOKEN")
NOTIFY_AFRICA_SENDER_ID = os.getenv("NOTIFY_AFRICA_SENDER_ID")
NOTIFY_AFRICA_BASE_URL = os.getenv("NOTIFY_AFRICA_BASE_URL")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

class UserCreate(BaseModel):
    name: str
    phone: str
    password: str

class UserLogin(BaseModel):
    phone: str
    password: str

class OTPVerification(BaseModel):
    phone: str
    otp: str

class ForgotPasswordRequest(BaseModel):
    phone: str

class ResetPassword(BaseModel):
    phone: str
    otp: str
    new_password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: Dict[str, Any]

class UserResponse(BaseModel):
    id: str
    name: str
    phone: str
    role: str

class RefreshToken(BaseModel):
    refresh_token: str

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return hash_password(plain_password) == hashed_password

def generate_otp() -> str:
    return ''.join(random.choices(string.digits, k=6))

def send_otp_sms(phone: str, otp: str) -> bool:
    if not all([NOTIFY_AFRICA_API_TOKEN, NOTIFY_AFRICA_SENDER_ID, NOTIFY_AFRICA_BASE_URL]):
        print(f"OTP for {phone}: {otp}") 
        return True
    
    url = f"{NOTIFY_AFRICA_BASE_URL}/sms"
    payload = {
        "recipients": [f"+{phone}"],
        "message": f"Your FALTASI WEALTH verification code is: {otp}",
        "sender": NOTIFY_AFRICA_SENDER_ID
    }
    
    headers = {
        "Authorization": f"Bearer {NOTIFY_AFRICA_API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=10)
        return response.status_code == 200
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=7)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str, token_type: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != token_type:
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = verify_token(token, "access")
    phone = payload.get("sub")
    if not phone:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    db = next(get_db())
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

async def get_current_admin(current_user = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user

def create_tokens(user: User) -> TokenResponse:
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_token_expires = timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    
    access_token = create_access_token(
        data={
            "sub": user.phone, 
            "id": str(user.id),
            "role": user.role
        }, 
        expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(
        data={
            "sub": user.phone, 
            "id": str(user.id),
            "role": user.role
        }, 
        expires_delta=refresh_token_expires
    )
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user={
            "id": str(user.id),
            "name": user.name,
            "phone": user.phone,
            "role": user.role
        }
    )

@router.post("/register/otp")
async def request_registration_otp(phone_data: ForgotPasswordRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == phone_data.phone).first()
    if user:
        raise HTTPException(status_code=400, detail="Phone already registered")
    
    otp = generate_otp()
    
    # Store OTP temporarily (in production, use Redis)
    db.execute("INSERT INTO otp_verifications (phone, otp, expires_at, purpose) VALUES (:phone, :otp, :expires_at, :purpose)",
               {"phone": phone_data.phone, "otp": hash_password(otp), "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5), "purpose": "register"})
    db.commit()
    
    background_tasks.add_task(send_otp_sms, phone_data.phone, otp)
    return {"message": "OTP sent", "phone": phone_data.phone}

@router.post("/register/verify", response_model=TokenResponse)
def verify_registration_otp(otp_data: OTPVerification, db: Session = Depends(get_db)):
    # Verify OTP
    verification = db.execute(
        "SELECT * FROM otp_verifications WHERE phone = :phone AND purpose = 'register' AND expires_at > NOW()",
        {"phone": otp_data.phone}
    ).fetchone()
    
    if not verification or not verify_password(otp_data.otp, verification[1]):
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    
    # Delete used OTP
    db.execute("DELETE FROM otp_verifications WHERE phone = :phone AND purpose = 'register'", {"phone": otp_data.phone})
 
    user_data = UserCreate(name="New User", phone=otp_data.phone, password="temp")  # Password set later
    
    db_user = User(
        name=user_data.name,
        phone=user_data.phone,
        password_hash=hash_password(user_data.password),
        role="investor"
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    return create_tokens(db_user)

@router.post("/register/complete", response_model=TokenResponse)
def complete_registration(user_data: UserCreate, current_user=Depends(get_current_user)):
    # Update user with real name and password
    db = next(get_db())
    user = db.query(User).filter(User.phone == user_data.phone).first()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")
    
    user.name = user_data.name
    user.password_hash = hash_password(user_data.password)
    db.commit()
    
    return create_tokens(user)

@router.post("/login", response_model=TokenResponse)
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == user_data.phone).first()
    if not user or not verify_password(user_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    return create_tokens(user)

@router.post("/forgot-password/otp")
async def request_forgot_password_otp(phone_data: ForgotPasswordRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == phone_data.phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    otp = generate_otp()
    
    db.execute("INSERT INTO otp_verifications (phone, otp, expires_at, purpose) VALUES (:phone, :otp, :expires_at, :purpose)",
               {"phone": phone_data.phone, "otp": hash_password(otp), "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5), "purpose": "reset"})
    db.commit()
    
    background_tasks.add_task(send_otp_sms, phone_data.phone, otp)
    return {"message": "Reset OTP sent", "phone": phone_data.phone}

@router.post("/reset-password")
def reset_password(reset_data: ResetPassword, db: Session = Depends(get_db)):
    # Verify OTP
    verification = db.execute(
        "SELECT * FROM otp_verifications WHERE phone = :phone AND purpose = 'reset' AND expires_at > NOW()",
        {"phone": reset_data.phone}
    ).fetchone()
    
    if not verification or not verify_password(reset_data.otp, verification[1]):
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    
    # Update password
    user = db.query(User).filter(User.phone == reset_data.phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.password_hash = hash_password(reset_data.new_password)
    db.execute("DELETE FROM otp_verifications WHERE phone = :phone AND purpose = 'reset'", {"phone": reset_data.phone})
    db.commit()
    
    return {"message": "Password reset successful"}

@router.post("/refresh", response_model=TokenResponse)
def refresh_token(refresh_data: RefreshToken, db: Session = Depends(get_db)):
    payload = verify_token(refresh_data.refresh_token, "refresh")
    phone = payload.get("sub")
    
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    return create_tokens(user)

@router.get("/me", response_model=UserResponse)
def read_current_user(current_user = Depends(get_current_user)):
    return UserResponse(
        id=str(current_user.id),
        name=current_user.name,
        phone=current_user.phone,
        role=current_user.role
    )