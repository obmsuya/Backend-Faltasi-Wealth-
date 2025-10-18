# main.py - Updated with JWT security information
from fastapi import FastAPI
from contextlib import asynccontextmanager
from datetime import datetime
from app.transactions import router as transactions_router
from app.shares_offering import router as shares_offering_router
from app.redis_client import get_redis_client, close_redis_client

from app.redis_client import get_redis_client, close_redis_client

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize Redis connection
    await get_redis_client()  # Initialize the connection
    print("Redis client initialized")
    yield
    # Shutdown: Close Redis connection
    await close_redis_client()
    print("Redis client closed")

app = FastAPI(
    title="Shared Buying App", 
    version="1.0.0",
    lifespan=lifespan
)


# Import routers - this registers the endpoints automatically
from app.auth import router as auth_router
from app.shares import router as shares_router
from app.transactions import router as transactions_router
from app.shares_offering import router as shares_offering_router
from app.portfolio import router as portfolio_router

# Include routers
app.include_router(auth_router)
app.include_router(shares_router)
app.include_router(transactions_router)
app.include_router(shares_offering_router)
app.include_router(portfolio_router)


# Basic endpoints
@app.get("/")
def read_root():
    return {
        "message": "Welcome to FALTASI WEALTH API!",
        "status": "running",
        "version": "1.0.0",
        "authentication": {
            "how_to_use": "Register -> Login -> Use access_token in Authorization: Bearer <token>",
            "endpoints": [
                "POST /auth/register - Create investor account (no auth needed)",
                "POST /auth/login - Get access/refresh tokens",
                "POST /auth/refresh - Refresh access token",
                "GET /auth/me - Get current user (requires auth)",
                "GET /shares/ - List available shares (no auth for now)",
                "POST /shares/ - Create new share offering (admin only)",
                "GET /transactions/ - List user transactions (requires auth)",
                "POST /transactions/buy - Buy shares (requires auth)",
                "POST /transactions/sell - Sell shares (requires auth)",
                "POST /transactions/{transaction_id}/approve - Approve transaction (admin only)",
                "POST /shares_offering/ - Create new share offering (admin only)",
                "GET /shares_offering/ - List all share offerings (no auth for now)",
                "GET /shares_offering/{id} - Get specific share offering (no auth for now)",
                "PUT /shares_offering/{id} - Update share offering (admin only)",
                "DELETE /shares_offering/{id} - Delete share offering (admin only)",
            ]
        }
    }

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now(),
        "jwt": "PyJWT 2.10.1 loaded successfully"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)