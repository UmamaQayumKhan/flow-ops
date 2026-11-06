"""Tests for the generated workflows and the linter that checks them.

The linter is tested against deliberately broken workflows as well as the real
ones. A linter that only ever sees passing input is not evidence of anything.
"""

from __future__ import annotations

import json

import pytest

from flowops.builder import Workflow, slug, stable_id
from flowops.validate import validate_directory, validate_workflow
from flowops.workflows import ALL_WORKFLOWS, build_all


# --- builder -------------------------------------------------------------


def test_node_ids_are_deterministic():
    """Re-exporting an unchanged workflow must not produce a diff. n8n assigns
    random UUIDs, which makes every export unreviewable."""
    assert stable_id("A", "B") == stable_id("A", "B")
    assert stable_id("A", "B") != stable_id("A", "C")


def test_generated_json_is_byte_stable():
    first = json.dumps(ALL_WORKFLOWS["ticket-triage-agent"]().to_json(), indent=2)
    second = json.dumps(ALL_WORKFLOWS["ticket-triage-agent"]().to_json(), indent=2)
    assert first == second


def test_duplicate_node_names_are_rejected_at_build_time():
    wf = Workflow(name="X")
    wf.add("Node", "n8n-nodes-base.noOp")
    with pytest.raises(ValueError, match="duplicate node name"):
        wf.add("Node", "n8n-nodes-base.noOp")


def test_slug_is_filesystem_safe():
    assert slug("Ticket Triage Agent") == "ticket-triage-agent"


def test_ai_subnode_connections_point_at_the_agent():
    """The arrow runs sub-node → agent, not agent → sub-node. Reversing it is
    the most common reason an imported agent shows a disconnected model."""
    wf = ALL_WORKFLOWS["ticket-triage-agent"]()
    data = wf.to_json()
    model_links = [
        (src, link["node"])
        for src, by_type in data["connections"].items()
        for ctype, buckets in by_type.items()
        if ctype == "ai_languageModel"
        for bucket in buckets
        for link in bucket
    ]
    assert model_links
    for source, target in model_links:
        assert "Model" in source
        assert target == "Triage Agent"


# --- every real workflow -------------------------------------------------


@pytest.mark.parametrize("name", sorted(ALL_WORKFLOWS))
def test_workflow_passes_the_linter(name):
    report = validate_workflow(ALL_WORKFLOWS[name]().to_json())
    assert report.ok, "\n".join(str(f) for f in report.errors)


@pytest.mark.parametrize("name", sorted(ALL_WORKFLOWS))
def test_workflow_has_no_warnings_either(name):
    report = validate_workflow(ALL_WORKFLOWS[name]().to_json())
    assert not report.warnings, "\n".join(str(f) for f in report.warnings)


@pytest.mark.parametrize("name", sorted(ALL_WORKFLOWS))
def test_workflow_has_an_error_handler(name):
    """Silent failure is n8n's default and the worst one: nobody finds out
    until someone asks why the digest stopped arriving."""
    data = ALL_WORKFLOWS[name]().to_json()
    types = [n["type"] for n in data["nodes"]]
    assert "n8n-nodes-base.errorTrigger" in types


@pytest.mark.parametrize("name", sorted(ALL_WORKFLOWS))
def test_workflow_is_committed_inactive(name):
    assert ALL_WORKFLOWS[name]().to_json()["active"] is False


@pytest.mark.parametrize("name", sorted(ALL_WORKFLOWS))
def test_every_ai_node_output_is_validated_in_code(name):
    """An LLM's structured output is never acted on directly: a Code node
    checks it first. Asserted across all workflows so a new one cannot skip it."""
    data = ALL_WORKFLOWS[name]().to_json()
    ai_nodes = [
        n for n in data["nodes"]
        if n["type"].startswith("@n8n/n8n-nodes-langchain.")
        and any(k in n["type"] for k in ("agent", "chainLlm", "informationExtractor"))
    ]
    if not ai_nodes:
        pytest.skip("no AI output-producing node")
    code_nodes = {n["name"] for n in data["nodes"] if n["type"] == "n8n-nodes-base.code"}
    for ai in ai_nodes:
        downstream = {
            link["node"]
            for ctype, buckets in data["connections"].get(ai["name"], {}).items()
            if ctype == "main"
            for bucket in buckets
            for link in bucket
        }
        # either validated immediately, or it is a pure-summary chain with no
        # downstream action to guard
        assert downstream & code_nodes or ai["type"].endswith(".chainLlm"), (
            f"{name}: {ai['name']} output is not validated by a Code node"
        )


