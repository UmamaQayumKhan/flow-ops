"""A linter for n8n workflows.

This is the part of the project that would have saved me the most time in a real
job. n8n fails these quietly: a connection pointing at a renamed node simply
never fires, an orphan node sits on the canvas doing nothing, a webhook with no
authentication is open to the internet, and an exported workflow can carry a
real API key in a parameter straight into git.

Every check below corresponds to a way an automation breaks or leaks in
production, and each one runs in CI on every push.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

TRIGGER_MARKERS = (
    "webhook",
    "trigger",
    "cron",
    "interval",
    "n8n-nodes-base.start",
    "formTrigger",
    "emailReadImap",
)

# Sub-nodes hang off an AI node by a typed connection and are legitimately
# absent from the main graph, so the orphan check has to know about them.
AI_SUBNODE_MARKERS = (
    "lmChat",
    "memory",
    "Tool",
    "outputParser",
    "embeddings",
    "vectorStore",
    "textSplitter",
    "mcpClient",
)

AI_CONNECTION_TYPES = ("ai_languageModel", "ai_memory", "ai_tool", "ai_outputParser", "ai_embedding")

# Key-shaped literals. The point is to catch a real credential pasted into a
# parameter, so `={{ $credentials.x }}` references must not trip it.
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bsk-[A-Za-z0-9_-]{16,}", "openai-style key"),
    (r"\bgsk_[A-Za-z0-9]{20,}", "groq key"),
    (r"\bAIza[A-Za-z0-9_-]{30,}", "google api key"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "slack token"),
    (r"\bghp_[A-Za-z0-9]{30,}", "github token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "aws access key id"),
    (r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}", "jwt"),
    (r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"][^'\"{}\s]{8,}['\"]", "inline credential"),
    (r"postgres(?:ql)?://[^:\s]+:[^@\s]+@", "database url with password"),
)

# Nodes that reach the network and should therefore retry.
NETWORK_NODE_MARKERS = ("httpRequest", "postgres", "slack", "gmail", "googleSheets", "mySql")


@dataclass
class Finding:
    level: str  # "error" | "warning"
    workflow: str
    code: str
    message: str
    node: str | None = None

    def __str__(self) -> str:
        where = f" [{self.node}]" if self.node else ""
        return f"{self.level.upper():<7} {self.workflow}{where}: {self.code} — {self.message}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, level: str, workflow: str, code: str, message: str, node: str | None = None) -> None:
        self.findings.append(Finding(level, workflow, code, message, node))

    def extend(self, other: "Report") -> None:
        self.findings.extend(other.findings)


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _walk_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk_strings(v)


def _is_trigger(node: dict[str, Any]) -> bool:
    return any(m.lower() in node.get("type", "").lower() for m in TRIGGER_MARKERS)


def _is_ai_subnode(node: dict[str, Any]) -> bool:
    return any(m.lower() in node.get("type", "").lower() for m in AI_SUBNODE_MARKERS)


def _is_network_node(node: dict[str, Any]) -> bool:
    return any(m.lower() in node.get("type", "").lower() for m in NETWORK_NODE_MARKERS)


def validate_workflow(data: dict[str, Any]) -> Report:
    report = Report()
    name = data.get("name", "<unnamed>")
    nodes: list[dict[str, Any]] = data.get("nodes", [])
    connections: dict[str, Any] = data.get("connections", {})

    if not nodes:
        report.add("error", name, "empty", "workflow has no nodes")
        return report

    names = [n.get("name", "") for n in nodes]
    by_name = {n.get("name"): n for n in nodes}

    # --- structural integrity ------------------------------------------
    duplicates = {n for n in names if names.count(n) > 1}
    for dup in sorted(duplicates):
        report.add("error", name, "duplicate-node-name", f"{dup!r} appears {names.count(dup)} times", dup)

    ids = [n.get("id") for n in nodes]
    dup_ids = {i for i in ids if ids.count(i) > 1}
    for dup in sorted(x for x in dup_ids if x):
        report.add("error", name, "duplicate-node-id", f"id {dup} is used more than once")

    # Dangling connections: n8n drops these silently and the branch never runs.
    referenced: set[str] = set()
    for source, by_type in connections.items():
        if source not in by_name:
            report.add("error", name, "dangling-source", f"connections reference unknown node {source!r}")
        for ctype, buckets in by_type.items():
            for bucket in buckets:
                for link in bucket:
                    target = link.get("node")
                    referenced.add(target)
                    if target not in by_name:
                        report.add(
                            "error",
                            name,
                            "dangling-target",
                            f"{source!r} connects to unknown node {target!r}",
                            source,
                        )
                    if link.get("type") != ctype:
                        report.add(
                            "error",
                            name,
                            "connection-type-mismatch",
                            f"{source!r} → {target!r} declared {ctype} but link says {link.get('type')}",
                            source,
                        )

    # --- triggers -------------------------------------------------------
    triggers = [n for n in nodes if _is_trigger(n)]
    if not triggers:
        report.add("error", name, "no-trigger", "workflow has no trigger node and can never run")

    # An error-trigger-only workflow is valid; a main trigger should exist too.
    main_triggers = [t for t in triggers if "errorTrigger" not in t.get("type", "")]
    if triggers and not main_triggers:
        report.add("warning", name, "only-error-trigger", "workflow has only an error trigger")

    # --- orphans --------------------------------------------------------
    for node in nodes:
        nname = node.get("name")
        if nname in referenced or nname in connections:
            continue
        if _is_trigger(node) or _is_ai_subnode(node):
            continue
        report.add(
            "error",
            name,
            "orphan-node",
            "node is not connected to anything and will never execute",
            nname,
        )

    # --- AI wiring ------------------------------------------------------
    agents = [n for n in nodes if n.get("type", "").endswith((".agent", ".chainLlm", ".informationExtractor", ".textClassifier"))]
    for agent in agents:
        aname = agent.get("name")
        has_model = any(
            link.get("node") == aname
            for by_type in connections.values()
            for ctype, buckets in by_type.items()
            if ctype == "ai_languageModel"
            for bucket in buckets
            for link in bucket
        )
        if not has_model:
            report.add(
                "error",
                name,
                "ai-node-without-model",
                "AI node has no ai_languageModel connection; it cannot run",
                aname,
            )

        opts = agent.get("parameters", {}).get("options", {})
        if agent.get("type", "").endswith(".agent") and "maxIterations" not in opts:
            report.add(
                "warning",
                name,
                "unbounded-agent-loop",
                "agent has no maxIterations; a confused model can loop until the rate limit",
                aname,
            )

    # --- secrets --------------------------------------------------------
    for node in nodes:
        for text in _walk_strings(node.get("parameters", {})):
            # Expression references to credentials are the correct pattern.
            if "$credentials" in text:
                continue
            for pattern, label in SECRET_PATTERNS:
                if re.search(pattern, text):
                    report.add(
                        "error",
                        name,
                        "inline-secret",
                        f"parameter looks like a {label}; use a credential reference",
                        node.get("name"),
                    )
                    break

    # --- webhook exposure ----------------------------------------------
    # Match the webhook *trigger* exactly. A substring match on "webhook" also
    # catches `respondToWebhook`, which is an output node with no auth setting
    # of its own -- the linter's own first false positive.
    for node in nodes:
        if node.get("type") != "n8n-nodes-base.webhook":
            continue
        params = node.get("parameters", {})
        if params.get("authentication") in (None, "none", ""):
            report.add(
                "error",
                name,
                "unauthenticated-webhook",
                "public webhook has no authentication; anyone can trigger LLM spend",
                node.get("name"),
            )

    # --- resilience -----------------------------------------------------
    for node in nodes:
        if not _is_network_node(node):
            continue
        if node.get("retryOnFail") or node.get("onError") == "continueRegularOutput":
            continue
        report.add(
            "warning",
            name,
            "no-retry-on-network-node",
            "network call has neither retryOnFail nor an error branch; a transient blip loses the run",
            node.get("name"),
        )

    # --- operability ----------------------------------------------------
    settings = data.get("settings", {})
    if settings.get("saveDataErrorExecution") != "all":
        report.add(
            "warning",
            name,
            "error-executions-not-saved",
            "failed executions are not retained, making failures undebuggable",
        )
    if data.get("active") is True:
        report.add(
            "warning",
            name,
            "committed-active",
            "workflow is committed in the active state; activation belongs to the environment",
        )
    if not data.get("meta", {}).get("description"):
        report.add("warning", name, "no-description", "workflow has no description")

    return report


def validate_file(path: str | Path) -> Report:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report = Report()
        report.add("error", p.name, "invalid-json", f"{exc.msg} at line {exc.lineno}")
        return report
    return validate_workflow(data)


def validate_directory(directory: str | Path) -> Report:
    report = Report()
    files = sorted(Path(directory).glob("*.json"))
    if not files:
        report.add("error", str(directory), "no-workflows", "no .json workflows found")
    for path in files:
        report.extend(validate_file(path))
    return report
