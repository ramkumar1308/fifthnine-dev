from fastapi import FastAPI

from app.routes import health, products

app = FastAPI(title="catalog-api")
app.include_router(health.router)
app.include_router(products.router, prefix="/v1")
