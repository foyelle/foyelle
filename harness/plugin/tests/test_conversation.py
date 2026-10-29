import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foyelle_policy.conversation import Conversations, PerConversation, mentioned  # noqa: E402
from foyelle_policy.policy import Policy  # noqa: E402

POLICY = Policy.from_dict({"connectors": {
    "files": {"sensitivity": "private", "tools": {"read": {"tier": "read"}}},
    "todoist": {"sensitivity": "private", "service": True, "title": "Todoist", "tools": {"find_tasks": {"tier": "read"}}},
    "kiwi": {"sensitivity": "public", "service": True, "title": "Kiwi.com flights", "tools": {"search_flight": {"tier": "read"}}},
}})
SERVERS = {"mcp_files_read": "files", "mcp_todoist_find_tasks": "todoist", "mcp_kiwi_search_flight": "kiwi",
           "mcp_shell_use_service": "shell", "mcp__todoist__list_resources": None}


def tool(name):
    return {"type": "function", "function": {"name": name, "description": "", "parameters": {}}}


def request(text, system="You are Foyelle."):
    return {"model": "m", "tools": [tool(n) for n in SERVERS],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}]}


def engine(tmp_path):
    return PerConversation(lambda: POLICY, SERVERS.get, Conversations(tmp_path))


def names(req):
    return [t["function"]["name"] for t in req["tools"]]


def test_services_not_used_are_left_out_and_named(tmp_path):
    out = engine(tmp_path).filter(request("what's in my documents?"), session_id="s1")["request"]
    assert names(out) == ["mcp_files_read", "mcp_shell_use_service"]
    note = out["messages"][0]["content"]
    assert note.startswith("You are Foyelle.") and "Kiwi.com flights, Todoist" in note and "shell.use_service" in note


def test_a_named_service_is_offered(tmp_path):
    out = engine(tmp_path).filter(request("what's on my todoist today?"), session_id="s1")["request"]
    assert "mcp_todoist_find_tasks" in names(out) and "mcp_kiwi_search_flight" not in names(out)
    assert mentioned("find me a Kiwi flight", {"kiwi": "Kiwi.com flights"}) == {"kiwi"}
    assert mentioned("a kiwifruit smoothie", {"kiwi": "Kiwi.com flights"}) == set()


def test_on_and_off_per_conversation(tmp_path):
    e = engine(tmp_path)
    e.conversations.set("s1", "kiwi", True)
    e.conversations.set("s1", "todoist", False)
    out = e.filter(request("todoist please"), session_id="s1")["request"]
    assert "mcp_kiwi_search_flight" in names(out) and "mcp_todoist_find_tasks" not in names(out)
    # Another conversation is untouched.
    assert "mcp_kiwi_search_flight" not in names(e.filter(request("hello"), session_id="s2")["request"])


def test_use_service_loads_it_and_calls_to_unloaded_services_are_refused(tmp_path):
    e = engine(tmp_path)
    e.filter(request("plan a trip"), session_id="s1")
    assert e.refuse("s1", "kiwi", "model") and "use_service" in e.refuse("s1", "kiwi", "model")
    assert e.refuse("s1", "kiwi", "surface") is None  # the person's own card
    assert e.refuse("s1", "files", "model") is None  # Foyelle's own helpers are always offered
    e.use("s1", "Kiwi.com flights")
    assert e.refuse("s1", "kiwi", "model") is None
    assert "mcp_kiwi_search_flight" in names(e.filter(request("plan a trip"), session_id="s1")["request"])
    # Unknown sessions (after a restart) are left to the policy.
    assert e.refuse("never-seen", "kiwi", "model") is None


def test_nothing_to_do_and_broken_input_change_nothing(tmp_path):
    e = engine(tmp_path)
    assert e.filter({"model": "m", "messages": []}, session_id="s1") is None
    assert e.filter({"tools": "nonsense"}, session_id="s1") is None
    assert e.filter(None, session_id="s1") is None  # type: ignore[arg-type]
    assert Conversations(tmp_path).get("../../etc/passwd") == {"on": set(), "off": set()}


def test_the_note_goes_where_the_provider_reads_it(tmp_path):
    req = {"tools": [tool("mcp_kiwi_search_flight")], "instructions": "Be kind.",
           "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]}
    out = engine(tmp_path).filter(req, session_id="s1")["request"]
    assert out["instructions"].startswith("Be kind.") and "Kiwi.com flights" in out["instructions"] and out["tools"] == []


def test_the_per_server_tools_hermes_adds_follow_their_service(tmp_path):
    out = engine(tmp_path).filter(request("hello"), session_id="s1")["request"]
    assert "mcp__todoist__list_resources" not in names(out)
