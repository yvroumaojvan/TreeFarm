// 用户服务：类 + 方法 + 箭头函数
export class UserService {
  constructor() {
    this.users = new Map();
  }

  add(user) {
    this.users.set(user.id, user);
  }

  find(id) {
    return this.users.get(id);
  }

  list() {
    return [...this.users.values()];
  }
}

export const defaultUser = () => ({ id: 0, name: 'guest' });
