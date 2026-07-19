"""Finalize a post-training candidate only after the GPU trainer has exited."""

from __future__ import annotations

import argparse

from core.storage.local import LocalStorage
from trainer.checkpoint_bridge import CheckpointBridge, CheckpointPublicationError
from trainer.grpo_config import GrpoSmokeConfig
from trainer.grpo_train import _preserve_checkpoint_failure, _publish_final_artifacts


def run(job_id: str, config: GrpoSmokeConfig, *, storage: LocalStorage | None = None) -> int:
    """Serve-test and publish the configured final candidate, fail closed."""
    if config.serving_gate_timing != "post_training":
        raise ValueError("finalizer requires serving_gate_timing=post_training")
    storage = storage or LocalStorage()
    staging = storage.root / job_id / ".post-training-finalizer"
    bridge = CheckpointBridge(
        storage,
        job_id,
        staging,
        lora_rank=config.lora_rank,
        lora_alpha=config.lora_alpha,
        serving_gate_timing="post_training",
    )
    try:
        checkpoint = bridge.finalize_candidate(config.total_steps)
        _publish_final_artifacts(
            storage,
            job_id,
            config,
            checkpoint / f"global_step_{config.total_steps}",
        )
    except Exception as caught:
        error = (
            caught
            if isinstance(caught, CheckpointPublicationError)
            else CheckpointPublicationError(
                step=config.total_steps,
                native_checkpoint=staging / f"global_step_{config.total_steps}",
                cause=caught,
            )
        )
        _preserve_checkpoint_failure(storage, job_id, bridge, error)
        print(f"{job_id} stopped after post-training serving-gate failure", flush=True)
        return 74
    print(
        f"Finished {job_id}; post-training service-accepted checkpoint is {checkpoint}",
        flush=True,
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finalize a VerifierForge checkpoint candidate")
    parser.add_argument("--job", required=True)
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(run(args.job, GrpoSmokeConfig.load(args.config)))


if __name__ == "__main__":
    main()
