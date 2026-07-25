"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    """Return system health status."""
    return {
        "status": "ok",
        "version": "0.1.0",
        "service": "NL2SQL Data Engineering Agent",
    }
