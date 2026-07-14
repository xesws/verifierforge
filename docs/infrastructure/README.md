# Infrastructure Documentation

This area owns RunPod operations, `scripts/vf`, SSH/deploy-key setup, `/workspace` persistence, storage backends, bootstrap behavior, and sandbox operational constraints.

Before changing these systems, create a versioned area document with remote prerequisites, commands, secrets-free configuration, failure/rollback behavior, data-flow rules, and validation. Keep checkpoints out of Git and rsync; document only their Storage location. Do not place credentials, pod IPs, or private keys in this directory.
