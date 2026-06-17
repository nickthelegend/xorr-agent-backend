import httpx
import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
from sqlmodel import Session
from config import settings
from persistence.db import engine as db_engine
from persistence.models import McpSkillCache

logger = logging.getLogger("xorr.data.cmc_mcp")

# Global dict for skill catalogs or catalog items cached during session
MCP_SKILL_CACHE: Dict[str, Any] = {}

class CmcMcpClient:
    def __init__(self, api_key: str = None, url: str = None):
        self.api_key = api_key or settings.cmc_mcp_api_key
        self.url = url or settings.cmc_mcp_url
        self.client = httpx.AsyncClient(timeout=90.0)

    async def connect(self) -> None:
        """Connects to the CMC MCP stream endpoint and initializes the session."""
        init_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "xorr-agent",
                    "version": "2.0.0"
                }
            }
        }
        headers = {
            "X-CMC-MCP-API-KEY": self.api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }
        try:
            resp = await self.client.post(self.url, json=init_body, headers=headers)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to connect to CMC MCP: {e}")
            raise ConnectionError(f"CMC MCP connection failed: {e}")

    async def list_skills(self) -> List[Dict[str, Any]]:
        """Queries all skills available in the MCP server."""
        body = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        headers = {
            "X-CMC-MCP-API-KEY": self.api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }
        try:
            resp = await self.client.post(self.url, json=body, headers=headers)
            resp.raise_for_status()
            
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    payload = json.loads(line[5:])
                    if "result" in payload and "tools" in payload["result"]:
                        skills = []
                        for t in payload["result"]["tools"]:
                            skills.append({
                                "unique_name": t["name"],
                                "description": t.get("description", ""),
                                "input_schema": t.get("inputSchema", {})
                            })
                        return skills
            return []
        except Exception as e:
            logger.error(f"list_skills failed: {e}")
            raise

    async def find_skill(self, query: str) -> List[Dict[str, Any]]:
        """Queries the find_skill tool on CMC MCP for skills matching search query."""
        body = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "find_skill",
                "arguments": {
                    "query": query
                }
            }
        }
        headers = {
            "X-CMC-MCP-API-KEY": self.api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }
        try:
            resp = await self.client.post(self.url, json=body, headers=headers)
            resp.raise_for_status()
            
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    payload = json.loads(line[5:])
                    if "result" in payload and "content" in payload["result"]:
                        content_list = payload["result"]["content"]
                        if content_list and len(content_list) > 0:
                            text_val = content_list[0].get("text", "")
                            data_dict = json.loads(text_val)
                            candidates = data_dict.get("candidates", [])
                            skills = []
                            for c in candidates:
                                skills.append({
                                    "unique_name": c["uniqueName"],
                                    "description": c.get("skillDescription", ""),
                                    "input_schema": c.get("inputSchema", {})
                                })
                            return skills
            return []
        except Exception as e:
            logger.error(f"find_skill failed: {e}")
            raise

    async def execute_skill(self, unique_name: str, parameters: dict) -> dict:
        """Executes a specific skill with the given parameters and returns JSON result."""
        body = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "execute_skill",
                "arguments": {
                    "unique_name": unique_name,
                    "parameters": parameters
                }
            }
        }
        headers = {
            "X-CMC-MCP-API-KEY": self.api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }
        try:
            resp = await self.client.post(self.url, json=body, headers=headers)
            resp.raise_for_status()
            
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    payload = json.loads(line[5:])
                    if "result" in payload:
                        if "content" in payload["result"]:
                            content_list = payload["result"]["content"]
                            if content_list and len(content_list) > 0:
                                text_val = content_list[0].get("text", "")
                                try:
                                    return json.loads(text_val)
                                except json.JSONDecodeError:
                                    return {"raw_text": text_val}
                        if payload["result"].get("isError"):
                            raise RuntimeError(f"execute_skill returned error: {payload['result']}")
                        return payload["result"]
            raise RuntimeError("No event message found in stream")
        except Exception as e:
            logger.error(f"execute_skill failed: {e}")
            raise

    async def close(self) -> None:
        """Closes the underlying HTTPX client."""
        await self.client.aclose()

    @classmethod
    def from_env(cls):
        return cls()

async def get_cached_mcp_skill(
    unique_name: str,
    parameters: Optional[dict] = None,
    client: Optional[CmcMcpClient] = None,
    ttl_minutes: int = 30
) -> dict:
    """
    Returns the cached execution output for a specific skill.
    If cached value is absent or older than ttl_minutes, executes the skill and updates the cache.
    """
    # 1. Check database cache
    try:
        with Session(db_engine) as session:
            db_cache = session.get(McpSkillCache, unique_name)
            if db_cache:
                # Ensure tzinfo is timezone.utc for comparison
                cached_at = db_cache.cached_at
                if cached_at.tzinfo is None:
                    cached_at = cached_at.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - cached_at
                if age < timedelta(minutes=ttl_minutes):
                    return json.loads(db_cache.payload_json)
    except Exception as e:
        logger.warning(f"Failed to query database cache: {e}")

    # 2. Not in cache or expired, fetch from server
    close_client = False
    if client is None:
        client = CmcMcpClient()
        try:
            await client.connect()
        except Exception as e:
            logger.error(f"Failed to connect inside get_cached_mcp_skill: {e}")
            # If connection fails, fallback to expired database cache if it exists, otherwise raise
            try:
                with Session(db_engine) as session:
                    db_cache = session.get(McpSkillCache, unique_name)
                    if db_cache:
                        logger.warning(f"MCP connection failed; falling back to expired database cache for {unique_name}")
                        return json.loads(db_cache.payload_json)
            except Exception:
                pass
            raise
        close_client = True
        
    try:
        if parameters is None:
            parameters = {"preview": True}
        data = await client.execute_skill(unique_name, parameters)
        
        # Save to database cache
        try:
            with Session(db_engine) as session:
                db_cache = McpSkillCache(
                    unique_name=unique_name,
                    payload_json=json.dumps(data),
                    cached_at=datetime.now(timezone.utc)
                )
                session.merge(db_cache)
                session.commit()
        except Exception as e:
            logger.warning(f"Failed to save execution cache to database: {e}")
            
        return data
    finally:
        if close_client:
            await client.close()
