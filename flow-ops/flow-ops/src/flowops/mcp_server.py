"""An MCP server exposing Northwind's business tools, over stdio and HTTP/SSE.

Why MCP rather than a handful of n8n HTTP Request nodes: the same tool
definitions then serve the n8n AI Agent node, a desktop MCP client session, and any
other client, from one implementation. Wire the tools into n8n directly and you
end up reimplementing them for every other consumer, after which the copies
disagree about the refund policy.

The JSON-RPC layer is implemented directly rather than via an SDK. It is about
150 lines, it keeps the dependency surface at zero for stdio mode, and the wire
format stays visible -- which matters when a client sends something unexpected
and you need to see exactly what.

Transports:
    python -m flowops.mcp_server                 # stdio (desktop MCP clients)
    python -m flowops.mcp_server --http --port 8080   # HTTP + SSE (n8n)
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "flowops", "version": "1.0.0"}

# FastAPI is imported at module level, optionally, rather than inside
# build_http_app. This module uses `from __future__ import annotations`, so
# annotations are strings that FastAPI resolves with get_type_hints against
# MODULE globals -- a function-local import leaves `Request` unresolvable and
# FastAPI silently reinterprets the parameter as a query field, which surfaces
# as a 422 on every POST. The try/except keeps stdio mode dependency-free.
try:
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import StreamingResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover - stdio-only install
    FASTAPI_AVAILABLE = False

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"


# --------------------------------------------------------------------------
# JSON-RPC errors
# --------------------------------------------------------------------------

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# --------------------------------------------------------------------------
# tool registry
# --------------------------------------------------------------------------


@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[..., Any]

    def descriptor(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "inputSchema": self.schema}


@dataclass
class FlowOpsServer:
    tools: dict[str, Tool] = field(default_factory=dict)
    accounts: dict[str, Any] = field(default_factory=dict)
    policies: dict[str, str] = field(default_factory=dict)
    initialised: bool = False

    def __post_init__(self) -> None:
        self._load_data()
        self._register_tools()

    # -- data ------------------------------------------------------------
    def _load_data(self) -> None:
        accounts_path = DATA / "accounts.json"
        if accounts_path.exists():
            self.accounts = json.loads(accounts_path.read_text(encoding="utf-8"))
        kb = DATA / "kb"
        if kb.exists():
            for path in sorted(kb.glob("*.md")):
                self.policies[path.stem] = path.read_text(encoding="utf-8")

    # -- tools -----------------------------------------------------------
    def tool(self, name: str, description: str, schema: dict[str, Any]):
        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.tools[name] = Tool(name, description, schema, fn)
            return fn

        return register

    def _register_tools(self) -> None:
        @self.tool(
            "policy_search",
            "Search Northwind policy documents. Returns matching sections with their source. "
            "Call this before making any policy claim.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "search terms"},
                    "top_k": {"type": "integer", "description": "sections to return, 1-5", "default": 3},
                },
                "required": ["query"],
            },
        )
        def policy_search(query: str, top_k: int = 3) -> dict[str, Any]:
            terms = [t for t in query.lower().split() if len(t) > 2]
            hits: list[tuple[float, str, str, str]] = []
            for doc_id, text in self.policies.items():
                section = ""
                buffer: list[str] = []

                def score_section(sec: str, body: str) -> None:
                    blob = f"{sec} {body}".lower()
                    if not blob.strip():
                        return
                    score = sum(blob.count(t) for t in terms)
                    # heading matches are worth more than body mentions
                    score += 3 * sum(1 for t in terms if t in sec.lower())
                    if score:
                        hits.append((score, doc_id, sec, body.strip()[:1200]))

                for line in text.splitlines():
                    if line.startswith("#"):
                        score_section(section, "\n".join(buffer))
                        section = line.lstrip("# ").strip()
                        buffer = []
                    else:
                        buffer.append(line)
                score_section(section, "\n".join(buffer))

            hits.sort(key=lambda h: h[0], reverse=True)
            top = hits[: max(1, min(int(top_k), 5))]
            return {
                "found": len(top),
                "sections": [
                    {"n": i, "doc": doc, "section": sec, "text": body, "score": score}
                    for i, (score, doc, sec, body) in enumerate(top, start=1)
                ],
            }

        @self.tool(
            "account_lookup",
            "Fetch a workspace's tier, plan term, billing status and seat usage.",
            {
                "type": "object",
                "properties": {"workspace_id": {"type": "string"}},
                "required": ["workspace_id"],
            },
        )
        def account_lookup(workspace_id: str) -> dict[str, Any]:
            account = self.accounts.get(workspace_id)
            if account is None:
                return {
                    "found": False,
                    "error": f"workspace '{workspace_id}' not found",
                    "known_workspaces": sorted(self.accounts),
                }
            return {"found": True, **account}

        @self.tool(
            "refund_policy",
            "Authoritative refund eligibility and pro-rata amount. Never reason about "
            "refund eligibility yourself; call this tool.",
            {
                "type": "object",
                "properties": {
                    "plan": {"type": "string", "enum": ["monthly", "annual"]},
                    "days_into_term": {"type": "integer", "default": 0},
                    "monthly_price": {"type": "number", "default": 0},
                    "is_overage": {"type": "boolean", "default": False},
                    "via_reseller": {"type": "boolean", "default": False},
                },
                "required": ["plan"],
            },
        )
        def refund_policy(
            plan: str,
            days_into_term: int = 0,
            monthly_price: float = 0.0,
            is_overage: bool = False,
            via_reseller: bool = False,
        ) -> dict[str, Any]:
            # The three exceptions that contradict the headline rule, in the
            # order they override it. Computed, never generated.
            if is_overage:
                return {
                    "eligible": False,
                    "amount_usd": 0.0,
                    "reason": "overage charges are never refundable, including inside the 30-day window",
                    "policy_ref": "billing-policy#overage-charges",
                }
            if via_reseller:
                return {
                    "eligible": False,
                    "amount_usd": 0.0,
                    "reason": "reseller purchases are governed by the reseller's terms",
                    "policy_ref": "billing-policy#annual-plans",
                }
            if plan.lower() == "monthly":
                return {
                    "eligible": False,
                    "amount_usd": 0.0,
                    "reason": "monthly subscriptions are not refundable",
                    "policy_ref": "billing-policy#monthly-plans",
                }
            if plan.lower() != "annual":
                return {
                    "eligible": False,
                    "amount_usd": 0.0,
                    "reason": f"unrecognised plan '{plan}'; needs human review",
                    "policy_ref": None,
                }
            if days_into_term > 30:
                return {
                    "eligible": False,
                    "amount_usd": 0.0,
                    "reason": f"day {days_into_term} is past the 30-day refund window",
                    "policy_ref": "billing-policy#annual-plans",
                }
            months_elapsed = max(1, (days_into_term + 29) // 30)
            unused = max(0, 12 - months_elapsed)
            amount = round(unused * float(monthly_price), 2)
            return {
                "eligible": amount > 0,
                "amount_usd": amount,
                "reason": f"annual plan within 30 days; pro-rata on {unused} whole unused months",
                "approval_required": credit_authority(amount)["approval_required"],
                "policy_ref": "billing-policy#annual-plans",
            }

        @self.tool(
            "credit_authority",
            "Which approval level a goodwill credit of a given size requires.",
            {
                "type": "object",
                "properties": {"amount_usd": {"type": "number"}},
                "required": ["amount_usd"],
            },
        )
        def credit_authority(amount_usd: float) -> dict[str, Any]:
            amount = float(amount_usd)
            if amount <= 200:
                level, who = "agent", "a support agent may issue this without approval"
            elif amount <= 1000:
                level, who = "support_lead", "needs support lead approval"
            else:
                level, who = "finance", "needs finance approval"
            return {
                "amount_usd": amount,
                "approval_required": level,
                "note": who,
                "agent_can_issue": level == "agent",
            }

        @self.tool(
            "escalation_check",
            "Whether a ticket must be escalated to a human, per the support runbook. "
            "Returns the matched triggers.",
            {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "requested_amount_usd": {"type": "number", "default": 0},
                },
                "required": ["message"],
            },
        )
        def escalation_check(message: str, requested_amount_usd: float = 0.0) -> dict[str, Any]:
            import re

            patterns = (
                (r"(?:data|records?)\s+(?:from|belonging to)\s+another\s+(?:workspace|company|customer)", "cross-tenant data exposure"),
                (r"(?:another|other|someone else'?s?)\s+(?:workspace|company|customer)'?s?\s+(?:data|records?)", "cross-tenant data exposure"),
                (r"\bunauthoris?z?ed\s+(?:charge|payment|transaction)", "disputed charge"),
                (r"(?:did\s*n[o']?t|didn'?t|never)\s+(?:\w+\s+){0,3}authori[sz]e?", "disputed charge"),
                (r"\bfraud(?:ulent)?\b|\bchargeback\b", "disputed charge"),
                (r"\bdata\s+breach\b|\b(?:been|was)\s+hacked\b|\bcompromised\b", "security incident"),
                (r"(?:nobody|no one|no users?)\s+can\s+(?:log\s*in|authenticate)", "domain-wide authentication failure"),
                (r"(?:entire|whole|all of our)\s+(?:domain|org\w*|company)\s+(?:cannot|can'?t)\s+(?:log\s*in|authenticate)", "domain-wide authentication failure"),
                (r"\bproduction\s+is\s+(?:down|offline)\b|\bcomplete\s+outage\b", "production outage"),
                (r"\blegal\s+action\b|\bour\s+(?:lawyer|solicitor|attorney)s?\s+(?:will|has)\b", "legal exposure"),
            )
            triggers = sorted(
                {label for pattern, label in patterns if re.search(pattern, message, re.I)}
            )
            amount = float(requested_amount_usd or 0)
            if amount > 1000:
                triggers.append(f"requested amount ${amount:,.2f} exceeds support authority")
            return {
                "escalate": bool(triggers),
                "triggers": triggers,
                "reason": "; ".join(triggers) if triggers else "no escalation trigger matched",
            }

    # -- MCP methods -----------------------------------------------------
    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch one JSON-RPC message. Returns None for notifications."""
        if request.get("jsonrpc") != "2.0":
            raise RpcError(INVALID_REQUEST, "jsonrpc must be '2.0'")

        method = request.get("method")
        params = request.get("params") or {}
        req_id = request.get("id")
        is_notification = req_id is None

        if method == "initialize":
            self.initialised = True
            result: Any = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            }
        elif method in {"notifications/initialized", "initialized"}:
            return None
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [t.descriptor() for t in self.tools.values()]}
        elif method == "tools/call":
            result = self._call_tool(params)
        elif method in {"resources/list", "prompts/list"}:
            # Declared unsupported rather than erroring: clients probe for these
            # on connect and an error here shows up as a broken server.
            result = {"resources": []} if method.startswith("resources") else {"prompts": []}
        else:
            raise RpcError(METHOD_NOT_FOUND, f"unknown method: {method}")

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = self.tools.get(name)
        if tool is None:
            raise RpcError(
                INVALID_PARAMS,
                f"unknown tool: {name}",
                {"available": sorted(self.tools)},
            )

        required = tool.schema.get("required", [])
        missing = [r for r in required if r not in arguments]
        if missing:
            raise RpcError(
                INVALID_PARAMS, f"missing required argument(s): {', '.join(missing)}"
            )

        allowed = set(tool.schema.get("properties", {}))
        cleaned = {k: v for k, v in arguments.items() if k in allowed}

        try:
            output = tool.handler(**cleaned)
        except TypeError as exc:
            raise RpcError(INVALID_PARAMS, f"bad arguments for {name}: {exc}") from exc
        except Exception as exc:
            # Tool errors are returned as isError content, not as a protocol
            # error: the model should see what went wrong and adapt, not have
            # the connection fail.
            return {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            }

        return {
            "content": [{"type": "text", "text": json.dumps(output, indent=2, default=str)}],
            "isError": False,
        }

    def dispatch_raw(self, raw: str) -> str | None:
        """Parse, dispatch and serialise. Used by both transports."""
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as exc:
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": PARSE_ERROR, "message": f"parse error: {exc.msg}"},
                }
            )

        if isinstance(request, list):  # batch
            responses = [r for r in (self._safe(m) for m in request) if r]
            return json.dumps(responses) if responses else None

        response = self._safe(request)
        return json.dumps(response) if response else None

    def _safe(self, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": INVALID_REQUEST, "message": "request must be an object"},
            }
        try:
            return self.handle(request)
        except RpcError as exc:
            error: dict[str, Any] = {"code": exc.code, "message": exc.message}
            if exc.data is not None:
                error["data"] = exc.data
            return {"jsonrpc": "2.0", "id": request.get("id"), "error": error}
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": INTERNAL_ERROR, "message": f"{type(exc).__name__}: {exc}"},
            }


