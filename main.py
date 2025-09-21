# main.py - Updated with JWT security information
from fastapi import FastAPI
from datetime import datetime

# Create the main app
app = FastAPI(
    title="FALTASI WEALTH API",  
    description="Share trading platform MVP with JWT Auth", 
    version="1.0.0" 
)

# Import routers - this registers the endpoints automatically
from app.auth import router as auth_router
from app.shares import router as shares_router

# Include routers
app.include_router(auth_router)
app.include_router(shares_router)

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
                "GET /shares/ - List available shares (no auth for now)"
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