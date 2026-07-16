# Post-hackathon hardening ideas

- Replace the current verl source pin `cb367c201187259e109a90f58af9ad9659126429`
  with a formal verl release explicitly compatible with `vllm==0.10.2`. The
  current 2026-07-13 mainline commit is a fragile reverse compatibility pin;
  investigate and validate this only after the hackathon run.
- Resolve each model to its cached snapshot's absolute directory before launch
  rather than a Hub ID. This would eliminate Hub metadata/probe paths. Do not
  change the current model path until a separate cache-identity validation is
  documented.