# --------------------------------------------------------------------------
# transports
# --------------------------------------------------------------------------


def serve_stdio(server: FlowOpsServer) -> int:
    """Line-delimited JSON-RPC on stdin/stdout, for desktop MCP clients.

    Nothing may be written to stdout except protocol messages -- a stray print
    corrupts the stream and the client reports a cryptic parse failure. All
    logging goes to stderr.
    """
    print("flowops MCP server ready on stdio", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = server.dispatch_raw(line)
        if response:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()
    return 0


def build_http_app(server: FlowOpsServer, token: str = ""):
    """HTTP + SSE transport, which is what the n8n MCP Client node speaks."""
    import asyncio

    if not FASTAPI_AVAILABLE:
        raise RuntimeError(
            "HTTP transport needs fastapi and uvicorn: pip install -r requirements.txt"
        )

    app = FastAPI(title="FlowOps MCP", version=SERVER_INFO["version"])
    queues: dict[str, asyncio.Queue] = {}

    def check(authorization: str | None) -> None:
        if not token:
            return
        expected = f"Bearer {token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="unauthorised")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "protocol": PROTOCOL_VERSION,
            "tools": sorted(server.tools),
            "policies_loaded": len(server.policies),
            "accounts_loaded": len(server.accounts),
            "auth_required": bool(token),
        }

    @app.get("/tools")
    def list_tools() -> dict[str, Any]:
        """Plain-HTTP convenience view, handy for debugging without an MCP client."""
        return {"tools": [t.descriptor() for t in server.tools.values()]}

    @app.post("/messages")
    async def messages(
        request: Request, authorization: str | None = Header(default=None)
    ) -> Any:
        check(authorization)
        raw = (await request.body()).decode("utf-8")
        response = server.dispatch_raw(raw)
        return json.loads(response) if response else {}

    @app.get("/sse")
    async def sse(authorization: str | None = Header(default=None)) -> StreamingResponse:
        check(authorization)
        session = f"s{len(queues) + 1}"
        queues[session] = asyncio.Queue()

        async def events():
            # The endpoint event tells the client where to POST its requests;
            # this is how the SSE transport is bootstrapped.
            yield f"event: endpoint\ndata: /messages?session={session}\n\n"
            try:
                while True:
                    try:
                        payload = await asyncio.wait_for(queues[session].get(), timeout=15)
                        yield f"event: message\ndata: {payload}\n\n"
                    except asyncio.TimeoutError:
                        # Keep-alive: proxies drop an idle SSE stream.
                        yield ": keep-alive\n\n"
            finally:
                queues.pop(session, None)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(prog="flowops-mcp")
    ap.add_argument("--http", action="store_true", help="serve HTTP+SSE instead of stdio")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args(argv)

    server = FlowOpsServer()

    if not args.http:
        return serve_stdio(server)

    import uvicorn

    app = build_http_app(server, token=os.getenv("MCP_TOKEN", ""))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
