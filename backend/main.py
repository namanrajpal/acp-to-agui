"""FastAPI main application for the ACP → AG-UI Bridge."""

import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import __version__
from backend.config import load_config
from backend.types.api import HealthResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

config = load_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager for startup/shutdown."""
    logger.info(f"Starting {config.display_title} v{__version__}")

    app.state.config = config

    from backend.sessions.store import SessionStore
    session_store = SessionStore(db_path=config.db_path)
    await session_store.initialize()
    app.state.session_store = session_store

    from backend.sessions.manager import SessionManager
    session_manager = SessionManager(session_store, agent_command=config.agent_command)
    app.state.session_manager = session_manager

    yield

    logger.info("Shutting down ACP → AG-UI Bridge")
    await session_manager.shutdown()
    await session_store.close()


app = FastAPI(
    title=config.display_title,
    description=config.description,
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="ok", version=__version__, project=config.project_name)


from backend.api import files, git

app.include_router(files.router, prefix="/api", tags=["files"])
app.include_router(git.router, prefix="/api", tags=["git"])

from backend.sessions.routes import router as sessions_router

app.include_router(sessions_router, tags=["sessions"])
