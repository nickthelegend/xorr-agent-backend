from fastapi import APIRouter, Depends
from sqlmodel import Session
from api.deps import get_session, settings
from persistence.repo import get_state
from core.twak_executor import TwakExecutor
from core.wallet import WalletManager

router = APIRouter()

# Keep cached executor instances to avoid re-instantiating
_executors = {}

def get_wallet_manager(session: Session) -> WalletManager:
    state = get_state(session)
    is_sim = (state.mode == "simulation")
    if is_sim not in _executors:
        _executors[is_sim] = TwakExecutor(settings, simulation=is_sim)
    return WalletManager(_executors[is_sim])

@router.get("/wallet")
async def get_wallet(session: Session = Depends(get_session)):
    mgr = get_wallet_manager(session)
    state_dict = await mgr.get_state()
    return state_dict

@router.post("/wallet/refresh")
async def refresh_wallet(session: Session = Depends(get_session)):
    mgr = get_wallet_manager(session)
    state_dict = await mgr.get_state(force_refresh=True)
    return state_dict
