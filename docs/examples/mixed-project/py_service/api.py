# -*- coding: utf-8 -*-
"""Python API 服务：被 Go worker 和 JS 前端跨语言调用（模拟）。"""
from models import User


def create_user(name, email):
    """创建用户（对外 API）"""
    user = User(name, email)
    user.save()
    return user


def get_user(user_id):
    """查询用户（对外 API）"""
    return User.find(user_id)
