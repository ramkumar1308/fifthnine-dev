package com.acme.orders.client;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

@Component
public class ShippingQuoteClient {

    private final RestClient restClient;

    public ShippingQuoteClient(@Value("${clients.shipping.base-url}") String baseUrl) {
        this.restClient = RestClient.builder().baseUrl(baseUrl).build();
    }

    public long quote(String sku, int quantity) {
        Quote quote = restClient.get()
            .uri("/v2/quotes?sku={sku}&qty={qty}", sku, quantity)
            .retrieve()
            .body(Quote.class);
        return quote.cents();
    }

    record Quote(long cents, String carrier) {}
}
