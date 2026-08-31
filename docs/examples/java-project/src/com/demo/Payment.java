package com.demo;

public interface Payment {
    boolean pay(String orderId, double amount);
}
