# Acme Monorepo

**Last Updated:** 2026-10-20

This is the root context for the Acme monorepo. Everything below is loaded by
every single session, in every package, whether or not it is relevant.

## Code Style

Use 2-space indentation everywhere. Prefer named exports over default exports.
Never commit directly to main; open a pull request and request one reviewer.
Run the formatter before every commit, and keep imports sorted alphabetically.

## Version Management

**Format:** XX.YY (e.g., 01.00, 01.01, 02.00) - zero-pad both major and minor.
**Bump version when:** structure changes, behaviours are updated, or constraints
are added or removed. Record the change in the changelog with a dated entry.

## Deployment

Deploys run from CI on merge to main. Never deploy from a laptop.

## Architecture

The monorepo holds three deployable units and two shared libraries. Packages
communicate over HTTP in development and over the internal mesh in production.
Shared types live in packages/types and are generated from the OpenAPI spec.
Never hand-edit generated types; regenerate them and commit the result.

## Environment Setup

Node 20 and Python 3.12 are both required. Use the repo's .nvmrc and .python-version
files rather than system defaults. Install dependencies with the workspace root
command so lockfiles stay consistent across packages.

Secrets come from the vault, never from .env files committed to the repo.
Local development uses the seeded database; do not point local at staging.

## Testing Standards

Every pull request must keep coverage above eighty percent. Integration tests
run against ephemeral containers that are torn down after each run. Flaky tests
are quarantined, not retried, and a ticket is opened the same day.

Unit tests must not reach the network. If a test needs a third-party response,
record a fixture and replay it. Snapshot tests are reviewed like any other code.

## Security

All user input is validated at the boundary. Authorisation is checked in the
service layer, never only in the UI. Dependencies are scanned weekly and
critical advisories are patched within two working days.

Never log credentials, tokens, or full request bodies. Redact before logging.

## Pull Request Process

Open a draft early so CI runs. Mark ready for review only when the checks pass.
Squash on merge and keep the commit message in the imperative mood. Link the
issue the PR closes so the board updates automatically.

Reviews focus on correctness first, then clarity, then style. Style arguments
that the formatter cannot settle are resolved by the package owner.

## Troubleshooting

If the dev server will not start, clear the build cache before anything else.
If types look stale, regenerate them from the spec. If the database refuses
connections, check the container health rather than restarting blindly.

## Logging and Observability

Structured JSON logs only. Every request carries a correlation id that is
propagated across service boundaries and included in every log line.

Metrics are emitted per endpoint: latency, error rate, saturation. Dashboards
live in the observability repo and are reviewed monthly. Alerts page only for
user-visible breakage; everything else opens a ticket instead.

Traces sample at ten percent in production and one hundred percent in staging.
Never trace request bodies; trace timings and identifiers only.

## Data Handling

Personal data is stored only in the primary database, never in caches, logs,
or analytics payloads. Retention is thirteen months unless legal says otherwise.

Exports are generated asynchronously and delivered as signed, expiring links.
Deletion requests cascade through the worker queue and must complete in thirty
days. Confirm deletion with a verification job rather than trusting the cascade.

## Dependency Policy

Prefer the standard library. A new dependency needs a one-paragraph
justification in the PR covering what it replaces and who maintains it.

Pin exact versions in applications and use ranges only in shared libraries.
Remove dependencies that have gone a year without a release unless there is a
clear reason to keep them.

## Documentation

Every package carries a README explaining what it does and how to run it.
Architecture decisions go in docs/adr as numbered records; never rewrite an
accepted record, supersede it with a new one that references the original.

Runbooks live beside the service they describe and are updated with the code.
