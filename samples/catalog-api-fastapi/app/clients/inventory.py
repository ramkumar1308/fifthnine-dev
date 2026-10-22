import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential, wait_fixed

from app.settings import settings

_client = httpx.AsyncClient(base_url=settings.inventory_url, timeout=httpx.Timeout(2.0))


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(3),
    wait=wait_fixed(1),
    reraise=True,
)
async def fetch_stock(sku: str) -> int:
    response = await _client.get(f"/stock/{sku}")
    response.raise_for_status()
    return response.json()["available"]


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    wait=wait_exponential(multiplier=0.5, max=10),
)
async def fetch_warehouses(sku: str) -> list[str]:
    response = await _client.get(f"/stock/{sku}/warehouses")
    response.raise_for_status()
    return response.json()["warehouses"]
