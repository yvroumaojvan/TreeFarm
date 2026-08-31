public class Main {
    public static void main(String[] args) {
        OrderService service = new OrderService();
        service.checkout("order-1", 99.5);
    }
}
