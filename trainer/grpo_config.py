"""The small, inspectable D2 GRPO configuration and verl command overrides."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path


CONFIG_DIRECTORY = Path(__file__).with_name("verl_configs")

RAY_DIAGNOSTIC_ENVIRONMENT = (
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "VLLM_LOGGING_LEVEL",
    "RAY_DEDUP_LOGS",
    "PYTHONFAULTHANDLER",
)

TOTAL_EPOCHS_SAFETY_MARGIN = 2


def _hydra_string_literal(value: str) -> str:
    """Return a Hydra override value that cannot be coerced to a scalar."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _ray_diagnostic_environment_overrides() -> list[str]:
    """Propagate explicitly set H100 diagnostics through Ray worker startup."""
    overrides: list[str] = []
    for name in RAY_DIAGNOSTIC_ENVIRONMENT:
        value = os.environ.get(name)
        if value is not None:
            overrides.append(
                "+ray_kwargs.ray_init.runtime_env.env_vars."
                f"{name}={_hydra_string_literal(value)}"
            )
    return overrides


@dataclass(frozen=True)
class GrpoSmokeConfig:
    """Operator-visible values for a bounded verifier-backed GRPO run."""

    model_path: str
    total_steps: int
    total_epochs: int | None
    train_batch_size: int
    rollout_n: int
    max_prompt_length: int
    max_response_length: int
    lora_rank: int
    lora_alpha: int
    learning_rate: float
    kl_loss_coef: float
    rollout_gpu_memory_utilization: float
    enforce_eager: bool
    vllm_attention_backend: str | None
    checkpoint_every: int
    validation_every: int
    dataset_mode: str
    reward_mode: str
    save_hf_model: bool
    entropy_brake: bool
    serving_gate_timing: str

    @classmethod
    def load(cls, name: str = "grpo_v1_0p5b") -> "GrpoSmokeConfig":
        """Load an operator-visible YAML config from ``trainer/verl_configs``."""
        if Path(name).name != name or not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"invalid GRPO config name: {name!r}")
        path = CONFIG_DIRECTORY / f"{name}.yaml"
        try:
            import yaml
        except ModuleNotFoundError as error:  # pragma: no cover - supplied by verl/Hydra on the pod
            raise RuntimeError("GRPO configuration requires PyYAML (installed with verl)") from error

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ValueError(f"unknown GRPO config: {name}") from error
        if not isinstance(raw, dict):
            raise ValueError(f"GRPO config {path} must contain a mapping")

        expected = {field.name for field in cls.__dataclass_fields__.values()}
        if set(raw) != expected:
            missing = sorted(expected - set(raw))
            extra = sorted(set(raw) - expected)
            raise ValueError(f"GRPO config {path} fields mismatch; missing={missing}, extra={extra}")
        config = cls(**raw)
        config.validate()
        return config

    def validate(self) -> None:
        if not self.model_path:
            raise ValueError("model_path must not be empty")
        for name in (
            "total_steps",
            "train_batch_size",
            "rollout_n",
            "max_prompt_length",
            "max_response_length",
            "lora_rank",
            "lora_alpha",
            "checkpoint_every",
            "validation_every",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.total_epochs is not None and (
            not isinstance(self.total_epochs, int) or isinstance(self.total_epochs, bool)
        ):
            raise ValueError("total_epochs must be a positive integer or null")
        if self.total_epochs is not None and self.total_epochs < 1:
            raise ValueError("total_epochs must be a positive integer or null")
        if self.learning_rate <= 0 or self.kl_loss_coef < 0:
            raise ValueError("learning_rate must be positive and kl_loss_coef non-negative")
        if not 0 < self.rollout_gpu_memory_utilization <= 1:
            raise ValueError("rollout_gpu_memory_utilization must be in (0, 1]")
        if not isinstance(self.enforce_eager, bool):
            raise ValueError("enforce_eager must be a boolean")
        if self.vllm_attention_backend not in (None, "TORCH_SDPA"):
            raise ValueError("vllm_attention_backend must be null or TORCH_SDPA")
        if self.dataset_mode not in ("d2_split", "frozen_training_pool"):
            raise ValueError("dataset_mode must be d2_split or frozen_training_pool")
        if self.reward_mode not in ("verifier", "random_bernoulli"):
            raise ValueError("reward_mode must be verifier or random_bernoulli")
        if not isinstance(self.save_hf_model, bool):
            raise ValueError("save_hf_model must be a boolean")
        if not isinstance(self.entropy_brake, bool):
            raise ValueError("entropy_brake must be a boolean")
        if self.serving_gate_timing not in ("per_checkpoint", "post_training"):
            raise ValueError(
                "serving_gate_timing must be per_checkpoint or post_training"
            )

    @property
    def checkpoint_save_contents(self) -> str:
        """Return the explicit verl checkpoint payload for this run."""
        contents = "['model','optimizer','extra']"
        if self.save_hf_model:
            contents = "['model','optimizer','extra','hf_model']"
        return contents

    @property
    def reward_function_name(self) -> str:
        """Select a verifier or the independent random-control adapter."""
        return "compute_score" if self.reward_mode == "verifier" else "compute_random_score"

    def with_l4_fallback(self) -> "GrpoSmokeConfig":
        """Apply the one documented OOM retry, without changing the run target."""
        return replace(
            self,
            train_batch_size=2,
            max_response_length=256,
            rollout_gpu_memory_utilization=0.35,
        )

    def with_total_steps(self, total_steps: int) -> "GrpoSmokeConfig":
        # verl saves on its final iteration too, but making this explicit keeps
        # a two-step preflight's Storage publication deterministic and visible.
        config = replace(
            self,
            total_steps=total_steps,
            checkpoint_every=min(self.checkpoint_every, total_steps),
        )
        config.validate()
        return config

    def resolve_total_epochs(self, steps_per_epoch: int) -> int:
        """Return a safe epoch count for the actual prepared train input.

        verl's epoch loop can end before ``total_training_steps``. A null YAML
        value therefore derives a small safety margin after the minimum count;
        an explicit value remains an operator choice but must be large enough
        to reach the requested target.
        """
        if not isinstance(steps_per_epoch, int) or isinstance(steps_per_epoch, bool):
            raise ValueError("steps_per_epoch must be a positive integer")
        if steps_per_epoch < 1:
            raise ValueError("steps_per_epoch must be a positive integer")

        minimum = (self.total_steps + steps_per_epoch - 1) // steps_per_epoch
        if self.total_epochs is None:
            return minimum + TOTAL_EPOCHS_SAFETY_MARGIN
        if self.total_epochs < minimum:
            raise ValueError(
                "total_epochs would cap trainer.total_training_steps before its target: "
                f"total_epochs={self.total_epochs}, steps_per_epoch={steps_per_epoch}, "
                f"reachable_steps={self.total_epochs * steps_per_epoch}, "
                f"target_steps={self.total_steps}; use null or at least {minimum}"
            )
        return self.total_epochs

    def verl_overrides(
        self,
        *,
        train_file: Path,
        validation_file: Path,
        staging_dir: Path,
        reward_file: Path,
        job_id: str,
        resume_path: Path | None,
        steps_per_epoch: int,
    ) -> list[str]:
        """Build the explicit overrides for ``verl.trainer.main_ppo_sync``."""
        self.validate()
        total_epochs = self.resolve_total_epochs(steps_per_epoch)
        overrides = [
            "model_engine=dp",
            "algorithm.adv_estimator=grpo",
            "algorithm.use_kl_in_reward=False",
            f"data.train_files={train_file}",
            f"data.val_files={validation_file}",
            "data.prompt_key=prompt",
            "data.return_raw_chat=True",
            "data.shuffle=False",
            "data.seed=42",
            "data.filter_overlong_prompts=True",
            "data.truncation=error",
            f"data.train_batch_size={self.train_batch_size}",
            "data.val_batch_size=1",
            f"data.max_prompt_length={self.max_prompt_length}",
            f"data.max_response_length={self.max_response_length}",
            f"actor_rollout_ref.model.path={self.model_path}",
            # verl 0.8 defaults to FlashAttention2. The D2 pod deliberately
            # uses the proven torch runtime without that optional extension.
            "+actor_rollout_ref.model.override_config.attn_implementation=sdpa",
            "actor_rollout_ref.model.use_remove_padding=True",
            "actor_rollout_ref.model.enable_gradient_checkpointing=True",
            f"actor_rollout_ref.model.lora_rank={self.lora_rank}",
            f"actor_rollout_ref.model.lora_alpha={self.lora_alpha}",
            "actor_rollout_ref.model.target_modules=all-linear",
            "actor_rollout_ref.actor.strategy=fsdp",
            f"actor_rollout_ref.actor.optim.lr={self.learning_rate}",
            f"actor_rollout_ref.actor.ppo_mini_batch_size={self.train_batch_size}",
            "actor_rollout_ref.actor.ppo_micro_batch_size_per_gpu=1",
            "actor_rollout_ref.actor.use_dynamic_bsz=True",
            "actor_rollout_ref.actor.ppo_max_token_len_per_gpu=16384",
            "actor_rollout_ref.actor.use_kl_loss=True",
            f"actor_rollout_ref.actor.kl_loss_coef={self.kl_loss_coef}",
            "actor_rollout_ref.actor.kl_loss_type=low_var_kl",
            "actor_rollout_ref.actor.fsdp_config.param_offload=False",
            "actor_rollout_ref.actor.fsdp_config.optimizer_offload=False",
            f"actor_rollout_ref.actor.checkpoint.save_contents={self.checkpoint_save_contents}",
            "actor_rollout_ref.rollout.name=vllm",
            "actor_rollout_ref.rollout.tensor_model_parallel_size=1",
            f"actor_rollout_ref.rollout.gpu_memory_utilization={self.rollout_gpu_memory_utilization}",
            f"actor_rollout_ref.rollout.n={self.rollout_n}",
            "actor_rollout_ref.rollout.temperature=1.0",
            "actor_rollout_ref.rollout.top_p=1.0",
            "actor_rollout_ref.rollout.top_k=-1",
            "actor_rollout_ref.rollout.max_num_batched_tokens=8192",
            "actor_rollout_ref.rollout.enable_chunked_prefill=True",
            f"actor_rollout_ref.rollout.enforce_eager={str(self.enforce_eager).lower()}",
            "actor_rollout_ref.rollout.free_cache_engine=True",
            "actor_rollout_ref.rollout.load_format=auto",
            "actor_rollout_ref.rollout.val_kwargs.n=1",
            "actor_rollout_ref.rollout.val_kwargs.temperature=0",
            "actor_rollout_ref.rollout.val_kwargs.top_p=1.0",
            "actor_rollout_ref.rollout.val_kwargs.top_k=-1",
            "actor_rollout_ref.rollout.val_kwargs.do_sample=False",
            "actor_rollout_ref.ref.fsdp_config.param_offload=True",
            "actor_rollout_ref.ref.log_prob_use_dynamic_bsz=True",
            "actor_rollout_ref.ref.log_prob_max_token_len_per_gpu=16384",
            "reward.reward_manager.name=naive",
            "reward.num_workers=1",
            f"reward.custom_reward_function.path={reward_file}",
            f"reward.custom_reward_function.name={self.reward_function_name}",
            f"trainer.project_name=verifierforge_d2",
            f"trainer.experiment_name={job_id}",
            "trainer.logger=['console','file']",
            "trainer.nnodes=1",
            "trainer.n_gpus_per_node=1",
            f"trainer.total_training_steps={self.total_steps}",
            f"trainer.total_epochs={total_epochs}",
            "trainer.val_before_train=True",
            f"trainer.save_freq={self.checkpoint_every}",
            f"trainer.test_freq={self.validation_every}",
            f"trainer.default_local_dir={staging_dir}",
            "trainer.default_hdfs_dir=null",
        ]
        overrides.extend(_ray_diagnostic_environment_overrides())
        if self.vllm_attention_backend is not None:
            overrides.append(
                "+ray_kwargs.ray_init.runtime_env.env_vars."
                "VLLM_ATTENTION_BACKEND="
                f"{_hydra_string_literal(self.vllm_attention_backend)}"
            )
        if resume_path is None:
            overrides.append("trainer.resume_mode=disable")
        else:
            overrides.extend(("trainer.resume_mode=resume_path", f"trainer.resume_from_path={resume_path}"))
        return overrides
