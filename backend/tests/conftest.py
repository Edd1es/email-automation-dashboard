import base64
import email
import json
from email import policy
from types import SimpleNamespace

import pytest

from app import create_app
from app.db import connect

TOKEN = "t" * 40
HEADERS = {"X-Internal-Token": TOKEN}


class Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeGmail:
    """In-memory stand-in for the parts of the Gmail API this app uses."""

    def __init__(self):
        self.sent = []
        self.threads_data = {}
        self.fail_for = set()
        self.search_results = []

    def users(self):
        return self

    def getProfile(self, userId):
        return Call({"emailAddress": "me@sender.test"})

    def messages(self):
        return SimpleNamespace(send=self._send, list=self._list, get=self._get)

    def threads(self):
        return SimpleNamespace(get=self._thread)

    def _send(self, userId, body):
        parsed = email.message_from_bytes(
            base64.urlsafe_b64decode(body["raw"]), policy=policy.default
        )
        if parsed["To"] in self.fail_for:
            return Call(RuntimeError("rejected"))
        self.sent.append(parsed)
        number = len(self.sent)
        return Call({"id": f"g{number}", "threadId": f"th{number}"})

    def _list(self, userId, q, maxResults):
        return Call({"messages": [{"id": m["id"]} for m in self.search_results]})

    def _get(self, userId, id, format, metadataHeaders):
        return Call(next(m for m in self.search_results if m["id"] == id))

    def _thread(self, userId, id, format, metadataHeaders):
        return Call(self.threads_data.get(id, {"messages": []}))


class FakeSheets:
    def __init__(self):
        self.values_data = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range):
        return Call({"values": self.values_data})


class FakeGroq:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        content = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.fixture
def fakes():
    return SimpleNamespace(gmail=FakeGmail(), sheets=FakeSheets())


@pytest.fixture
def app(tmp_path, fakes):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "s" * 40,
        "INTERNAL_API_TOKEN": TOKEN,
        "DATABASE_PATH": str(tmp_path / "test.db"),
        "GROQ_API_KEY": "",
        "PUBLIC_BASE_URL": "https://mail.test",
        "GOOGLE_SERVICE_FACTORY": lambda name, _v: fakes.gmail if name == "gmail" else fakes.sheets,
    })


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    conn = connect(app.config["DATABASE_PATH"])
    yield conn
    conn.close()


def post(client, path, body=None):
    return client.post(path, json=body or {}, headers=HEADERS)


def get(client, path):
    return client.get(path, headers=HEADERS)


def make_campaign(client, **extra):
    body = {"name": "Test", "goal": "Say hello", "sheet_id": "sheet1", **extra}
    return post(client, "/api/campaigns", body).get_json()["id"]
