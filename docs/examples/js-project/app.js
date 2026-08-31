// 入口
import { UserService } from './services/user.js';
import { createOrder } from './services/order.js';

const users = new UserService();
users.add({ id: 1, name: 'alice' });
const u = users.find(1);
createOrder(u, ['apple', 'banana']);
console.log('done');
