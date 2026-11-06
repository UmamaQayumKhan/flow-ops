"""A small typed builder that emits n8n workflow JSON.

Most n8n portfolios are a folder of JSON blobs exported from the canvas. They
are unreviewable in a pull request, they drift, and nobody notices when an
export ships a hardcoded API key or a connection pointing at a renamed node.

Defining workflows as code fixes all three:

* a diff shows an intent change, not 400 lines of reshuffled coordinates
* `validate.py` lints every workflow in CI -- dangling connections, orphan
  nodes, inline secrets, missing triggers
* node IDs are derived deterministically from the workflow and node name, so
  regenerating produces byte-identical output instead of a new random UUID per
  export

The JSON this emits imports into n8n unchanged (Workflows → Import from File).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# n8n lays the canvas out on a grid; these keep generated workflows readable
# when a reviewer opens one in the editor.
COL_WIDTH = 220
ROW_HEIGHT = 170
ORIGIN = (0, 300)


def stable_id(workflow: str, node: str) -> str:
    """Deterministic node id.

    n8n assigns a random UUID per node on export, so re-exporting an unchanged
    workflow produces a diff of pure noise. Deriving the id from the names makes
    regeneration idempotent and the diffs meaningful.
    """
    digest = hashlib.blake2b(f"{workflow}::{node}".encode(), digest_size=16).hexdigest()
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


@dataclass
class Node:
    name: str
    type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    type_version: float = 1
    position: tuple[int, int] | None = None
    credentials: dict[str, Any] = field(default_factory=dict)
    webhook_id: str | None = None
    # notes render on the canvas; worth filling in because the next person to
    # open the workflow is the one who has to debug it at 2am
    notes: str = ""
    always_output_data: bool = False
    continue_on_fail: bool = False
    retry_on_fail: bool = False
    max_tries: int = 3
    disabled: bool = False

    def to_json(self, workflow_name: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "parameters": self.parameters,
            "id": stable_id(workflow_name, self.name),
            "name": self.name,
            "type": self.type,
            "typeVersion": self.type_version,
            "position": list(self.position or ORIGIN),
        }
        if self.credentials:
            out["credentials"] = self.credentials
        if self.webhook_id:
            out["webhookId"] = self.webhook_id
        if self.notes:
            out["notes"] = self.notes
            out["notesInFlow"] = True
        if self.always_output_data:
            out["alwaysOutputData"] = True
        if self.continue_on_fail:
            out["onError"] = "continueRegularOutput"
        if self.retry_on_fail:
            out["retryOnFail"] = True
            out["maxTries"] = self.max_tries
        if self.disabled:
            out["disabled"] = True
        return out


@dataclass
class Workflow:
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    # source node -> output index -> list of (target node, input index)
    connections: dict[str, dict[str, list[list[tuple[str, int]]]]] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    _cursor: list[int] = field(default_factory=lambda: [0, 0])

    # -- construction ----------------------------------------------------
    def add(
        self,
        name: str,
        type_: str,
        parameters: dict[str, Any] | None = None,
        *,
        type_version: float = 1,
        col: int | None = None,
        row: int = 0,
        **kw: Any,
    ) -> Node:
        if any(n.name == name for n in self.nodes):
            raise ValueError(f"{self.name}: duplicate node name {name!r}")
        column = self._cursor[0] if col is None else col
        node = Node(
            name=name,
            type=type_,
            parameters=parameters or {},
            type_version=type_version,
            position=(ORIGIN[0] + column * COL_WIDTH, ORIGIN[1] + row * ROW_HEIGHT),
            **kw,
        )
        self.nodes.append(node)
        if col is None:
            self._cursor[0] += 1
        return node

    def connect(
        self,
        source: str | Node,
        target: str | Node,
        *,
        source_output: int = 0,
        target_input: int = 0,
        connection_type: str = "main",
    ) -> "Workflow":
        src = source.name if isinstance(source, Node) else source
        dst = target.name if isinstance(target, Node) else target
        outputs = self.connections.setdefault(src, {}).setdefault(connection_type, [])
        while len(outputs) <= source_output:
            outputs.append([])
        outputs[source_output].append((dst, target_input))
        return self

    def chain(self, *names: str | Node) -> "Workflow":
        for a, b in zip(names, names[1:]):
            self.connect(a, b)
        return self

    def attach(
        self, child: str | Node, parent: str | Node, connection_type: str
    ) -> "Workflow":
        """Attach a sub-node (model, memory, tool) to an AI Agent node.

        n8n's LangChain nodes use typed connections -- `ai_languageModel`,
        `ai_memory`, `ai_tool` -- and the arrow runs from the sub-node to the
        agent, which is the opposite direction from a normal main connection.
        Getting this backwards is the single most common reason an imported
        agent workflow shows a disconnected model.
        """
        return self.connect(child, parent, connection_type=connection_type)

    # -- output ----------------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        connections: dict[str, Any] = {}
        for src, by_type in self.connections.items():
            connections[src] = {
                ctype: [
                    [
                        {"node": dst, "type": ctype, "index": idx}
                        for dst, idx in bucket
                    ]
                    for bucket in buckets
                ]
                for ctype, buckets in by_type.items()
            }

        return {
            "name": self.name,
            "nodes": [n.to_json(self.name) for n in self.nodes],
            "connections": connections,
            "active": False,
            "settings": {
                "executionOrder": "v1",
                # Keep failed executions so a webhook failure is debuggable
                # instead of vanishing.
                "saveDataErrorExecution": "all",
                "saveDataSuccessExecution": "all",
                "saveExecutionProgress": True,
                "callerPolicy": "workflowsFromSameOwner",
                **self.settings,
            },
            "staticData": None,
            "pinData": {},
            "meta": {"description": self.description},
            "tags": [{"name": t} for t in self.tags],
            "versionId": stable_id(self.name, "__version__"),
        }

    def write(self, directory: str | Path) -> Path:
        path = Path(directory) / f"{slug(self.name)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2) + "\n", encoding="utf-8")
        return path

    # -- introspection ---------------------------------------------------
    def node_names(self) -> set[str]:
        return {n.name for n in self.nodes}

    def targets(self) -> set[str]:
        out: set[str] = set()
        for by_type in self.connections.values():
            for buckets in by_type.values():
                for bucket in buckets:
                    out.update(dst for dst, _ in bucket)
        return out

    def sources(self) -> set[str]:
        return set(self.connections)


def slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    return "".join(keep).strip("-").replace("--", "-")


# --------------------------------------------------------------------------
# node type constants -- typo-proofing the strings n8n matches exactly
# --------------------------------------------------------------------------


class T:
    WEBHOOK = "n8n-nodes-base.webhook"
    RESPOND = "n8n-nodes-base.respondToWebhook"
    SCHEDULE = "n8n-nodes-base.scheduleTrigger"
    HTTP = "n8n-nodes-base.httpRequest"
    CODE = "n8n-nodes-base.code"
    SET = "n8n-nodes-base.set"
    IF = "n8n-nodes-base.if"
    SWITCH = "n8n-nodes-base.switch"
    MERGE = "n8n-nodes-base.merge"
    SLACK = "n8n-nodes-base.slack"
    GMAIL = "n8n-nodes-base.gmail"
    SHEETS = "n8n-nodes-base.googleSheets"
    POSTGRES = "n8n-nodes-base.postgres"
    WAIT = "n8n-nodes-base.wait"
    NOOP = "n8n-nodes-base.noOp"
    STOP_AND_ERROR = "n8n-nodes-base.stopAndError"
    ERROR_TRIGGER = "n8n-nodes-base.errorTrigger"
    EXTRACT_FILE = "n8n-nodes-base.extractFromFile"
    SPLIT_OUT = "n8n-nodes-base.splitOut"
    FILTER = "n8n-nodes-base.filter"

    # LangChain / AI nodes
    AGENT = "@n8n/n8n-nodes-langchain.agent"
    LLM_CHAIN = "@n8n/n8n-nodes-langchain.chainLlm"
    LM_GROQ = "@n8n/n8n-nodes-langchain.lmChatGroq"
    LM_GEMINI = "@n8n/n8n-nodes-langchain.lmChatGoogleGemini"
    LM_OPENAI = "@n8n/n8n-nodes-langchain.lmChatOpenAi"
    MEMORY_BUFFER = "@n8n/n8n-nodes-langchain.memoryBufferWindow"
    MCP_CLIENT = "@n8n/n8n-nodes-langchain.mcpClientTool"
    TOOL_HTTP = "@n8n/n8n-nodes-langchain.toolHttpRequest"
    TOOL_WORKFLOW = "@n8n/n8n-nodes-langchain.toolWorkflow"
    OUTPUT_PARSER = "@n8n/n8n-nodes-langchain.outputParserStructured"
    TEXT_CLASSIFIER = "@n8n/n8n-nodes-langchain.textClassifier"
    INFO_EXTRACTOR = "@n8n/n8n-nodes-langchain.informationExtractor"


class C:
    """Typed connection names for AI sub-nodes."""

    MODEL = "ai_languageModel"
    MEMORY = "ai_memory"
    TOOL = "ai_tool"
    PARSER = "ai_outputParser"


def groq_model(
    wf: Workflow, name: str = "Groq Chat Model", *, col: int, row: int = 1
) -> Node:
    """Groq is the default because its free tier is fast enough to sit in a
    webhook path -- a ticket-triage workflow that takes 20 seconds to answer is
    not a triage workflow."""
    return wf.add(
        name,
        T.LM_GROQ,
        {"model": "llama-3.3-70b-versatile", "options": {"temperature": 0.1}},
        col=col,
        row=row,
        credentials={"groqApi": {"id": "={{ $credentials.groqApi }}", "name": "Groq account"}},
        notes="Free tier. Swap for lmChatGoogleGemini or lmChatOpenAi without touching the agent.",
    )


def mcp_tool(
    wf: Workflow,
    name: str,
    *,
    col: int,
    row: int = 2,
    endpoint: str = "http://flowops-mcp:8080/sse",
) -> Node:
    """Point the agent at the MCP server in `mcp_server/`.

    MCP matters here because the same tool definitions serve the n8n agent, a
    desktop MCP client session and the eval harness. Without it, every tool gets
    reimplemented as an n8n HTTP node and the three drift apart.
    """
    return wf.add(
        name,
        T.MCP_CLIENT,
        {"sseEndpoint": endpoint, "authentication": "headerAuth"},
        col=col,
        row=row,
        credentials={
            "httpHeaderAuth": {
                "id": "={{ $credentials.mcpHeaderAuth }}",
                "name": "FlowOps MCP token",
            }
        },
        notes="Tools served by the FlowOps MCP server: policy lookup, account, refund, escalation.",
    )
