"""
FastAPI application entrypoint.
"""

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.config import settings
from app.auth.jwt import (
    LoginRequest,
    TokenResponse,
    login as do_login,
    get_current_user,
)
from app.routers import companies, gantt, meetings, commitments, gtm

# OpenAPI Tags Metadata
tags_metadata = [
    {
        "name": "health",
        "description": "Health check endpoints for monitoring service availability.",
    },
    {
        "name": "auth",
        "description": "Authentication endpoints for user login and token management.",
    },
    {
        "name": "companies",
        "description": "Manage portfolio companies including creation, retrieval, updates, and deletion. "
                       "Companies are the core entities in the system, representing portfolio investments.",
    },
    {
        "name": "gantt",
        "description": "Gantt chart data management including pulling data from Google Sheets, "
                       "computing KPIs (shipping velocity, execution speed, planning depth), "
                       "generating diffs between snapshots, and providing portfolio overview analytics.",
    },
    {
        "name": "meetings",
        "description": "Meeting notes management including DOCX upload, AI-powered parsing with Gemini, "
                       "automatic commitment extraction, and meeting history retrieval. "
                       "Meetings are parsed to extract summaries, decisions, risks, and actionable commitments.",
    },
    {
        "name": "commitments",
        "description": "Commitment tracking and status management. Commitments are actionable items "
                       "extracted from meetings with due dates, assignees, and automatic status updates "
                       "(open, due-soon, overdue, resolved).",
    },
    {
        "name": "granola",
        "description": "Granola AI note-taking app integration. Sync meeting notes directly from Granola "
                       "for individual companies or all portfolio companies in parallel using incremental cursors.",
    },
    {
        "name": "gtm",
        "description": "Go-To-Market (GTM) plan management. Upload GTM playbooks and generate AI-powered "
                       "personalised GTM plans per portfolio company based on meeting history and the active playbook.",
    },
]

app = FastAPI(
    title="AJVC Portfolio Intelligence Dashboard API",
    description="""
## 🚀 AJVC Portfolio Intelligence Dashboard - Backend API

A comprehensive **portfolio management system** for venture capital firms to track portfolio companies,
analyze project execution, monitor commitments, and gain AI-powered insights from meeting notes.

### Key Features

* **📊 Company Management**: Track portfolio companies with investment details and status
* **📈 Gantt Layer Analytics**: Pull data from Google Sheets and compute execution KPIs
* **📝 AI-Powered Meeting Analysis**: Upload DOCX meeting notes for automatic parsing with Google Gemini
* **✅ Commitment Tracking**: Automatically extract and track actionable commitments from meetings
* **🤝 Granola Integration**: Sync meeting notes directly from the Granola AI note-taking app
* **🚀 GTM Plan Generation**: AI-powered Go-To-Market plans per portfolio company
* ** Secure Authentication**: JWT-based authentication for all protected endpoints

### Tech Stack

* **FastAPI** for high-performance async API
* **PostgreSQL** with SQLAlchemy ORM
* **Google Gemini AI** for intelligent meeting parsing
* **Google Sheets API** for Gantt data integration
* **Granola API** for meeting note synchronisation
* **JWT Authentication** for secure access

### Getting Started

1. Authenticate via `/api/v1/auth/login` to get a JWT token
2. Include the token in the `Authorization: Bearer <token>` header
3. Create or select a company via `/api/v1/companies`
4. Upload meeting notes or pull Gantt data for analysis
5. Sync Granola notes per-company via `/api/v1/companies/{company_id}/meetings/sync-from-granola`

### API Versioning

Current version: **v1.0.0** - All endpoints are prefixed with `/api/v1`
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=tags_metadata,
    contact={
        "name": "AJVC Portfolio Intelligence",
        "email": "support@ajvc.in",
    },
    license_info={
        "name": "Proprietary",
    },
)

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth router (inline — minimal) ───────────────────────────────────────────
from fastapi import APIRouter

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@auth_router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    return do_login(body.password)


app.include_router(auth_router)

# ── Domain routers ────────────────────────────────────────────────────────────
app.include_router(companies.router, prefix="/api/v1")
app.include_router(gantt.router, prefix="/api/v1")
app.include_router(meetings.router, prefix="/api/v1")
app.include_router(commitments.router, prefix="/api/v1")
app.include_router(gtm.router, prefix="/api/v1")


# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}


