"""Dashboard-Route – liefert die Single-Page-Dashboard-Anwendung aus."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Gibt das Dashboard als HTML-Seite zurück."""
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "admin_key": "",
    })
