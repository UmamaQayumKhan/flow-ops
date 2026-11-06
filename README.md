# FlowOps — n8n AI automations defined as code, plus an MCP tool server

Four production-shaped n8n workflows (57 nodes) generated from typed Python
definitions, linted in CI, and an MCP server that serves the same business
tools to the n8n AI Agent node, desktop MCP clients, and anything else that speaks
MCP.

```bash
pip install -r requirements-dev.txt
make test        # 87 tests
make generate    # build workflows/*.json
make validate    # 0 errors, 0 warnings
cp .env.example .env   # set the three required secrets
make up          # n8n + postgres + mcp
```

---

## Why this is not a folder of exported JSON

Most n8n portfolios are workflows exported from the canvas. That has three
problems I kept hitting:

1. **Exports are unreviewable.** n8n assigns a fresh random UUID to every node
   on export and rewrites canvas coordinates, so a one-line change produces a
   400-line diff. Nobody reviews that, so nobody catches anything in it.
2. **n8n fails these silently.** A connection pointing at a renamed node never
   fires. An orphan node sits on the canvas doing nothing. A webhook with no
   authentication is open to the internet. You find out in production.
3. **Exports leak.** A parameter containing a real API key goes straight into
   git with no warning.

So workflows here are Python, node IDs are derived deterministically from names
(regenerating is byte-identical), and `validate.py` lints every workflow on
every push.

### The linter

`make validate` → **0 errors, 0 warnings across 4 workflows**. What it checks,
and why each one is on the list:

| check | level | what it prevents |
| --- | --- | --- |
| `dangling-target` / `dangling-source` | error | a branch that silently never fires |
| `orphan-node` | error | a node on the canvas that can never execute |
| `no-trigger` | error | a workflow that can never run |
| `duplicate-node-id` / `duplicate-node-name` | error | expression references resolving to the wrong node |
| `inline-secret` | error | a real API key committed to git |
| `unauthenticated-webhook` | error | anyone on the internet triggering your LLM spend |
| `ai-node-without-model` | error | an AI node with no `ai_languageModel` connection |
| `connection-type-mismatch` | error | a mis-wired AI sub-node |
| `unbounded-agent-loop` | warning | a confused agent looping until the rate limit |
| `no-retry-on-network-node` | warning | a transient blip losing the whole run |
| `error-executions-not-saved` | warning | failures that cannot be debugged |
| `committed-active` | warning | a workflow activating itself on import |

The linter is tested against deliberately broken workflows, not just passing
ones — including seven distinct secret formats (OpenAI, Groq, Google, Slack,
GitHub, AWS, and a Postgres URL with an inline password) and a test asserting
that the correct `={{ $credentials.x }}` pattern does **not** trip the scanner.

It also caught a false positive in itself: a substring match on `"webhook"`
flagged `respondToWebhook`, which is an output node with no authentication
setting at all. `test_respond_to_webhook_is_not_treated_as_a_trigger` keeps it
fixed.

---

## The four workflows

### 1. Ticket Triage Agent — 17 nodes

```
Webhook → Sanitise Input → Injection Detected? ─true──→ Escalate To Human
                                   │false                      ↑
                                   ↓                           │
                           Triage Agent ──→ Validate ──→ Valid? ┤ (low confidence)
                        (Groq + memory + MCP)          │        │
                                                       │invalid ↓
                                                   Dead Letter  Route ──→ Auto Reply
                                                                            ↓
                                                              Log Outcome → Response
```

Input guardrails run **in the workflow, before the agent** — a Code node strips
six classes of prompt injection and redacts card numbers, API keys and bearer
tokens. A jailbreak attempt skips the agent entirely and goes to a human, so no
tokens are spent and no draft exists to leak.

The agent's structured output is validated by a Code node, and anything
malformed goes to a dead-letter table rather than driving an action.

### 2. Daily Ops Digest — 11 nodes

Scheduled at **07:40, not 08:00** — schedulers pile up on the hour. Two parallel
Postgres queries (ticket metrics, dead-letter counts) merge into one shaped
object, an LLM chain summarises it, and it goes to Slack and email with the raw
numbers attached.

Uses an LLM *chain*, not an agent: there is nothing to decide and no tool to
call, so an agent would add a tool loop for no benefit. Both queries are
`alwaysOutputData` because a quiet night returns no rows and the digest must
still send.

### 3. Lead Qualification Router — 16 nodes

Form → email validity filter → company enrichment → AI scoring against an
explicit rubric → routed to sales, nurture or archive → CRM upsert.

Two details that matter:

- The **cheapest filter runs first.** A malformed email address never costs an
  enrichment credit or an LLM call.
- The model scores; **code decides the band.** A Code node re-derives
  hot/warm/cold from the numeric score, because the band drives routing and an
  off-by-one band sends an enterprise lead to self-serve. It records
  `bandCorrected` so you can see how often the model disagreed — a rising rate
  means the prompt and the rubric have drifted apart.

Enrichment uses `neverError` plus `continueOnFail`: a provider outage must
downgrade the score, not drop the lead.

### 4. Invoice Extraction Pipeline — 13 nodes

PDF → deterministic text extraction → LLM structured extraction → **arithmetic
validation in code** → ledger or exception queue.

