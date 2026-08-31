package com.demo;

public class Alipay implements Payment {
    @Override
    public boolean pay(String orderId, double amount) {
        return amount > 0;
    }
}
