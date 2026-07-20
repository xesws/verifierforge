# VerifierForge demo artifacts

This directory contains reviewer-safe D4 metrics and a complete, derived held-out report projection. Its ten arena cards come from the frozen M5 evidence identified in manifest.json. It intentionally excludes model weights, checkpoints, credentials, raw traffic, and the full 60x8 sample evidence. Run `VF_API_DATA_MODE=artifacts uvicorn app.api.main:app` to serve it.
