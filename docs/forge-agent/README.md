# Forge Agent documentation

This area owns the decision agent's contracts, read-only tools, guarded runner,
audit persistence, Gate C evaluator, and flag-contained product integration.
Every implementation wave must retain the design boundary: the agent may
recommend a `TrainingConfig` but must never import or invoke a provisioner,
trainer, GPU SDK, shell command, or paid execution handle.

Tool results and traces contain only metadata, redacted samples, declared
assumptions, public rationale, and token accounting. Hidden chain-of-thought,
credentials, and raw traffic outside an approved data contract are prohibited.
