# Security Policy

## Supported versions

This project is a pre-1.0 MVP. Security fixes are applied to the `main` branch only; there are no
maintained release branches.

| Version | Supported |
|---|---|
| `main` (0.1.x) | Yes |
| Any tagged release older than the current `main` | No |

## Reporting a vulnerability

**Do not open a public issue, pull request or discussion for a security problem.**

Report privately through one of these channels:

- **Preferred:** GitHub private vulnerability reporting — *the Security tab → "Report a
  vulnerability"*. <!-- TODO: enable this in repository settings and confirm the link resolves -->
- **Email:** `<security contact — to be filled in before the first external report>`

Include, as far as you can:

- the affected component (API route, node, database adapter, MCP server, frontend page);
- the version or commit you tested;
- a minimal reproduction — request payload, SQL, or configuration;
- the impact you believe it has;
- whether the issue is already public anywhere.

**Never paste live credentials, connection strings, tokens or customer data into an issue, a pull
request, or a first email.** Redact them; describe the shape instead (`sk-…`, `<redacted>`). If you
believe a credential is already committed to this repository, say so and stop there — do not test it
against the live provider.

## What to expect

| Stage | Target |
|---|---|
| Acknowledgement of your report | within 5 business days |
| Initial assessment (severity, affected versions, plan) | within 10 business days |
| Fix or documented mitigation for high and critical issues | best effort; this is an unfunded MVP and no SLA is offered |

If you do not hear back within 10 business days, send a follow-up to the same channel before
disclosing publicly.

## Disclosure process

1. You report privately.
2. We confirm the issue and agree on a disclosure date. Our default is 90 days after the report, or
   sooner once a fix ships.
3. We prepare the fix on a private branch, add a regression test, and credit you in the release
   notes unless you prefer otherwise.
4. After the fix is on `main`, we publish a short advisory describing the impact and the workaround
   for anyone who cannot upgrade immediately.

If a credential has been exposed, rotation comes first and disclosure second: revoking the key
removes the risk, publishing the advisory does not.

## Scope

In scope:

- the FastAPI surface under `app/api/`, including authentication and the administrator gate;
- the read-only SQL policy in `app/security/sql_policy.py` and any way to make it execute a write,
  a DDL statement, or a file/external table function;
- bypassing `PermissionManager` or `ConstraintEnforcer`;
- session, query-cache or trace handling that leaks one request's data into another;
- secrets leaking through responses, logs, traces or the audit store;
- the built frontend under `frontend_dist/` (XSS, auth token handling).

Out of scope, or already documented as a known limitation:

- **The deployment is single-tenant by design.** One administrator group shares one data set; there
  is no tenant isolation to break.
- **Anyone who can reach the API with a valid administrator token is trusted.** Publishing a
  database connection with a privileged account is a deployment decision, not a vulnerability.
- Findings that require an already-compromised host, or that only affect a local development
  configuration with `APP_ENV=development`.
- Missing hardening headers on the API, unless they enable a concrete attack you can describe.
- Rate limiting and denial-of-service by volume; there is no SLO yet.

## Security-relevant design notes

- Read-only is enforced three times: a single-statement AST policy, read-only driver sessions, and
  an `execute_sql` node with no write path.
- `JWT_SECRET` shorter than 32 characters, or left at its default, makes the application refuse to
  start.
- LLM API keys configured through the workspace are AES-GCM encrypted before being written to MySQL
  and are never returned by any endpoint.
- The optional usage audit (`LLM_AUDIT_PATH`) stores no question text and no SQL by default.
