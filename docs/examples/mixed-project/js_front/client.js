// JS 前端：模拟调用 Python API
export async function createUser(name, email) {
  // 模拟 POST /api/users → py_service.create_user
  return fetch('/api/users', { method: 'POST', body: JSON.stringify({ name, email }) });
}

export function getProfile(userId) {
  // 模拟 GET /api/users/:id → py_service.get_user
  return `profile-${userId}`;
}
