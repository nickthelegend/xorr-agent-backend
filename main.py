from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone
import asyncio

from config import settings
from persistence.db import init_db
from api.deps import get_session

# Import routes
from api.overview import router as overview_router
from api.trades import router as trades_router
from api.brain import router as brain_router
from api.wallet import router as wallet_router
from api.settings_routes import router as settings_router
from api.engine_routes import router as engine_router
from api.stream import router as stream_router
from api.backtest import router as backtest_router
from api.learning import router as learning_router
from api.mcp import router as mcp_router

app = FastAPI(
    title="Xorr Agent API",
    version="0.1.0",
    description="Autonomous Trading Agent backend API for BNB Chain"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    # Initialize SQLite tables
    init_db()
    
    # Reconcile local state with on-chain truth
    from sqlmodel import Session
    from persistence.db import engine as db_engine
    from persistence.repo import get_state
    from core.twak_executor import TwakExecutor
    from core.wallet import WalletManager
    from core.reconciler import reconcile_on_boot
    
    with Session(db_engine) as session:
        state = get_state(session)
        executor = TwakExecutor(settings, simulation=(state.mode == "simulation"))
        wallet_mgr = WalletManager(executor)
        # Reconcile positions and log starting equity
        await reconcile_on_boot(session, wallet_mgr)
        
    # Start engine scheduler background tasks
    from engine.scheduler import scheduler
    scheduler.start()
    
    # Start news polling loop in background
    from data.binance_news import start_news_polling_loop
    asyncio.create_task(start_news_polling_loop(interval_sec=60))
    
    print("[XORR] Database initialized, reconciled on-chain state. Engine scheduler and news active.")

@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat()
    }

# Register routers
app.include_router(overview_router, prefix="/api")
app.include_router(trades_router, prefix="/api")
app.include_router(brain_router, prefix="/api")
app.include_router(wallet_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(engine_router, prefix="/api")
app.include_router(stream_router, prefix="/api")
app.include_router(backtest_router, prefix="/api")
app.include_router(learning_router, prefix="/api")
app.include_router(mcp_router, prefix="/api")
