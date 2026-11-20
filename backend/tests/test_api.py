from app.simulation import parse_simulation_header, status_for

from .conftest import MOCK_REPLY, new_session, unique


def chat(client, session_id, message, headers=None, **extra):
    return client.post("/chat", json={"session_id": session_id, "message": message, **extra}, headers=headers or {})


# ---------- /health ----------


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "keys_configured": 3, "cache_enabled": True}


def test_health_accepts_head_for_uptime_monitors(client):
    res = client.head("/health")
    assert res.status_code == 200
    assert res.content == b""


# ---------- /chat happy path and history ----------


def test_chat_live_inference_and_history(client):
    sid = new_session()
    msg = unique("What is the capital of France?")
    res = chat(client, sid, msg)
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == sid
    assert body["reply"] == MOCK_REPLY
    meta = body["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["model"] == "gemini/gemini-3.5-flash-lite"
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0
    assert isinstance(meta["latency_ms"], int) and meta["latency_ms"] > 0
    assert isinstance(meta["input_tokens"], int) and isinstance(meta["output_tokens"], int)

    history = client.get(f"/sessions/{sid}/messages")
    assert history.status_code == 200
    messages = history.json()["messages"]
    assert history.json()["session_id"] == sid
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0] == {"role": "user", "content": msg}
    assert messages[1]["content"] == MOCK_REPLY
    assert messages[1]["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert messages[1]["metadata"]["input_tokens"] == meta["input_tokens"]
    assert messages[1]["metadata"]["output_tokens"] == meta["output_tokens"]


def test_message_is_trimmed_before_storing(client):
    sid = new_session()
    msg = unique("hello")
    assert chat(client, sid, f"   {msg}  \n").status_code == 200
    assert client.get(f"/sessions/{sid}/messages").json()["messages"][0]["content"] == msg


def test_multi_turn_history_is_kept_in_order_and_sessions_are_separate(client):
    sid_a, sid_b = new_session(), new_session()
    for i in range(3):
        assert chat(client, sid_a, unique(f"a{i}")).status_code == 200
    assert chat(client, sid_b, unique("b0")).status_code == 200
    a = client.get(f"/sessions/{sid_a}/messages").json()["messages"]
    b = client.get(f"/sessions/{sid_b}/messages").json()["messages"]
    assert len(a) == 6 and len(b) == 2
    assert [m["content"].split(" ")[0] for m in a if m["role"] == "user"] == ["a0", "a1", "a2"]


def test_history_is_sent_to_the_model(client, monkeypatch):
    sent = []
    gateway = client.app.state.gateway
    original = gateway.complete

    async def spy(messages, simulated_failures=None):
        sent.append(messages)
        return await original(messages, simulated_failures)

    monkeypatch.setattr(gateway, "complete", spy)
    sid = new_session()
    first, second = unique("My name is Ada."), unique("What is my name?")
    chat(client, sid, first, system_prompt="You are a pirate.")
    chat(client, sid, second)
    assert sent[1] == [
        {"role": "system", "content": "You are a pirate."},  # session prompt is remembered
        {"role": "user", "content": first},
        {"role": "assistant", "content": MOCK_REPLY},
        {"role": "user", "content": second},
    ]


def test_unknown_session_returns_404(client):
    res = client.get(f"/sessions/{new_session()}/messages")
    assert res.status_code == 404
    assert res.json() == {"detail": "session not found"}


# ---------- validation (422) ----------


def test_validation_errors(client):
    sid = new_session()
    cases = [
        {"session_id": sid},
        {"message": "hi"},
        {"session_id": "", "message": "hi"},
        {"session_id": sid, "message": ""},
        {"session_id": sid, "message": "   \n "},
        {"session_id": sid, "message": "x" * 4001},
        {"session_id": "s" * 129, "message": "hi"},
        {"session_id": sid, "message": 123},
    ]
    for payload in cases:
        res = client.post("/chat", json=payload)
        assert res.status_code == 422, payload
        assert "detail" in res.json()
    assert client.get(f"/sessions/{sid}/messages").status_code == 404


def test_malformed_json_returns_422(client):
    res = client.post("/chat", content=b"{not json", headers={"Content-Type": "application/json"})
    assert res.status_code == 422
    assert "detail" in res.json()


def test_message_of_exactly_4000_chars_is_accepted(client):
    assert chat(client, new_session(), unique("x" * 3950)[:4000]).status_code == 200


# ---------- caching ----------


def test_same_first_message_in_new_session_hits_cache(client):
    msg = unique("Tell me a fact")
    first = chat(client, new_session(), msg).json()
    second = chat(client, new_session(), msg).json()
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second["reply"] == first["reply"]
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["fallback_attempts"] == 0
    assert second["metadata"]["input_tokens"] == first["metadata"]["input_tokens"]
    assert second["metadata"]["output_tokens"] == first["metadata"]["output_tokens"]
    assert second["metadata"]["latency_ms"] < first["metadata"]["latency_ms"]


def test_different_message_is_never_served_from_cache(client):
    chat(client, new_session(), unique("one"))
    res = chat(client, new_session(), unique("two")).json()
    assert res["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_different_system_prompt_is_a_different_cache_key(client):
    msg = unique("hello")
    chat(client, new_session(), msg)
    res = chat(client, new_session(), msg, system_prompt="Answer in French.").json()
    assert res["metadata"]["resolution_source"] == "LIVE_INFERENCE"


# ---------- failover via X-Simulate-Failure ----------


def test_key_1_429_fails_over_to_key_2(client):
    res = chat(client, new_session(), unique("hi"), headers={"X-Simulate-Failure": "key_1=429"})
    assert res.status_code == 200
    meta = res.json()["metadata"]
    assert meta["key_id"] == "key_2"
    assert meta["fallback_attempts"] == 1
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert res.json()["reply"] == MOCK_REPLY


def test_key_1_500_fails_over_to_key_2(client):
    res = chat(client, new_session(), unique("hi"), headers={"X-Simulate-Failure": "key_1=500"})
    assert res.status_code == 200
    assert res.json()["metadata"]["key_id"] == "key_2"


def test_two_failures_fall_through_to_key_3(client):
    res = chat(client, new_session(), unique("hi"), headers={"X-Simulate-Failure": "key_1=429,key_2=502"})
    assert res.status_code == 200
    assert res.json()["metadata"]["key_id"] == "key_3"
    assert res.json()["metadata"]["fallback_attempts"] == 2


def test_all_keys_failing_returns_503_and_stores_nothing(client):
    sid = new_session()
    res = chat(client, sid, unique("hi"), headers={"X-Simulate-Failure": "*=429"})
    assert res.status_code == 503
    assert res.json() == {"detail": "all providers unavailable"}
    assert client.get(f"/sessions/{sid}/messages").status_code == 404


def test_503_does_not_add_to_existing_history(client):
    sid = new_session()
    chat(client, sid, unique("first"))
    chat(client, sid, unique("second"), headers={"X-Simulate-Failure": "*=503"})
    assert len(client.get(f"/sessions/{sid}/messages").json()["messages"]) == 2


def test_simulation_skips_cache_reads_so_all_failed_is_still_503(client):
    msg = unique("cached question")
    chat(client, new_session(), msg)
    res = chat(client, new_session(), msg, headers={"X-Simulate-Failure": "*=429"})
    assert res.status_code == 503


def test_simulation_is_per_request_only(client):
    chat(client, new_session(), unique("x"), headers={"X-Simulate-Failure": "key_1=429"})
    res = chat(client, new_session(), unique("y"))
    assert res.json()["metadata"]["key_id"] == "key_1"


def test_header_is_ignored_when_simulation_disabled(client_no_simulation):
    res = chat(client_no_simulation, new_session(), unique("hi"), headers={"X-Simulate-Failure": "*=429"})
    assert res.status_code == 200
    assert res.json()["metadata"]["key_id"] == "key_1"


def test_cors_allows_frontend_origin_and_simulation_header(client):
    res = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"


# ---------- header parsing ----------


def test_parse_simulation_header():
    assert parse_simulation_header("key_1=429") == {"key_1": 429}
    assert parse_simulation_header("key_1=429, key_2=500") == {"key_1": 429, "key_2": 500}
    assert parse_simulation_header("*=429") == {"*": 429}
    assert parse_simulation_header("key_1=418,garbage,key_2=") == {}
    assert parse_simulation_header(None) == {}
    assert status_for({"*": 429, "key_2": 500}, "key_2") == 500
    assert status_for({"*": 429}, "key_3") == 429
    assert status_for({"key_1": 429}, "key_2") is None
