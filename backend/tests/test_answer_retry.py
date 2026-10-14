import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from types import SimpleNamespace  # noqa: E402

from app.llm import chat as chat_mod  # noqa: E402

BAD = "| a | b |\n|---|---|---|\n| 1 | 2 |"
GOOD = "| a | b |\n|---|---|\n| 1 | 2 |"


def _stream(text: str):
    return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))])]


class FakeClient:
    def __init__(self, answers: list[str]):
        self.answers = list(answers)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        text = self.answers.pop(0) if self.answers else self.last
        self.last = text
        return _stream(text)


def _run(monkeypatch, answers: list[str]):
    fake = FakeClient(answers)
    monkeypatch.setattr(chat_mod, "_client", lambda: fake)
    events = list(chat_mod.ask_stream("测试问题", None, user_id=1, erp_sid="s"))
    return fake, events


def test_bad_table_triggers_regeneration_and_good_answer_is_returned(monkeypatch):
    fake, events = _run(monkeypatch, [BAD, GOOD])
    assert fake.calls == 2
    assert [e for e in events if e["type"] == "retry"] == [{"type": "retry", "attempt": 1}]
    done = [e for e in events if e["type"] == "done"][0]
    assert done["answer"] == GOOD


def test_retry_limit_is_enforced_and_last_answer_is_repaired(monkeypatch):
    fake, events = _run(monkeypatch, [BAD, BAD, BAD, BAD])
    assert fake.calls == chat_mod.MAX_FORMAT_RETRIES + 1
    assert len([e for e in events if e["type"] == "retry"]) == chat_mod.MAX_FORMAT_RETRIES
    done = [e for e in events if e["type"] == "done"][0]
    assert done["answer"] == GOOD  # 超过次数后仍由校验修复，保证入库的是可渲染的表格


def test_good_answer_is_not_retried(monkeypatch):
    fake, events = _run(monkeypatch, [GOOD])
    assert fake.calls == 1
    assert not [e for e in events if e["type"] == "retry"]
