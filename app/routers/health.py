"""Health-Check-Endpunkt."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    """Gibt den Status der Anwendung zurück."""
    return {"status": "healthy", "service": "dreamline"}
