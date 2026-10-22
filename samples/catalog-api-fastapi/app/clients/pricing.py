import requests

from app.settings import settings


def get_price(sku: str) -> dict:
    response = requests.get(f"{settings.pricing_url}/prices/{sku}")
    response.raise_for_status()
    return response.json()


def get_price_history(sku: str) -> list:
    response = httpx_get_history(sku)
    return response


def httpx_get_history(sku: str) -> list:
    import httpx

    response = httpx.get(f"{settings.pricing_url}/prices/{sku}/history")
    response.raise_for_status()
    return response.json()