def test_committed_files_match_the_python_definitions(workflows_dir):
    """The drift check: if someone edits on the canvas and exports over the
    generated file, the Python definition stops being the source of truth."""
    for wf in build_all():
        path = workflows_dir / f"{slug(wf.name)}.json"
        assert path.exists(), f"{path.name} not generated — run `make generate`"
        expected = json.dumps(wf.to_json(), indent=2) + "\n"
        assert path.read_text(encoding="utf-8") == expected, (
            f"{path.name} has drifted from its Python definition"
        )


def test_directory_lint_is_clean(workflows_dir):
    report = validate_directory(workflows_dir)
    assert report.ok, "\n".join(str(f) for f in report.errors)
    assert not report.warnings, "\n".join(str(f) for f in report.warnings)


def test_all_four_workflows_exist(workflows_dir):
    assert len(list(workflows_dir.glob("*.json"))) == 4


# --- the linter actually catches things ----------------------------------


def _minimal(nodes, connections=None, **kw):
    return {
        "name": "Broken",
        "nodes": nodes,
        "connections": connections or {},
        "active": False,
        "settings": {"saveDataErrorExecution": "all"},
        "meta": {"description": "test"},
        **kw,
    }


def _node(name, type_, parameters=None, **kw):
    return {
        "name": name,
        "id": f"id-{name}",
        "type": type_,
        "typeVersion": 1,
        "position": [0, 0],
        "parameters": parameters or {},
        **kw,
    }


def test_linter_catches_dangling_connection():
    data = _minimal(
        [_node("Trigger", "n8n-nodes-base.scheduleTrigger")],
        {"Trigger": {"main": [[{"node": "Ghost", "type": "main", "index": 0}]]}},
    )
    codes = {f.code for f in validate_workflow(data).errors}
    assert "dangling-target" in codes


def test_linter_catches_orphan_node():
    data = _minimal(
        [
            _node("Trigger", "n8n-nodes-base.scheduleTrigger"),
            _node("Lonely", "n8n-nodes-base.noOp"),
        ]
    )
    codes = {f.code for f in validate_workflow(data).errors}
    assert "orphan-node" in codes


def test_linter_catches_missing_trigger():
    data = _minimal([_node("Thing", "n8n-nodes-base.noOp")])
    codes = {f.code for f in validate_workflow(data).errors}
    assert "no-trigger" in codes


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghijklmnopqrstuvwx",
        "gsk_abcdefghijklmnopqrstuvwxyz12",
        "AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz012345",
        "xoxb-1234567890-abcdefghij",
        "AKIAIOSFODNN7EXAMPLE",
        'api_key = "supersecretvalue123"',
        "postgresql://user:hunter2@db:5432/app",
    ],
)
def test_linter_catches_inline_secrets(secret):
    """An exported workflow carrying a real key into git is the single most
    expensive mistake this linter prevents."""
    data = _minimal(
        [
            _node("Trigger", "n8n-nodes-base.scheduleTrigger"),
            _node("Call", "n8n-nodes-base.httpRequest", {"url": f"https://x/{secret}"}, retryOnFail=True),
        ],
        {"Trigger": {"main": [[{"node": "Call", "type": "main", "index": 0}]]}},
    )
    codes = {f.code for f in validate_workflow(data).errors}
    assert "inline-secret" in codes


