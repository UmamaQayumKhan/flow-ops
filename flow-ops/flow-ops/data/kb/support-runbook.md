# Northwind Cloud — Support Escalation Runbook

Internal document for the support team. Not customer-facing.

## Tiers of support

| Plan | Channel | First response target | Hours |
| --- | --- | --- | --- |
| Starter | Email only | 48 business hours | Mon–Fri |
| Growth | Email + in-app chat | 8 business hours | Mon–Fri |
| Enterprise | Email, chat, dedicated Slack channel | 1 hour for P1, 4 hours otherwise | 24/7 for P1 |

Business hours are 09:00–18:00 in the customer's registered timezone.

## Priority definitions

- **P1** — production down, data loss, or security concern. Enterprise only for the 1-hour
  target; Growth P1 follows the 8-hour target.
- **P2** — major feature unavailable with no workaround.
- **P3** — feature degraded, workaround exists.
- **P4** — question, documentation gap, or feature request.

## Escalation triggers

Escalate to engineering on-call immediately when any of the following is true:

1. More than three workspaces report the same symptom within 30 minutes.
2. Any report of data visible across workspace boundaries. This is always SEV1 regardless
   of the number of affected customers.
3. Authentication is failing for an entire verified domain.
4. A customer reports a charge they did not authorise.
5. A P1 ticket has had no engineering acknowledgement within 20 minutes.

Do not escalate for rate-limit complaints, billing disputes under $500, or feature
requests. Those follow the normal queue.

## Refund authority

Support agents may issue a goodwill credit of up to $200 without approval. Credits between
$200 and $1,000 need a support lead. Anything above $1,000, and any cash refund rather than
a credit, needs finance approval.

Refund eligibility itself is governed by the billing policy, not by this runbook. An agent
cannot refund a monthly plan or an overage charge even within the $200 discretion; the
discretion applies to credits only.

## Account recovery

If a customer has lost access to the owner account and SSO is enforced:

1. Verify the requester against the billing contact on file.
2. Require confirmation from a second admin on the workspace, or from the domain's DNS
   TXT record if no second admin exists.
3. Only then disable SSO enforcement temporarily and issue a password reset.

Never disable SSO enforcement on a single unverified request. This is the most common
social engineering vector against Northwind workspaces.

## Common resolutions

- **"Our API stopped working overnight"** — check for billing suspension (HTTP 402) before
  anything else. This is the cause roughly half the time.
- **"We can't log in after adding SSO"** — almost always `SAML_AUDIENCE_MISMATCH` from a
  trailing slash. See the SSO setup guide.
- **"We were charged after cancelling"** — confirm whether the cancellation stopped the
  next renewal or was expected to be a refund. Monthly plans are not refundable.
- **"Webhooks stopped firing"** — check whether the endpoint was auto-disabled after 7 days
  of consecutive failures.

## Tone

Acknowledge the impact before explaining the cause. Never speculate about a root cause on
the record; say an investigation is underway and give a time for the next update.
