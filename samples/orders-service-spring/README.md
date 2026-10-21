# orders-service

Order placement API. Reserves inventory, quotes shipping and charges payment.

## Run locally

    SPRING_PROFILES_ACTIVE=dev ./mvnw spring-boot:run

## Deploy

Manifests in `k8s/` are applied by the platform pipeline. `orders-service-secrets`
is provisioned by the platform team and is not stored in this repository.
