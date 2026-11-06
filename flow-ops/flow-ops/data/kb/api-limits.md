# Northwind Cloud — API Rate Limits and Error Handling

## Limits by tier

Rate limits are applied per workspace, not per API token.

| Tier | Requests/minute | Burst | Concurrent connections |
| --- | --- | --- | --- |
| Starter | 60 | 120 | 5 |
| Growth | 600 | 1,200 | 25 |
| Enterprise | 3,000 (default) | 6,000 | 100 |

The burst allowance is a token bucket that refills at the sustained rate. A client may
exceed the per-minute limit briefly up to the burst ceiling, then is throttled to the
sustained rate.

Write endpoints (`POST`, `PATCH`, `PUT`, `DELETE`) count double against the limit. A
Growth workspace therefore sustains 600 reads per minute or 300 writes per minute.

## Rate limit headers

Every response carries:

- `X-RateLimit-Limit` — the sustained limit for the window
- `X-RateLimit-Remaining` — requests left in the current window
- `X-RateLimit-Reset` — Unix timestamp when the window resets
- `Retry-After` — present on 429 responses only, in seconds

## Handling 429

A throttled request returns HTTP 429 with a JSON body:

```json
{ "error": "rate_limited", "retry_after": 12, "request_id": "req_01HX..." }
```

Clients must honour `Retry-After`. Retrying faster than the header indicates does not
reset the window and repeated violation can trigger a temporary block of 15 minutes.

Recommended client behaviour is exponential backoff with full jitter, capped at 60 seconds,
with a maximum of 5 attempts.

## Idempotency

`POST` endpoints that create resources accept an `Idempotency-Key` header. Keys are
retained for 24 hours. A repeat request with the same key and the same body returns the
original response. A repeat with the same key and a *different* body returns HTTP 422 with
error `idempotency_key_reuse`.

Idempotency keys are required on `POST /v2/payments` and optional everywhere else.

## Pagination

List endpoints are cursor-paginated. Pass `limit` (max 200, default 50) and follow
`next_cursor` from the response body. Offset pagination was removed in API v2; a request
containing `offset` returns HTTP 400 with error `offset_not_supported`.

Cursors expire after 10 minutes. An expired cursor returns HTTP 410 and the client must
restart the iteration.

## Error codes

| HTTP | Error code | Meaning |
| --- | --- | --- |
| 400 | `invalid_request` | Malformed body or parameters |
| 401 | `invalid_token` | Token missing, expired or revoked |
| 402 | `workspace_suspended` | Billing suspension; see billing policy |
| 403 | `insufficient_scope` | Token lacks the required scope |
| 404 | `not_found` | Resource does not exist or is not visible to this token |
| 409 | `conflict` | Version conflict on optimistic concurrency |
| 410 | `cursor_expired` | Pagination cursor expired |
| 422 | `unprocessable` | Validation failed; `details` lists the fields |
| 429 | `rate_limited` | Throttled |
| 503 | `maintenance` | Planned maintenance; retry after the window |

## Webhooks

Webhook deliveries are retried on failure with exponential backoff for up to 24 hours:
after 1 minute, 5 minutes, 30 minutes, 2 hours, 6 hours and 18 hours. A delivery is
considered successful on any 2xx response within 10 seconds.

An endpoint that fails every delivery for 7 consecutive days is automatically disabled and
the workspace owner is notified. Webhook payloads are signed with HMAC-SHA256 in the
`Northwind-Signature` header; verify the signature before trusting the payload.

## Requesting a limit increase

Enterprise customers can request a higher sustained limit through their account manager.
Requests are reviewed against the 95th percentile of the workspace's actual traffic over
the previous 30 days. Increases above 10,000 requests per minute require a technical review.
