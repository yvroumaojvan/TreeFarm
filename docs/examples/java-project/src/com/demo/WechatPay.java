package com.demo;

public class WechatPay implements Payment {
    @Override
    public boolean pay(String orderId, double amount) {
        return amount >= 0.01;
    }
}
