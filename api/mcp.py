import logging
from fastapi import APIRouter, HTTPException
from data.cmc_mcp import CmcMcpClient, MCP_SKILL_CACHE

logger = logging.getLogger("xorr.api.mcp")
router = APIRouter()

@router.get("/mcp/skills")
async def list_mcp_skills():
    """Returns the cached MCP skill catalog."""
    skills = []
    for k, v in MCP_SKILL_CACHE.items():
        skills.append({
            "unique_name": k,
            "description": v.get("description", ""),
            "input_schema": v.get("input_schema", {})
        })
    return skills

@router.post("/mcp/refresh")
async def refresh_mcp_skills():
    """Forces rediscovery and caching of CMC MCP macro/whale/regime skills."""
    client = CmcMcpClient()
    try:
        await client.connect()
        # Discover important skills
        skills_btc = await client.find_skill("btc price")
        skills_whale = await client.find_skill("whale netflow")
        skills_regime = await client.find_skill("macro regime")
        
        all_discovered = skills_btc + skills_whale + skills_regime
        for s in all_discovered:
            name = s["unique_name"]
            MCP_SKILL_CACHE[name] = {
                "description": s["description"],
                "input_schema": s["input_schema"]
            }
            
        await client.close()
        return {"status": "success", "count": len(all_discovered)}
    except Exception as e:
        logger.error(f"Failed to refresh MCP skills: {e}")
        raise HTTPException(status_code=500, detail=f"Refresh failed: {str(e)}")
