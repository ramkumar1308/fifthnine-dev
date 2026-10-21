package com.acme.orders;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;

@SpringBootTest(properties = {
    "clients.payment.base-url=http://localhost:1",
    "clients.inventory.base-url=http://localhost:1",
    "clients.shipping.base-url=http://localhost:1"
})
class OrdersApplicationTests {

    @Test
    void contextLoads() {
    }
}
