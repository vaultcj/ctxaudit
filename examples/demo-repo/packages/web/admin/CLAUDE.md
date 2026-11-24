# Admin area

**Last Updated:** 2026-05-11

The admin area is permission-gated. Every route must check the role claim
before rendering, and must fail closed when the claim is missing.
