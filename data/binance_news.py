import httpx
import re
import asyncio
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, List, Set
from config import settings

@dataclass
class NewsEvent:
    symbol: str
    kind: str  # "will_list" | "new_listing" | "seed_tag"
    ts: datetime
    url: str
    title: str

# Queue for news catalyst strategy
news_queue = asyncio.Queue()

# In-memory list of recent news events for the news catalyst strategy
# Each element: (symbol, title, timestamp)
recent_listing_events = []

# Set of seen article IDs to de-dupe
_seen_article_ids: Set[str] = set()

def extract_symbol_from_title(title: str) -> Optional[str]:
    """Tries to extract the token symbol from the announcement title."""
    # Look for patterns like (ZRO), (TON), etc.
    m = re.search(r'\(([A-Z0-9]{2,10})\)', title)
    if m:
        return m.group(1).upper()
        
    # Look for "Will List TokenName (SYMBOL)"
    m2 = re.search(r'Will List\s+.*?([A-Z0-9]{2,10})', title, re.IGNORECASE)
    if m2:
        return m2.group(1).upper()
        
    return None

async def poll_binance_announcements() -> List[NewsEvent]:
    """Polls Binance support announcements JSON API for new listing news."""
    global _seen_article_ids
    # Binance support announcements API endpoint for listings (catalogId=48)
    url = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    params = {
        "type": 1,
        "catalogId": 48,
        "pageNo": 1,
        "pageSize": 10
    }
    
    events = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                articles = data.get("data", {}).get("articles", [])
                
                for art in articles:
                    art_id = str(art.get("id"))
                    if art_id in _seen_article_ids:
                        continue
                        
                    title = art.get("title", "")
                    title_lower = title.lower()
                    
                    # Determine kind
                    kind = None
                    if "will list" in title_lower or "listing" in title_lower:
                        kind = "will_list"
                    elif "seed tag" in title_lower or "monitoring tag" in title_lower:
                        kind = "seed_tag"
                    elif "apply tag" in title_lower:
                        kind = "seed_tag"
                        
                    if kind:
                        symbol = extract_symbol_from_title(title)
                        if symbol:
                            # Parse release date (milliseconds)
                            release_date_ms = art.get("releaseDate", int(time.time() * 1000))
                            dt = datetime.fromtimestamp(release_date_ms / 1000.0, timezone.utc)
                            code = art.get("code", "")
                            detail_url = f"https://www.binance.com/en/support/announcement/{code}"
                            
                            event = NewsEvent(
                                symbol=symbol,
                                kind=kind,
                                ts=dt,
                                url=detail_url,
                                title=title
                            )
                            events.append(event)
                            
                    _seen_article_ids.add(art_id)
    except Exception as e:
        print(f"[NEWS WARNING] Failed to poll Binance announcements: {e}")
        
    return events

async def start_news_polling_loop(interval_sec: int = 60):
    """Background loop that polls for news and pushes events to news_queue."""
    global recent_listing_events
    # Seed initially so we don't treat old listings as brand new listing alerts
    initial_events = await poll_binance_announcements()
    print(f"[NEWS] Seeded announcements feed with {len(_seen_article_ids)} articles.")
    
    while True:
        try:
            await asyncio.sleep(interval_sec)
            new_events = await poll_binance_announcements()
            
            # Clean up old events from recent_listing_events (older than 5 minutes)
            now = datetime.now(timezone.utc)
            recent_listing_events = [e for e in recent_listing_events if (now - e[2]).total_seconds() < 300]
            
            for event in new_events:
                # Calculate age
                age = (now - event.ts).total_seconds()
                if age < 180:  # Only push real-time events (less than 3 minutes old)
                    print(f"[NEWS ALERT] New listing catalyst: {event.symbol} ({event.kind}) - {event.title}")
                    # Append to global list for news catalyst strategy
                    recent_listing_events.append((event.symbol, event.title, event.ts))
                    await news_queue.put(event)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[NEWS LOOP ERROR] Exception in news polling loop: {e}")
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[NEWS LOOP ERROR] Exception in news polling loop: {e}")
            await asyncio.sleep(10)
