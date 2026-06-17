import httpx
from typing import Dict, Any, Optional
from config import settings

async def get_fear_greed() -> Optional[Dict[str, Any]]:
    """Fetches Fear & Greed index from alternative.me API with local fallback."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(settings.fear_greed_url)
            if response.status_code == 200:
                data = response.json()
                results = data.get("data", [])
                if results:
                    fng_item = results[0]
                    value = int(fng_item.get("value", 50))
                    
                    # Classify label
                    if value <= 25:
                        label = "Extreme Fear"
                        annotation = "AI halves position size in Extreme Fear and demands score ≥ 80 setups."
                    elif value <= 45:
                        label = "Fear"
                        annotation = "AI scales back position sizes due to general market fear."
                    elif value >= 75:
                        label = "Extreme Greed"
                        annotation = "AI limits exposure to avoid chasing market top in Extreme Greed."
                    elif value >= 55:
                        label = "Greed"
                        annotation = "AI operates with standard weights; momentum favored."
                    else:
                        label = "Neutral"
                        annotation = "Normal market conditions; default sizing active."
                        
                    return {
                        "value": value,
                        "label": label,
                        "annotation": annotation
                    }
    except Exception as e:
        print(f"[DATA WARNING] Failed to fetch Fear & Greed index: {e}")
    
    # Fallback default
    return {
        "value": 50,
        "label": "Neutral",
        "annotation": "Alternative.me offline. Defaulting to neutral regime."
    }
