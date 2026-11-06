"""MCP protocol and tool tests.

The protocol-level tests matter because a client that gets an unexpected error
on a routine probe reports the whole server as broken, which is a confusing
failure to debug from the client side.
"""

from __future__ import annotations

import json

import pytest

from flowops.mcp_server import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
)


def rpc(server, method: str, params: dict | None = None, _id: int | None = 1):
    msg = {"jsonrpc": "2.0", "method": method}
    if _id is not None:
        msg["id"] = _id
    if params is not None:
        msg["params"] = params
    raw = server.dispatch_raw(json.dumps(msg))
    return json.loads(raw) if raw else None


def call_tool(server, name: str, arguments: dict):
    resp = rpc(server, "tools/call", {"name": name, "arguments": arguments})
    assert "result" in resp, resp
    return json.loads(resp["result"]["content"][0]["text"])


# --- protocol ------------------------------------------------------------


def test_initialize_returns_protocol_and_capabilities(mcp):
    result = rpc(mcp, "initialize", {"protocolVersion": PROTOCOL_VERSION})["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "flowops"


def test_notifications_get_no_response(mcp):
    """A JSON-RPC notification has no id and must produce no reply. Replying to
    one desynchronises a strict client."""
    assert rpc(mcp, "notifications/initialized", _id=None) is None


def test_tools_list_exposes_schemas(mcp):
    tools = rpc(mcp, "tools/list")["result"]["tools"]
    assert len(tools) == 5
    for tool in tools:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"
        assert set(tool["inputSchema"].get("required", [])) <= set(
            tool["inputSchema"]["properties"]
        )


def test_resources_and_prompts_probes_do_not_error(mcp):
    """Clients probe these on connect. Returning an error makes a working
    server look broken."""
    assert rpc(mcp, "resources/list")["result"] == {"resources": []}
    assert rpc(mcp, "prompts/list")["result"] == {"prompts": []}


def test_ping_works(mcp):
    assert rpc(mcp, "ping")["result"] == {}


def test_unknown_method_returns_method_not_found(mcp):
    assert rpc(mcp, "frobnicate")["error"]["code"] == METHOD_NOT_FOUND


def test_malformed_json_returns_parse_error(mcp):
    assert json.loads(mcp.dispatch_raw("{nope"))["error"]["code"] == PARSE_ERROR


def test_missing_jsonrpc_version_is_rejected(mcp):
    raw = mcp.dispatch_raw(json.dumps({"id": 1, "method": "ping"}))
    assert json.loads(raw)["error"]["code"] != 0


def test_batch_requests_are_supported(mcp):
    batch = json.dumps(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
    )
    responses = json.loads(mcp.dispatch_raw(batch))
    assert [r["id"] for r in responses] == [1, 2]


def test_unknown_tool_lists_the_real_ones(mcp):
    error = rpc(mcp, "tools/call", {"name": "nope", "arguments": {}})["error"]
    assert error["code"] == INVALID_PARAMS
    assert "refund_policy" in error["data"]["available"]


def test_missing_required_argument_is_reported(mcp):
    error = rpc(mcp, "tools/call", {"name": "account_lookup", "arguments": {}})["error"]
    assert "workspace_id" in error["message"]


def test_unknown_arguments_are_ignored(mcp):
    out = call_tool(mcp, "credit_authority", {"amount_usd": 50, "nonsense": True})
    assert out["agent_can_issue"] is True


# --- tools ---------------------------------------------------------------


def test_policy_search_finds_the_overage_rule(mcp):
    out = call_tool(mcp, "policy_search", {"query": "overage charges refundable"})
    assert out["found"] >= 1
    assert out["sections"][0]["doc"] == "billing-policy"
    assert "verage" in out["sections"][0]["section"]


def test_policy_search_respects_top_k(mcp):
    assert call_tool(mcp, "policy_search", {"query": "refund", "top_k": 2})["found"] <= 2


def test_account_lookup_known_and_unknown(mcp):
    found = call_tool(mcp, "account_lookup", {"workspace_id": "ws_demo"})
    assert found["found"] and found["tier"] == "growth"

    missing = call_tool(mcp, "account_lookup", {"workspace_id": "ws_nope"})
    assert missing["found"] is False
    assert "ws_demo" in missing["known_workspaces"]


@pytest.mark.parametrize(
    "args,eligible,reason_fragment",
    [
        ({"plan": "monthly", "days_into_term": 3}, False, "not refundable"),
        ({"plan": "annual", "days_into_term": 40, "monthly_price": 100}, False, "past the 30-day"),
        ({"plan": "annual", "days_into_term": 5, "is_overage": True}, False, "overage"),
        ({"plan": "annual", "days_into_term": 5, "via_reseller": True}, False, "reseller"),
        ({"plan": "annual", "days_into_term": 12, "monthly_price": 124}, True, "pro-rata"),
        ({"plan": "lifetime"}, False, "unrecognised"),
    ],
)
def test_refund_policy_covers_every_exception(mcp, args, eligible, reason_fragment):
    """All three exceptions that contradict the headline rule, plus the happy
    path and an unknown plan. These are exactly the cases a model asked to
    'apply the refund policy' gets wrong, which is why this is a tool."""
    out = call_tool(mcp, "refund_policy", args)
    assert out["eligible"] is eligible
    assert reason_fragment in out["reason"]


def test_refund_prorata_is_on_whole_months(mcp):
    out = call_tool(mcp, "refund_policy", {"plan": "annual", "days_into_term": 12, "monthly_price": 124})
    assert out["amount_usd"] == pytest.approx(11 * 124)


def test_overage_beats_the_30_day_window(mcp):
    out = call_tool(
        mcp, "refund_policy", {"plan": "annual", "days_into_term": 1, "is_overage": True}
    )
    assert out["eligible"] is False


@pytest.mark.parametrize(
    "amount,level",
    [(0, "agent"), (200, "agent"), (200.01, "support_lead"), (1000, "support_lead"), (5000, "finance")],
)
def test_credit_authority_thresholds(mcp, amount, level):
    assert call_tool(mcp, "credit_authority", {"amount_usd": amount})["approval_required"] == level


@pytest.mark.parametrize(
    "message,trigger",
    [
        ("we saw another workspace's data", "cross-tenant data exposure"),
        ("there is an unauthorised charge", "disputed charge"),
        ("we did not authorize this payment", "disputed charge"),
        ("we think there was a data breach", "security incident"),
        ("nobody can log in", "domain-wide authentication failure"),
        ("production is down", "production outage"),
        ("our lawyer will be in touch", "legal exposure"),
    ],
)
def test_escalation_check_detects_triggers(mcp, message, trigger):
    out = call_tool(mcp, "escalation_check", {"message": message})
    assert out["escalate"] is True
    assert trigger in out["triggers"]


def test_escalation_check_passes_routine_tickets(mcp):
    out = call_tool(mcp, "escalation_check", {"message": "how long are audit logs kept?"})
    assert out["escalate"] is False
    assert out["triggers"] == []


def test_escalation_check_flags_large_amounts(mcp):
    out = call_tool(
        mcp, "escalation_check", {"message": "please credit us", "requested_amount_usd": 5000}
    )
    assert out["escalate"] is True


def test_tool_exception_is_returned_as_is_error_not_a_protocol_failure(mcp):
    """A tool that raises must not break the connection -- the model should see
    the error text and adapt."""

    def boom() -> None:
        raise RuntimeError("kaboom")

    mcp.tools["explode"] = type(mcp.tools["credit_authority"])(
        name="explode", description="d", schema={"type": "object", "properties": {}}, handler=boom
    )
    result = rpc(mcp, "tools/call", {"name": "explode", "arguments": {}})["result"]
    assert result["isError"] is True
    assert "kaboom" in result["content"][0]["text"]


# --- HTTP transport ------------------------------------------------------


def test_http_app_health_and_auth(mcp):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from flowops.mcp_server import build_http_app

    client = TestClient(build_http_app(mcp, token="secret"))

    health = client.get("/healthz").json()
    assert health["status"] == "ok"
    assert health["auth_required"] is True
    assert len(health["tools"]) == 5

    # unauthenticated POST is rejected
    assert client.post("/messages", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401

    ok = client.post(
        "/messages",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": "Bearer secret"},
    )
    assert ok.status_code == 200 and ok.json()["result"] == {}


def test_http_app_without_token_is_open(mcp):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from flowops.mcp_server import build_http_app

    client = TestClient(build_http_app(mcp, token=""))
    resp = client.post("/messages", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert resp.status_code == 200
    assert len(resp.json()["result"]["tools"]) == 5
