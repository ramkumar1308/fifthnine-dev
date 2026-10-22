package com.acme.orders.api;

import com.acme.orders.service.OrderService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/orders")
public class OrderController {

    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public OrderConfirmation create(@RequestBody OrderRequest request) {
        return orderService.placeOrder(request);
    }

    public record OrderRequest(String customerId, String sku, int quantity, long amountCents) {}

    public record OrderConfirmation(String orderId, String paymentId, long shippingCents) {}
}
