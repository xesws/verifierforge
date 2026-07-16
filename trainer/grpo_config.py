"""The small, inspectable D2 GRPO configuration and verl command overrides."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path


CONFIG_DIRECTORY = Path(__file__).with_name("verl_configs")


@dataclass(frozen=True)
class GrpoSmokeConfig:
    """Values intentionally constrained for the one-L4 D2 smoke run."""

    model_path: str
    total_steps: int
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
        if self.learning_rate <= 0 or self.kl_loss_coef < 0:
            raise ValueError("learning_rate must be positive and kl_loss_coef non-negative")
        if not 0 < self.rollout_gpu_memory_utilization <= 1:
            raise ValueError("rollout_gpu_memory_utilization must be in (0, 1]")
        if not isinstance(self.enforce_eager, bool):
            raise ValueError("enforce_eager must be a boolean")
        if self.vllm_attention_backend not in (None, "TORCH_SDPA"):
            raise ValueError("vllm_attention_backend must be null or TORCH_SDPA")

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

    def verl_overrides(
        self,
        *,
        train_file: Path,
        validation_file: Path,
        staging_dir: Path,
        reward_file: Path,
        job_id: str,
        resume_path: Path | None,
    ) -> list[str]:
        """Build the explicit overrides for ``verl.trainer.main_ppo_sync``."""
        self.validate()
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
            "actor_rollout_ref.actor.checkpoint.save_contents=['model','optimizer','extra']",
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
            "reward.custom_reward_function.name=compute_score",
            f"trainer.project_name=verifierforge_d2",
            f"trainer.experiment_name={job_id}",
            "trainer.logger=['console','file']",
            "trainer.nnodes=1",
            "trainer.n_gpus_per_node=1",
            f"trainer.total_training_steps={self.total_steps}",
            "trainer.total_epochs=10",
            "trainer.val_before_train=True",
            f"trainer.save_freq={self.checkpoint_every}",
            f"trainer.test_freq={self.validation_every}",
            f"trainer.default_local_dir={staging_dir}",
            "trainer.default_hdfs_dir=null",
        ]
        if self.vllm_attention_backend is not None:
            overrides.append(
                "+ray_kwargs.ray_init.runtime_env.env_vars."
                f"VLLM_ATTENTION_BACKEND={self.vllm_attention_backend}"
            )
        if resume_path is None:
            overrides.append("trainer.resume_mode=disable")
        else:
            overrides.extend(("trainer.resume_mode=resume_path", f"trainer.resume_from_path={resume_path}"))
        return overrides
