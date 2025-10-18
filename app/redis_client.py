import os
import aioredis
from typing import Optional


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Global redis client instance
redis_client: Optional[aioredis.Redis] = None

async def get_redis_client() -> aioredis.Redis:
    """Get Redis client instance"""
    global redis_client
    if redis_client is None:
        redis_client = await aioredis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping()
    return redis_client

async def close_redis_client():
    """Close Redis connection"""
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None