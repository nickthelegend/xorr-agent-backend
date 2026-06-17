import math
import time
from datetime import datetime, timezone, timedelta
from typing import Tuple, List, Optional
from skyfield.api import load
from skyfield.framelib import ecliptic_frame
from config import settings

NAK_ARC = 360.0 / 27.0          # 13.3333° per nakshatra
TITHI_ARC = 12.0               # 12° of elongation per tithi

NAKSHATRA_NAMES = [
    "Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra",
    "Punarvasu", "Pushya", "Ashlesha", "Magha", "Purva Phalguni",
    "Uttara Phalguni", "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha",
    "Jyeshtha", "Mula", "Purva Ashadha", "Uttara Ashadha", "Shravana",
    "Dhanishta", "Shatabhisha", "Purva Bhadrapada", "Uttara Bhadrapada",
    "Revati"
]

HORA_SEQUENCE = ["SUN", "VENUS", "MERCURY", "MOON", "SATURN", "JUPITER", "MARS"]
DAY_RULERS = {0: "MOON", 1: "MARS", 2: "MERCURY", 3: "JUPITER", 4: "VENUS", 5: "SATURN", 6: "SUN"}
_INDIA_OFFSET = timedelta(hours=5, minutes=30)

# Global Skyfield objects (lazy-loaded with fallback)
_TS = None
_EPH = None
_EARTH = None
_MOON = None
_SUN = None
_skyfield_failed = False

def init_skyfield():
    global _TS, _EPH, _EARTH, _MOON, _SUN, _skyfield_failed
    if _TS is not None or _skyfield_failed:
        return
    try:
        # skyfield downloads to data_store if specified
        _TS = load.timescale()
        # Try loading local or default de421
        try:
            _EPH = load("de421.bsp")
        except Exception:
            try:
                _EPH = load("de440s.bsp")
            except Exception:
                # Skyfield download fallback
                _EPH = load("de421.bsp")
        _EARTH = _EPH["earth"]
        _MOON = _EPH["moon"]
        _SUN = _EPH["sun"]
        print("[VEDIC] Skyfield loaded successfully with ephemeris.")
    except Exception as e:
        print(f"[VEDIC WARNING] Skyfield init failed: {e}. Falling back to analytical Vedic model.")
        _skyfield_failed = True

def get_days_since_j2000(dt: datetime) -> float:
    # J2000.0 epoch is 2000-01-01 12:00:00 UTC
    j2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return (dt - j2000).total_seconds() / 86400.0

def lahiri_ayanamsa(dt: datetime) -> float:
    # Ayanamsa correction (Lahiri)
    years = get_days_since_j2000(dt) / 365.25
    return 23.853 + (50.290966 / 3600.0) * years

def _get_analytical_longitudes(dt: datetime) -> Tuple[float, float]:
    """Analytical mean longitudes for Moon and Sun (standard astronomical series)."""
    d = get_days_since_j2000(dt)
    
    # Sun mean longitude
    sun_lon = (280.460 + 0.9856474 * d) % 360.0
    
    # Moon mean longitude
    moon_lon = (218.316 + 13.176396 * d) % 360.0
    
    return moon_lon, sun_lon

def _ecliptic_longitudes(dt: datetime) -> Tuple[float, float]:
    """Gets ecliptic longitudes (Moon, Sun) in degrees, falling back to analytical model if skyfield fails."""
    init_skyfield()
    if _skyfield_failed:
        return _get_analytical_longitudes(dt)
    
    try:
        t = _TS.from_datetime(dt)
        _, m_lon, _ = _EARTH.at(t).observe(_MOON).apparent().frame_latlon(ecliptic_frame)
        _, s_lon, _ = _EARTH.at(t).observe(_SUN).apparent().frame_latlon(ecliptic_frame)
        return float(m_lon.degrees) % 360.0, float(s_lon.degrees) % 360.0
    except Exception as e:
        print(f"[VEDIC WARNING] skyfield calculation failed ({e}), using analytical mean longitude.")
        return _get_analytical_longitudes(dt)

def current_nakshatra(dt: datetime) -> str:
    """Calculates the sidereal nakshatra name for a timestamp."""
    moon_lon, _ = _ecliptic_longitudes(dt)
    sidereal_moon_lon = (moon_lon - lahiri_ayanamsa(dt)) % 360.0
    index = int(sidereal_moon_lon // NAK_ARC) % 27
    return NAKSHATRA_NAMES[index]

def current_tithi(dt: datetime) -> int:
    """Calculates current tithi (1 to 30) for a timestamp."""
    moon_lon, sun_lon = _ecliptic_longitudes(dt)
    elong = (moon_lon - sun_lon) % 360.0
    tithi = int(elong // TITHI_ARC) + 1
    return max(1, min(30, tithi))

def lunar_volatility_window(dt: datetime) -> bool:
    """True if within ±48h of new or full moon (separation from syzygy < 26 degrees)."""
    moon_lon, sun_lon = _ecliptic_longitudes(dt)
    elong = (moon_lon - sun_lon) % 360.0
    # distance to new moon (0) or full moon (180)
    dist = min(elong, 360 - elong, abs(180 - elong))
    return dist < 26.0

def current_hora(dt: datetime) -> str:
    """Calculates current Hora planet (planetary hour) using 6:00 AM IST sunrise approximation."""
    # Convert UTC to IST
    ist = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
    sunrise = ist.replace(hour=6, minute=0, second=0, microsecond=0)
    if ist < sunrise:
        sunrise -= timedelta(days=1)
        
    day_ruler = DAY_RULERS[sunrise.weekday()]
    start_index = HORA_SEQUENCE.index(day_ruler)
    elapsed_hours = int((ist - sunrise).total_seconds() // 3600) % 24
    return HORA_SEQUENCE[(start_index + elapsed_hours) % len(HORA_SEQUENCE)]

def is_favorable(dt: datetime) -> Tuple[bool, List[str]]:
    """
    Vedic Filter Gate.
    Returns (True, []) if favorable, or (False, reasons) if unfavorable.
    Gated by:
      - Nakshatras: Ashwini, Bharani, Mrigashira, Punarvasu, Dhanishta
      - Hora: Saturn
      - Lunar volatility window: within 48h of new/full moon
    """
    reasons = []
    
    # 1. Nakshatra check
    nak = current_nakshatra(dt)
    unfavorable_naks = {"Ashwini", "Bharani", "Mrigashira", "Punarvasu", "Dhanishta"}
    if nak in unfavorable_naks:
        reasons.append(f"Unfavorable Nakshatra: {nak}")
        
    # 2. Hora check
    hora = current_hora(dt)
    if hora == "SATURN":
        reasons.append("Saturn Hora active")
        
    # 3. Lunar window check
    if lunar_volatility_window(dt):
        reasons.append("Lunar volatility window (within 48h of Syzygy)")
        
    is_fav = len(reasons) == 0
    return is_fav, reasons
