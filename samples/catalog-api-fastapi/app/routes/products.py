import asyncio

from fastapi import APIRouter

from app.clients import inventory, pricing, recommendations
from app.settings import settings

router = APIRouter()


@router.get("/products/{sku}")
async def product_page(sku: str) -> dict:
    async with asyncio.timeout(settings.product_page_budget_seconds):
        stock = await inventory.fetch_stock(sku)
    price = pricing.get_price(sku)
    related = await recommendations.related(sku)
    return {"sku": sku, "stock": stock, "price": price, "related": related}


@router.get("/products/{sku}/availability")
async def availability(sku: str) -> dict:
    return {"sku": sku, "warehouses": await inventory.fetch_warehouses(sku)}
