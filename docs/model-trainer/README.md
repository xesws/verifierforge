# Model Trainer Documentation

This area owns `trainer/` behavior: GRPO/verl plans, model and configuration choices, reward adaptation, checkpoint contents, resume behavior, and training validation.

Before touching trainer code, add or update `v0.<minor>.<patch>-<slug>.md` here and link it from the matching `docs/versions/` plan. Record model/config changes, checkpoint and RNG implications, GPU prerequisites, expected metrics, and exact smoke or training commands. Never document a real run as complete without the corresponding artifact and metrics location.
