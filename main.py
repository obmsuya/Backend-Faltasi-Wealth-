from fastapi import FastAPI
from datetime import datetime
app = FastAPI(
    title="FALTASI WEALTH API",  
    description="Share trading platform MVP", 
    version="1.0.0" 
)

@app.get("/")
def read_root():
    return {
        "message": "Welcome to FALTASI WEALTH API!",
        "status": "running",
        "version": "1.0.0",
        "endpoints": [
            "GET / - this message",
            "GET /docs - Swagger UI",
            "GET /health - health check"
        ]
    }

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now()
    }

@app.get("/database")
def database_status():
    return {
        "status": "Database integration pending",
        "database": "PostgreSQL (to be connected)"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)