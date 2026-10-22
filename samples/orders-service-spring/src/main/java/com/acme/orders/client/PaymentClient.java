package com.acme.orders.client;

import io.github.resilience4j.retry.annotation.Retry;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Component
public class PaymentClient {

    private final RestTemplate restTemplate = new RestTemplate();

    private final String baseUrl;

    public PaymentClient(@Value("${clients.payment.base-url}") String baseUrl) {
        this.baseUrl = baseUrl;
    }

    @Retry(name = "payment")
    public String charge(String orderId, String customerId, long amountCents) {
        Map<String, Object> body = Map.of("orderId", orderId, "customerId", customerId, "amountCents", amountCents);
        PaymentResponse response = restTemplate.postForObject(baseUrl + "/v1/charges", body, PaymentResponse.class);
        return response.paymentId();
    }

    record PaymentResponse(String paymentId, String status) {}
}
