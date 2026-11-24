# Worker service

**Last Updated:** 2025-11-30

Background jobs run here. Jobs must be idempotent because the queue delivers
at least once and retries aggressively on failure.

## Code Style

Use 2-space indentation everywhere. Prefer named exports over default exports.
Never commit directly to main; open a pull request and request one reviewer.
Run the formatter before every commit, and keep imports sorted alphabetically.

## Version Management

**Format:** XX.YY (e.g., 01.00, 01.01, 02.00) - zero-pad both major and minor.
**Bump version when:** structure changes, behaviours are updated, or constraints
are added or removed. Record the change in the changelog with a dated entry.
