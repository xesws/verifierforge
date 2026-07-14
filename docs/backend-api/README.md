# Backend API Documentation

This area owns `app/api/`, job lifecycle behavior, response compatibility, and shared API-facing contracts. `core/contracts.py` is a serial integration surface: changes require human approval and matching mock/test updates.

Before changing an endpoint or contract, add a versioned document describing request/response shape, status semantics, compatibility, failure behavior, storage reads, and validation commands. Link the matching mock behavior and identify every consumer that must rebase after merge.
