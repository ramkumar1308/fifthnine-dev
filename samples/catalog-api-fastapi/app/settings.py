from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    pricing_url: str = "http://pricing:8080"
    inventory_url: str = "http://inventory:8080"
    recommendations_url: str = "http://recommendations:8080"
    product_page_budget_seconds: float = 4.0


settings = Settings()
