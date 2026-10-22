import httpx

from app.settings import settings

# Recommendation model calls can be slow under load; don't cut them off.
_client = httpx.AsyncClient(base_url=settings.recommendations_url, timeout=None)


async def related(sku: str) -> list[str]:
    response = await _client.get("/related", params={"sku": sku, "limit": 8})
    response.raise_for_status()
    return response.json()["skus"]
