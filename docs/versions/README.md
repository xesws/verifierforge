# Version Documents

Create one file per target release under its family directory:

```text
docs/versions/v0.2.x/v0.2.0-d1-remote-smoke.md
```

Every version document is written **before** implementation and includes:

1. Status and owner/worktree branch.
2. Problem, scope, and explicit non-goals.
3. Affected source files, contracts, and documentation-area files.
4. Validation commands and merge criteria.

Use `v0.<minor>.0` for a feature or architectural capability and increment the patch for bugs, operational work, or documentation-only work. A version may not be reused by a separate active worktree.
