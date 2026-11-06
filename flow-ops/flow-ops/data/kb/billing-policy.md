# Northwind Cloud — Billing and Refund Policy

Effective 1 March 2026. Supersedes the November 2025 policy.

## Plans and pricing

Northwind Cloud is sold on three tiers. Prices are per workspace, per month, billed in USD.

| Tier | Monthly | Annual (per month) | Included seats | Included API calls |
| --- | --- | --- | --- | --- |
| Starter | $29 | $24 | 5 | 50,000 |
| Growth | $149 | $124 | 25 | 500,000 |
| Enterprise | Custom | Custom | Unlimited | Negotiated |

Additional seats beyond the included allowance are billed at $9 per seat per month on
Starter and $7 per seat per month on Growth. Enterprise seat pricing is set in the order form.

## Billing cycle

Monthly subscriptions renew on the calendar day of the original purchase. If that day does
not exist in the renewal month, the charge falls on the last day of the month. Annual
subscriptions renew on the anniversary of the purchase date.

Invoices are issued on the renewal date and payment is attempted immediately. A failed
payment is retried on days 1, 3 and 7 after the invoice date.

## Dunning and suspension

If payment has not succeeded by day 7, the workspace enters a grace period of 7 further
days. During the grace period the workspace remains fully functional and the workspace
owner receives email notification on days 7, 10 and 13.

On day 14 after the failed invoice the workspace is suspended. A suspended workspace is
read-only: existing data remains accessible and exportable, API write endpoints return
HTTP 402, and scheduled jobs do not run.

Suspended workspaces are retained for 60 days. After 60 days of continuous suspension the
workspace and all its data are permanently deleted and cannot be recovered.

## Refunds

### Monthly plans

Monthly subscriptions are not refundable. Cancelling a monthly plan stops the next renewal
and the workspace stays on its paid tier until the end of the current period.

### Annual plans

Annual subscriptions are refundable on a pro-rata basis within the first 30 days of the
term. After day 30 of the term, annual subscriptions are not refundable. The pro-rata
amount is calculated on whole unused months, not partial months.

This does not apply to annual plans purchased through a reseller. Reseller purchases are
governed by the reseller's own refund terms and Northwind cannot issue a direct refund.

### Overage charges

Overage charges for API calls above the included allowance are never refundable, including
within the first 30 days of an annual term. Customers who expect variable load should
contact sales about a committed-use discount rather than relying on overage.

## Downgrades and upgrades

Upgrades take effect immediately and are charged pro-rata for the remainder of the current
period. Downgrades take effect at the start of the next billing period; no credit is issued
for the unused portion of the higher tier.

A downgrade that would leave the workspace with more seats than the target tier includes is
blocked until seats are removed.

## Taxes

Prices exclude sales tax, VAT and GST. Customers in the EU and UK must supply a valid VAT
number to be zero-rated; without one, VAT is charged at the rate of the billing country.

## Purchase orders and invoicing

Invoice-based billing with net-30 terms is available on Enterprise only. Growth and Starter
customers must pay by card or direct debit. Purchase orders are accepted on Enterprise
contracts with an annual value above $25,000.
