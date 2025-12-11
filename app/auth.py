from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.redis_client import get_redis_client
from pydantic import BaseModel
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
import os
import hashlib
import random
import string
import httpx
from typing import Dict, Any
import json
import logging 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

SECRET_KEY = os.getenv("SECRET_KEY", "your-super-secret-jwt-key-change-this-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

NOTIFY_AFRICA_API_TOKEN = os.getenv("NOTIFY_AFRICA_API_TOKEN", "ntfy_386655e50c40f60f1b7c28063b1b4be27f7aed3e8a7d3c5794e300caba9929c7")
NOTIFY_AFRICA_SENDER_ID = os.getenv("NOTIFY_AFRICA_SENDER_ID", "32")
NOTIFY_AFRICA_BASE_URL = os.getenv("NOTIFY_AFRICA_BASE_URL", "https://api.notify.africa/api/v1/api/messages/send")

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

# Redis key patterns
OTP_KEY = "otp:{phone}:{purpose}"

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return hash_password(plain_password) == hashed_password

def generate_otp() -> str:
    return ''.join(random.choices(string.digits, k=6))

def send_otp_sms(phone: str, otp: str) -> bool:
    logger.info(f"--- SMS START: Preparing to send OTP to {phone} ---")

    if not all([NOTIFY_AFRICA_API_TOKEN, NOTIFY_AFRICA_SENDER_ID, NOTIFY_AFRICA_BASE_URL]):
        logger.warning("MISSING CONFIG: SMS environment variables are not set. Simulating success.")
        print(f"DEBUG OTP for {phone}: {otp}") 
        return True

    clean_phone = phone.replace("+", "").strip()
    if clean_phone.startswith("0") and len(clean_phone) == 10:
        clean_phone = "255" + clean_phone[1:]
    elif len(clean_phone) == 9:
        clean_phone = "255" + clean_phone
    
    url = NOTIFY_AFRICA_BASE_URL 
    
    payload = {
        "phone_number": clean_phone, 
        "message": f"Your FALTASI WEALTH verification code is: {otp}",
        "sender_id": NOTIFY_AFRICA_SENDER_ID 
    }
    
    logger.info(f"SMS PAYLOAD: {json.dumps(payload)}")
    
    headers = {
        "Authorization": f"Bearer {NOTIFY_AFRICA_API_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    try:
        logger.info(f"SMS REQUEST: Sending POST to {url}")
        response = httpx.post(url, json=payload, headers=headers, timeout=30)
        
        logger.info(f"SMS RESPONSE CODE: {response.status_code}")
        logger.info(f"SMS RESPONSE BODY: {response.text}")
        
        # Working system accepts 200 or 202
        if response.status_code in [200, 202]:
            try:
                data = response.json()
                if data.get("success", False): 
                    logger.info("--- SMS SUCCESS: Message sent successfully ---")
                    return True
                else:
                    logger.error(f"--- SMS FAILURE: API returned success=False. Msg: {data.get('message')} ---")
                    return False
            except json.JSONDecodeError:
                # If it's 200 but not JSON, assume it worked (fallback)
                logger.warning("--- SMS WARNING: 200 OK but response was not JSON ---")
                return True
        else:
            logger.error(f"--- SMS FAILURE: API returned error status {response.status_code} ---")
            return False
            
    except httpx.TimeoutException:
        logger.error("--- SMS ERROR: Request timed out ---")
        return False
    except Exception as e:
        logger.error(f"--- SMS ERROR: Unexpected exception: {str(e)} ---")
        return False
async def store_otp_in_redis(phone: str, otp: str, purpose: str, expires_in: int = 300):
    """Store OTP in Redis with expiration"""
    redis = await get_redis_client()
    
    otp_key = OTP_KEY.format(phone=phone, purpose=purpose)
    otp_data = {
        "otp_hash": hash_password(otp),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
    }
    await redis.setex(otp_key, expires_in, json.dumps(otp_data))

async def verify_otp_from_redis(phone: str, otp: str, purpose: str) -> bool:
    """Verify OTP from Redis"""
    # Use get_redis_client() helper
    redis = await get_redis_client()
    
    otp_key = OTP_KEY.format(phone=phone, purpose=purpose)
    otp_data = await redis.get(otp_key)
    
    if not otp_data:
        return False
    
    otp_info = json.loads(otp_data)
    
    # Check if OTP is expired
    expires_at = datetime.fromisoformat(otp_info["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        await redis.delete(otp_key)
        return False
    
    # Verify OTP
    if verify_password(otp, otp_info["otp_hash"]):
        await redis.delete(otp_key) 
        return True
    
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
async def request_registration_otp(
    phone_data: ForgotPasswordRequest, 
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.phone == phone_data.phone).first()
    if user:
        raise HTTPException(status_code=400, detail="Phone already registered")
    
    otp = generate_otp()
    
    # Store OTP in Redis instead of database
    await store_otp_in_redis(phone_data.phone, otp, "register", 300)
    
    background_tasks.add_task(send_otp_sms, phone_data.phone, otp)
    return {"message": "OTP sent", "phone": phone_data.phone}

@router.post("/register/verify", response_model=TokenResponse)
async def verify_registration_otp(otp_data: OTPVerification, db: Session = Depends(get_db)):
    # Verify OTP from Redis
    if not await verify_otp_from_redis(otp_data.phone, otp_data.otp, "register"):
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
 
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
async def complete_registration(user_data: UserCreate, current_user=Depends(get_current_user)):
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
async def login(user_data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == user_data.phone).first()
    if not user or not verify_password(user_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    return create_tokens(user)

@router.post("/forgot-password/otp")
async def request_forgot_password_otp(
    phone_data: ForgotPasswordRequest, 
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.phone == phone_data.phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    otp = generate_otp()
    
    # Store OTP in Redis
    await store_otp_in_redis(phone_data.phone, otp, "reset", 300)
    
    background_tasks.add_task(send_otp_sms, phone_data.phone, otp)
    return {"message": "Reset OTP sent", "phone": phone_data.phone}

@router.post("/reset-password")
async def reset_password(reset_data: ResetPassword, db: Session = Depends(get_db)):
    # Verify OTP from Redis
    if not await verify_otp_from_redis(reset_data.phone, reset_data.otp, "reset"):
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    
    # Update password
    user = db.query(User).filter(User.phone == reset_data.phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.password_hash = hash_password(reset_data.new_password)
    db.commit()
    
    return {"message": "Password reset successful"}

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_data: RefreshToken, db: Session = Depends(get_db)):
    payload = verify_token(refresh_data.refresh_token, "refresh")
    phone = payload.get("sub")
    
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    return create_tokens(user)

@router.get("/me", response_model=UserResponse)
async def read_current_user(current_user = Depends(get_current_user)):
    return UserResponse(
        id=str(current_user.id),
        name=current_user.name,
        phone=current_user.phone,
        role=current_user.role
    )