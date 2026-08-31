# -*- coding: utf-8 -*-
"""数据模型：被 api.py 和 main.py 引用。"""
import json


class User:
    def __init__(self, name, email):
        self.name = name
        self.email = email

    def save(self):
        with open(f"user_{self.name}.json", "w") as f:
            json.dump({"name": self.name, "email": self.email}, f)

    @staticmethod
    def find(user_id):
        with open(f"user_{user_id}.json") as f:
            data = json.load(f)
        return User(data["name"], data["email"])
