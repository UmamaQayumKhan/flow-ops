# Northwind Cloud — Security and Data Retention

## Certifications

Northwind Cloud holds SOC 2 Type II (most recent report covering 1 January to 31 December
2025) and ISO 27001:2022. Reports are available under NDA through the trust portal.

Northwind is not HIPAA-eligible. Customers must not upload protected health information.
A BAA is not offered on any tier.

## Hosting and regions

The managed service runs on AWS. Three regions are available at workspace creation and
cannot be changed afterwards:

- `us-east-1` (N. Virginia) — default
- `eu-central-1` (Frankfurt)
- `ap-southeast-2` (Sydney)

Workspace data stays in its region. Aggregated, non-identifying usage telemetry is
processed in `us-east-1` regardless of workspace region.

## Encryption

Data is encrypted at rest with AES-256 and in transit with TLS 1.2 or higher. TLS 1.0 and
1.1 were disabled in June 2025.

Customer-managed encryption keys (CMEK) through AWS KMS are Enterprise only and must be
requested before workspace creation. CMEK cannot be enabled on an existing workspace.

## Data retention

| Data type | Retention | Notes |
| --- | --- | --- |
| Workspace content | Life of workspace | Deleted 30 days after workspace deletion |
| Audit logs | 400 days | Enterprise; 90 days on Growth; not available on Starter |
| API access logs | 30 days | All tiers |
| Backups | 35 days | Point-in-time recovery within 7 days |
| Deleted items (trash) | 30 days | Restorable by an admin |
| Suspended workspaces | 60 days | See billing policy, then permanent deletion |

Audit log export is available through `GET /v2/audit-logs` on Growth and Enterprise. The
endpoint is cursor-paginated and capped at 10,000 records per request.

## Deletion requests

A workspace owner can request deletion from workspace settings. Deletion is soft for 30
days, during which an owner can cancel it. After 30 days the data is purged from primary
storage; backups age out within a further 35 days, so full erasure completes within 65 days
of the request.

Individual data subject deletion requests under GDPR or CCPA are handled through
privacy@northwind.cloud and completed within 30 days.

## Sub-processors

Current sub-processors are AWS (hosting), Stripe (payments), Twilio SendGrid (transactional
email), Datadog (observability, `us-east-1`) and Zendesk (support). The authoritative list
with 30 days' notice of changes is published on the trust portal.

## Incident response

Security incidents are triaged within 1 hour of detection. Customers affected by a
confirmed breach of their data are notified within 72 hours of confirmation, which meets
the GDPR Article 33 window.

Severity levels:

- **SEV1** — confirmed data exposure or full service outage. Executive notification
  immediate, customer notification within 72 hours, public postmortem within 14 days.
- **SEV2** — partial outage or degraded security control. Status page update within 30
  minutes, internal postmortem within 7 days.
- **SEV3** — single-customer impact or internal-only issue. Tracked in the normal
  engineering queue.

## Penetration testing

Northwind commissions an external penetration test annually and after any change to the
authentication system. Customers may run their own tests against their own workspace with
7 days' written notice to security@northwind.cloud; load testing above the workspace's
rate limit is not permitted.

## Vulnerability disclosure

Reports go to security@northwind.cloud. Northwind acknowledges within 2 business days and
targets 90 days to remediation for high-severity findings. There is no paid bug bounty
programme.