def test_linter_allows_credential_references():
    """The correct pattern must not trip the secret scanner."""
    data = _minimal(
        [
            _node("Trigger", "n8n-nodes-base.scheduleTrigger"),
            _node(
                "Call",
                "n8n-nodes-base.httpRequest",
                {"url": "https://api.example/x", "token": "={{ $credentials.apiKey }}"},
                retryOnFail=True,
            ),
        ],
        {"Trigger": {"main": [[{"node": "Call", "type": "main", "index": 0}]]}},
    )
    codes = {f.code for f in validate_workflow(data).errors}
    assert "inline-secret" not in codes


def test_linter_catches_unauthenticated_webhook():
    data = _minimal(
        [
            _node("Hook", "n8n-nodes-base.webhook", {"path": "x", "httpMethod": "POST"}),
            _node("Reply", "n8n-nodes-base.noOp"),
        ],
        {"Hook": {"main": [[{"node": "Reply", "type": "main", "index": 0}]]}},
    )
    codes = {f.code for f in validate_workflow(data).errors}
    assert "unauthenticated-webhook" in codes


def test_respond_to_webhook_is_not_treated_as_a_trigger():
    """The linter's own first false positive: a substring match on 'webhook'
    flagged respondToWebhook, which has no authentication setting."""
    data = _minimal(
        [
            _node(
                "Hook",
                "n8n-nodes-base.webhook",
                {"path": "x", "httpMethod": "POST", "authentication": "headerAuth"},
            ),
            _node("Respond", "n8n-nodes-base.respondToWebhook", {"respondWith": "json"}),
        ],
        {"Hook": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]}},
    )
    codes = {f.code for f in validate_workflow(data).errors}
    assert "unauthenticated-webhook" not in codes


def test_linter_catches_ai_node_without_a_model():
    data = _minimal(
        [
            _node("Trigger", "n8n-nodes-base.scheduleTrigger"),
            _node("Agent", "@n8n/n8n-nodes-langchain.agent", {"options": {"maxIterations": 3}}),
        ],
        {"Trigger": {"main": [[{"node": "Agent", "type": "main", "index": 0}]]}},
    )
    codes = {f.code for f in validate_workflow(data).errors}
    assert "ai-node-without-model" in codes


def test_linter_warns_on_unbounded_agent_loop():
    data = _minimal(
        [
            _node("Trigger", "n8n-nodes-base.scheduleTrigger"),
            _node("Agent", "@n8n/n8n-nodes-langchain.agent", {"options": {}}),
            _node("Model", "@n8n/n8n-nodes-langchain.lmChatGroq"),
        ],
        {
            "Trigger": {"main": [[{"node": "Agent", "type": "main", "index": 0}]]},
            "Model": {"ai_languageModel": [[{"node": "Agent", "type": "ai_languageModel", "index": 0}]]},
        },
    )
    codes = {f.code for f in validate_workflow(data).warnings}
    assert "unbounded-agent-loop" in codes


def test_linter_warns_on_missing_retry():
    data = _minimal(
        [
            _node("Trigger", "n8n-nodes-base.scheduleTrigger"),
            _node("Call", "n8n-nodes-base.httpRequest", {"url": "https://x"}),
        ],
        {"Trigger": {"main": [[{"node": "Call", "type": "main", "index": 0}]]}},
    )
    codes = {f.code for f in validate_workflow(data).warnings}
    assert "no-retry-on-network-node" in codes


def test_linter_catches_duplicate_node_ids():
    nodes = [
        _node("Trigger", "n8n-nodes-base.scheduleTrigger"),
        _node("A", "n8n-nodes-base.noOp"),
    ]
    nodes[1]["id"] = nodes[0]["id"]
    data = _minimal(
        nodes, {"Trigger": {"main": [[{"node": "A", "type": "main", "index": 0}]]}}
    )
    codes = {f.code for f in validate_workflow(data).errors}
    assert "duplicate-node-id" in codes


def test_linter_catches_committed_active_workflow():
    data = _minimal([_node("Trigger", "n8n-nodes-base.scheduleTrigger")], active=True)
    codes = {f.code for f in validate_workflow(data).warnings}
    assert "committed-active" in codes


def test_linter_reports_invalid_json(tmp_path):
    from flowops.validate import validate_file

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    report = validate_file(bad)
    assert {f.code for f in report.errors} == {"invalid-json"}
