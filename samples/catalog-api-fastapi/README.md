# catalog-api

Aggregates stock, pricing and recommendations for product pages.

    pip install -e '.[dev]'
    uvicorn app.main:app --reload

Production runs gunicorn with uvicorn workers (see `k8s/deployment.yaml`).
`catalog-api-env` is a platform-managed Secret, not stored here.
