# VerifierForge demo artifacts

This directory contains reviewer-safe D4 metrics and held-out report metadata. It intentionally excludes model weights, checkpoints, credentials, and raw traffic. Run `VF_API_DATA_MODE=artifacts uvicorn app.api.main:app` to serve it.