The validation node runs six independent checks: subtotal + tax = total, line
items sum to subtotal, quantity × unit price = line amount, ISO-4217 currency,
ISO-8601 dates, due date after issue date. Plus a hard rule that anything over
$10,000 goes to a human regardless of how clean the extraction looks.

LLMs transpose digits and mishandle thousands separators. An invoice that books
with the wrong total is a reconciliation problem for months, so the model reads
the document and code checks the maths. The ledger table carries a
`CHECK` constraint enforcing the same reconciliation, so the database refuses a
bad row even if the workflow is bypassed.

---

## Patterns applied to all four

Asserted by tests across every workflow, so a fifth workflow cannot skip them:

- **Every workflow has an error-trigger entry point** that alerts `#ops-alerts`
  with the failed node and execution URL. Silent failure is n8n's default and
  the worst one: nobody finds out until someone asks why the digest stopped.
- **Every network node retries** — including the alert node itself, because
  losing the alert about a failure is worse than the failure.
- **Every AI output is validated by a Code node** before anything downstream
  acts on it (`test_every_ai_node_output_is_validated_in_code`).
- **Nothing is dropped.** Malformed output goes to `flowops_dead_letter` with
  the specific reason, ready to replay.
- **No secrets inline**, enforced by the linter.
- **Natural unique keys in Postgres.** Webhooks are at-least-once; without
  `UNIQUE (ticket_id, execution_id)` a retried delivery logs twice and every
  rate in the daily digest is wrong.

---

## The MCP server

Five tools, served over **stdio** (desktop MCP clients) and **HTTP+SSE** (the n8n
MCP Client node) from one implementation.

| tool | purpose |
| --- | --- |
| `policy_search` | section-level search over the policy documents |
| `account_lookup` | workspace tier, plan term, billing status, seats |
| `refund_policy` | authoritative eligibility and pro-rata amount |
| `credit_authority` | which approval level a credit size needs |
| `escalation_check` | runbook escalation triggers matched in a message |

MCP rather than n8n HTTP Request nodes because the same definitions then serve
every consumer. Wire the tools into n8n directly and you reimplement them for
the next client, after which the copies disagree about the refund policy.

`refund_policy` is computed, never generated. It implements the three exceptions
that contradict the headline rule — overage charges are never refundable even
inside the 30-day window, reseller purchases cannot be refunded directly, and
monthly plans are never refundable — each of which is exactly what a model
asked to "apply the refund policy" gets wrong. All six branches are tested.

The JSON-RPC layer is implemented directly: about 150 lines, stdlib only in
stdio mode, and the wire format stays visible when a client sends something
unexpected. It handles batch requests, returns no response to notifications,
and answers the `resources/list` and `prompts/list` probes that clients send on
connect with empty lists rather than errors — returning an error there makes a
working server look broken.

Tool exceptions come back as `isError` content, not protocol errors, so the
model sees what went wrong and adapts instead of the connection dropping.

### Desktop MCP clients

```json
{
  "mcpServers": {
    "flowops": {
      "command": "python",
      "args": ["-m", "flowops.mcp_server"],
      "env": { "PYTHONPATH": "/absolute/path/to/flow-ops/src" }
    }
  }
}
```

Note that stdio mode writes **nothing** to stdout except protocol messages — a
stray `print` corrupts the stream and the client reports a cryptic parse
failure. All logging goes to stderr.

---

## Deploying

```bash
cp .env.example .env
# set POSTGRES_PASSWORD, N8N_PASSWORD, N8N_ENCRYPTION_KEY, MCP_TOKEN
make up
```

| service | where |
| --- | --- |
| n8n | http://localhost:5678 |
| MCP | http://localhost:8080/healthz |
| Postgres | internal, schema applied from `sql/` on first boot |

Then in n8n: create the eight credentials listed in `.env.example`, and import
the workflows from `/workflows` (mounted read-only into the container).

Compose details that are deliberate: Postgres rather than the default SQLite,
which locks under concurrent executions; `N8N_ENCRYPTION_KEY` set explicitly,
because losing it makes every stored credential unrecoverable; execution
pruning at 14 days, because executions accumulate forever by default and
eventually fill the disk; and `:?` on every required secret, so compose
refuses to boot insecurely rather than starting with a blank password. CI
asserts that last behaviour both ways.

---

## Layout

```
src/flowops/
  builder.py     typed n8n JSON emitter, deterministic node IDs
  workflows.py   the four workflows as code
  validate.py    the linter: 12 checks
  mcp_server.py  MCP over stdio and HTTP/SSE, 5 tools
  cli.py         generate · validate · check · show
workflows/       generated JSON, importable into n8n unchanged
sql/             schema with unique keys and a reconciliation CHECK
data/            policy KB and account fixtures the MCP server serves
tests/           87 tests
```

`make check` fails if the committed JSON has drifted from its Python
definition — the failure mode this whole approach exists to prevent, where
someone edits on the canvas, exports over the generated file, and Python
quietly stops being the source of truth.

## Known limits

- The enrichment provider in the lead workflow is a placeholder URL; swap it
  for Clearbit, Apollo or similar.
- The linter checks structure, secrets and resilience. It does not type-check
  n8n expressions — `{{ $json.nonexistent }}` still passes.
- The SSE transport is a minimal implementation sufficient for the n8n MCP
  Client node; it does not implement server-initiated requests such as
  sampling.
- Workflows are importable but not auto-provisioned. Pushing them through the
  n8n REST API on deploy would be the next step.
