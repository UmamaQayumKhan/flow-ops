# Northwind Cloud — SAML Single Sign-On Setup

SAML SSO is available on the Growth and Enterprise tiers. SCIM user provisioning is
Enterprise only.

## Prerequisites

- A workspace on Growth or Enterprise.
- A verified domain. Domain verification requires adding the TXT record
  `northwind-verify=<token>` to your DNS and can take up to 24 hours to propagate.
- An identity provider that supports SAML 2.0. Okta, Microsoft Entra ID, Google Workspace,
  OneLogin and JumpCloud are tested; any SAML 2.0 provider should work.

## Configuration values

Supply these to your identity provider:

- **ACS URL / Single Sign-On URL:** `https://auth.northwind.cloud/saml/acs`
- **Entity ID / Audience URI:** `https://auth.northwind.cloud/saml/metadata`
- **Name ID format:** `urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress`
- **Binding:** HTTP-POST

The environment variable `SSO_REDIRECT_URI` must be set on self-hosted deployments only.
It is ignored on the managed service, where the ACS URL above is fixed.

## Required attribute statements

| SAML attribute | Maps to | Required |
| --- | --- | --- |
| `email` | user login | yes |
| `firstName` | given name | yes |
| `lastName` | family name | yes |
| `groups` | role mapping | no |

If `groups` is not sent, every user who signs in through SSO is assigned the default role
configured on the SSO settings page.

## Role mapping

Group-to-role mapping is evaluated top to bottom and the first match wins. A user who
matches no rule receives the default role. Mapping values are compared case-insensitively.

Northwind roles are `owner`, `admin`, `member` and `viewer`. The `owner` role cannot be
assigned by SSO group mapping; ownership transfer is a manual action in workspace settings
and requires confirmation from the current owner.

## Enforcing SSO

Once SSO is verified, an admin can enable **Enforce SSO**, which disables password login
for all members of the verified domain. Two categories of account are exempt:

1. The workspace owner, who keeps password login so the workspace cannot be locked out if
   the identity provider fails.
2. Guest accounts on domains other than the verified domain.

Service accounts and API tokens are unaffected by SSO enforcement. API tokens continue to
authenticate with the token itself.

## Troubleshooting

### Error `SAML_AUDIENCE_MISMATCH`

The Audience URI in the identity provider does not exactly match the Entity ID above.
A trailing slash counts as a mismatch.

### Error `SAML_NO_EMAIL`

The assertion did not contain an `email` attribute, or sent it under a different name such
as `emailAddress` or `mail`. Rename the attribute statement to `email`.

### Error `SAML_CLOCK_SKEW`

The assertion timestamp is outside the permitted window. Northwind allows 3 minutes of
clock skew. Check NTP on the identity provider.

### Users land on the wrong workspace

A user who belongs to several workspaces with the same verified domain is sent to the most
recently active one. Use an identity-provider-initiated link with the
`?workspace=<slug>` parameter to pin the destination.

## SCIM provisioning

SCIM 2.0 is Enterprise only. The SCIM base URL is
`https://auth.northwind.cloud/scim/v2` and the bearer token is generated on the SSO
settings page. Deprovisioning a user through SCIM deactivates the account and releases the
seat at the end of the current billing period, not immediately.
