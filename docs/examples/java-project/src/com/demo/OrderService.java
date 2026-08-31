package com.demo;

public class OrderService {
    private final Payment payment;

    public OrderService() {
        this.payment = new Alipay();
    }

    public boolean checkout(String orderId, double amount) {
        return payment.pay(orderId, amount);
    }
}
