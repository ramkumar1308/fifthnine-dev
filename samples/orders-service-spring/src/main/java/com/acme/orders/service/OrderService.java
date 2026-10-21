package com.acme.orders.service;

import com.acme.orders.api.OrderController.OrderConfirmation;
import com.acme.orders.api.OrderController.OrderRequest;
import com.acme.orders.client.InventoryClient;
import com.acme.orders.client.PaymentClient;
import com.acme.orders.client.ShippingQuoteClient;
import java.util.UUID;
import org.springframework.stereotype.Service;

@Service
public class OrderService {

    private final InventoryClient inventoryClient;
    private final PaymentClient paymentClient;
    private final ShippingQuoteClient shippingQuoteClient;

    public OrderService(InventoryClient inventoryClient, PaymentClient paymentClient,
            ShippingQuoteClient shippingQuoteClient) {
        this.inventoryClient = inventoryClient;
        this.paymentClient = paymentClient;
        this.shippingQuoteClient = shippingQuoteClient;
    }

    public OrderConfirmation placeOrder(OrderRequest request) {
        String orderId = UUID.randomUUID().toString();
        inventoryClient.reserve(orderId, request.sku(), request.quantity());
        long shipping = shippingQuoteClient.quote(request.sku(), request.quantity());
        String paymentId = paymentClient.charge(orderId, request.customerId(), request.amountCents() + shipping);
        return new OrderConfirmation(orderId, paymentId, shipping);
    }
}
