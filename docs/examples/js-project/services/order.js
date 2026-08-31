// 订单服务：普通函数 + 箭头函数
export function createOrder(user, items) {
  const total = items.reduce((sum, it) => sum + priceOf(it), 0);
  return { user: user.id, items, total };
}

const priceOf = (item) => item.length * 10;

export function cancelOrder(orderId) {
  return `cancelled-${orderId}`;
}
