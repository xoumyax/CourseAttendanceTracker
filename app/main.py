"""
Main FastAPI application.
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import utils
from .config import settings
from .database import Base, engine
from .routers import admin, student

BASE_DIR = Path(__file__).resolve().parent.parent

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Course Attendance Tracker",
    description="Self-hosted class attendance tracker",
    version="2.0.0"
)

# CORS is off unless CORS_ORIGINS lists origins; the bundled pages are same-origin.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # the API uses bearer tokens, not cookies
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
    )

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(admin.router)
app.include_router(student.router)


def _page(request: Request, name: str) -> HTMLResponse:
    return templates.TemplateResponse(request, name)


@app.get("/api/config")
def get_public_config():
    """Course settings the pages need (name, timezone, check-in windows, grade scale)."""
    return utils.public_config()


@app.get("/", response_class=HTMLResponse)
@app.get("/student/login", response_class=HTMLResponse)
async def student_login_page(request: Request):
    return _page(request, "student_login.html")


@app.get("/student/register", response_class=HTMLResponse)
async def student_register_page(request: Request):
    return _page(request, "student_register.html")


@app.get("/student/reset-password", response_class=HTMLResponse)
async def student_reset_password_page(request: Request):
    return _page(request, "student_reset_password.html")


@app.get("/student/guide", response_class=HTMLResponse)
async def student_guide_page(request: Request):
    return _page(request, "student_guide.html")


@app.get("/attendance", response_class=HTMLResponse)
async def student_attendance_page(request: Request):
    return _page(request, "student_attendance.html")


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    return _page(request, "admin_login.html")


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard_page(request: Request):
    return _page(request, "admin_dashboard.html")


@app.get("/health")
async def health_check():
    return {"status": "healthy", "app": "Course Attendance Tracker"}
