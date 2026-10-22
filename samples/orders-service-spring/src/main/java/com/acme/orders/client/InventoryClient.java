package com.acme.orders.client;

import io.github.resilience4j.retry.annotation.Retry;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

@Component
public class InventoryClient {

    private final RestClient restClient;

    public InventoryClient(RestClient.Builder builder, @Value("${clients.inventory.base-url}") String baseUrl) {
        this.restClient = builder.baseUrl(baseUrl).build();
    }

    @Retry(name = "inventory")
    public void reserve(String orderId, String sku, int quantity) {
        restClient.put()
            .uri("/v1/reservations/{orderId}", orderId)
            .body(Map.of("sku", sku, "quantity", quantity))
            .retrieve()
            .toBodilessEntity();
    }
}
