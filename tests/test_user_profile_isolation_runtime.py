"""Runtime-level profile isolation test with an in-memory Supabase double."""

import json

import pytest


pytest.importorskip("langchain")
user_profile = pytest.importorskip("tools.user_profile")


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, database, operation="select", payload=None):
        self.database = database
        self.operation = operation
        self.payload = payload
        self.filters = {}

    def select(self, _fields):
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = dict(payload)
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = dict(payload)
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def execute(self):
        user_id = self.filters.get("user_id")
        if self.operation == "select":
            row = self.database.rows.get(user_id)
            return _Response([dict(row)] if row else [])
        if self.operation == "insert":
            self.database.rows[self.payload["user_id"]] = dict(self.payload)
            return _Response([dict(self.payload)])
        if self.operation == "update":
            self.database.rows[user_id].update(self.payload)
            return _Response([dict(self.database.rows[user_id])])
        raise AssertionError(f"unsupported operation: {self.operation}")


class _FakeSupabase:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        assert name == "user_profile"
        return _Query(self)


def test_two_context_users_cannot_read_or_update_each_other(monkeypatch):
    database = _FakeSupabase()
    current = {"user_id": "TEST-user-a"}
    monkeypatch.setattr(user_profile, "_supabase_client", database)
    monkeypatch.setattr(
        user_profile, "_current_user_id", lambda: current["user_id"]
    )

    user_profile.update_user_profile.invoke(
        {"fields_json": json.dumps({"major": "计算机科学"}, ensure_ascii=False)}
    )
    current["user_id"] = "TEST-user-b"
    user_profile.update_user_profile.invoke(
        {"fields_json": json.dumps({"major": "应用数学"}, ensure_ascii=False)}
    )

    profile_b = json.loads(user_profile.get_user_profile.invoke({}))
    current["user_id"] = "TEST-user-a"
    profile_a = json.loads(user_profile.get_user_profile.invoke({}))

    assert profile_a["user_id"] == "TEST-user-a"
    assert profile_a["major"] == "计算机科学"
    assert profile_b["user_id"] == "TEST-user-b"
    assert profile_b["major"] == "应用数学"
    assert database.rows["TEST-user-a"]["major"] != database.rows["TEST-user-b"]["major"]
