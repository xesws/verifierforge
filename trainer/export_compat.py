"""Convert PEFT-wrapped verl Hugging Face exports into serveable HF weights.

The D4 trainer exports a complete model state while its LoRA wrappers are still
present.  vLLM expects ordinary Qwen parameter names, so the converter merges
the captured low-rank deltas into the captured base-layer tensors without
touching the original resume/evaluation export.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import shutil
import uuid


SOURCE_EXPORT_DIRECTORY = "huggingface"
SERVEABLE_EXPORT_DIRECTORY = "serveable_huggingface"
SERVEABLE_MANIFEST = "verifierforge-serveable.json"
_PREFIX = "base_model.model."


class ExportCompatibilityError(RuntimeError):
    """A checkpoint export cannot be safely converted into a serveable model."""


@dataclass(frozen=True)
class ExportInspection:
    """The evidence used to select a conversion path."""

    layout: str
    source: Path
    files: tuple[str, ...]
    first_keys: tuple[str, ...]
    base_layer_keys: int
    lora_a_keys: int
    lora_b_keys: int
    other_keys: int


@dataclass(frozen=True)
class ConversionResult:
    """The atomically published serveable representation of one source export."""

    source: Path
    destination: Path
    layout: str
    lora_rank: int
    lora_alpha: int
    source_index_sha256: str
    already_present: bool

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["source"] = str(self.source)
        payload["destination"] = str(self.destination)
        return payload


def source_export_path(native_checkpoint: Path) -> Path:
    """Return the immutable raw HF export beneath a native verl checkpoint."""
    return Path(native_checkpoint) / "actor" / SOURCE_EXPORT_DIRECTORY


def serveable_export_path(native_checkpoint: Path) -> Path:
    """Return the sibling standard-HF export used only for serving/evaluation."""
    return Path(native_checkpoint) / "actor" / SERVEABLE_EXPORT_DIRECTORY


def classify_weight_keys(keys: Iterable[str], *, has_adapter_config: bool) -> str:
    """Classify source weights without guessing a converter from filenames alone."""
    names = tuple(keys)
    has_lora_a = any(".lora_A." in key for key in names)
    has_lora_b = any(".lora_B." in key for key in names)
    has_base_layer = any(".base_layer." in key for key in names)
    has_prefix = any(key.startswith(_PREFIX) for key in names)

    if has_adapter_config and has_lora_a and has_lora_b and not has_base_layer:
        return "pure_adapter"
    # A damaged full export can have only one half of a LoRA pair.  Keep it on
    # this path so conversion reports the actionable incomplete-pair error
    # instead of misclassifying it as an unrelated layout.
    if has_prefix and has_base_layer and (has_lora_a or has_lora_b):
        return "prefixed_full"
    return "other"


def inspect_export(source: Path) -> ExportInspection:
    """Read source layout evidence without writing into the checkpoint tree."""
    source = Path(source)
    shards = _safetensors_files(source)
    safe_open = _safe_open()
    keys: list[str] = []
    for shard in shards:
        with safe_open(shard, framework="pt", device="cpu") as handle:
            keys.extend(handle.keys())

    base_layer_keys = sum(".base_layer." in key for key in keys)
    lora_a_keys = sum(".lora_A." in key for key in keys)
    lora_b_keys = sum(".lora_B." in key for key in keys)
    other_keys = len(keys) - base_layer_keys - lora_a_keys - lora_b_keys
    files = tuple(sorted(path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()))
    return ExportInspection(
        layout=classify_weight_keys(keys, has_adapter_config=(source / "adapter_config.json").is_file()),
        source=source,
        files=files,
        first_keys=tuple(sorted(keys)[:10]),
        base_layer_keys=base_layer_keys,
        lora_a_keys=lora_a_keys,
        lora_b_keys=lora_b_keys,
        other_keys=other_keys,
    )


def standard_weight_key(source_key: str) -> str | None:
    """Map a PEFT-wrapper state key to the ordinary Qwen state-dict key.

    ``None`` means the key is a LoRA factor that is consumed while merging its
    matching ``base_layer.weight`` tensor rather than copied to the output.
    """
    if not source_key.startswith(_PREFIX):
        raise ExportCompatibilityError(f"unexpected source weight key without {_PREFIX!r}: {source_key}")
    key = source_key.removeprefix(_PREFIX)
    if ".lora_A." in key or ".lora_B." in key:
        return None
    return key.replace(".base_layer.", ".")


def convert_prefixed_full_export(
    source: Path,
    destination: Path,
    *,
    lora_rank: int,
    lora_alpha: int,
) -> ConversionResult:
    """Atomically merge a complete PEFT-wrapped source into normal bf16 HF files.

    The source already carries every base tensor, so no hub access or base-model
    download is needed.  Each safetensors shard is processed independently to
    bound peak memory.  The original directory is never opened for writing.
    """
    source = Path(source)
    destination = Path(destination)
    if lora_rank < 1 or lora_alpha < 1:
        raise ValueError("lora_rank and lora_alpha must be positive")

    inspection = inspect_export(source)
    if inspection.layout != "prefixed_full":
        raise ExportCompatibilityError(
            f"expected prefixed_full export, found {inspection.layout!r} at {source}"
        )
    source_index = source / "model.safetensors.index.json"
    if not source_index.is_file():
        raise ExportCompatibilityError(f"missing safetensors index: {source_index}")
    source_index_sha256 = sha256_file(source_index)

    if destination.exists():
        if is_serveable_export(destination):
            manifest = _read_manifest(destination)
            if manifest.get("source_index_sha256") == source_index_sha256:
                return ConversionResult(
                    source=source,
                    destination=destination,
                    layout=inspection.layout,
                    lora_rank=lora_rank,
                    lora_alpha=lora_alpha,
                    source_index_sha256=source_index_sha256,
                    already_present=True,
                )
        raise ExportCompatibilityError(f"refusing to overwrite non-matching serveable export: {destination}")

    temporary = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir(parents=True)
    try:
        _copy_support_files(source, temporary)
        weight_map, total_size = _convert_shards(
            source,
            temporary,
            lora_rank=lora_rank,
            lora_alpha=lora_alpha,
        )
        _write_json_atomic(
            temporary / "model.safetensors.index.json",
            {"metadata": {"total_size": total_size}, "weight_map": weight_map},
        )
        result = ConversionResult(
            source=source,
            destination=destination,
            layout=inspection.layout,
            lora_rank=lora_rank,
            lora_alpha=lora_alpha,
            source_index_sha256=source_index_sha256,
            already_present=False,
        )
        _write_json_atomic(temporary / SERVEABLE_MANIFEST, result.as_dict())
        os.rename(temporary, destination)
        return result
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def is_serveable_export(path: Path) -> bool:
    """Recognize an atomically completed converted directory, not a raw export."""
    path = Path(path)
    return (
        path.is_dir()
        and (path / "config.json").is_file()
        and (path / "model.safetensors.index.json").is_file()
        and (path / SERVEABLE_MANIFEST).is_file()
        and bool(list(path.glob("*.safetensors")))
    )


def sha256_file(path: Path) -> str:
    """Return the stable SHA-256 identity of one file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def convert_job_exports(
    runs_root: Path,
    job_id: str,
    *,
    lora_rank: int,
    lora_alpha: int,
    evidence_name: str = "export-compat-v0127",
) -> list[ConversionResult]:
    """Convert every numeric Storage checkpoint for one completed training job.

    The summary is atomic and records partial success if a later checkpoint
    fails.  Earlier converted siblings remain valid evidence and source exports
    remain untouched in either case.
    """
    if Path(evidence_name).name != evidence_name or not evidence_name:
        raise ValueError("evidence_name must be one job-local directory name")
    run_dir = Path(runs_root) / job_id
    wrappers: list[tuple[int, Path]] = []
    for candidate in (run_dir / "ckpt").glob("step_*"):
        try:
            step = int(candidate.name.removeprefix("step_"))
        except ValueError:
            continue
        native = candidate / f"global_step_{step}"
        if native.is_dir():
            wrappers.append((step, native))
    wrappers.sort()
    if not wrappers:
        raise ExportCompatibilityError(f"no published checkpoints under {run_dir / 'ckpt'}")

    evidence = run_dir / "evidence" / evidence_name / "conversion-summary.json"
    results: list[ConversionResult] = []
    try:
        for _step, native in wrappers:
            results.append(
                convert_prefixed_full_export(
                    source_export_path(native),
                    serveable_export_path(native),
                    lora_rank=lora_rank,
                    lora_alpha=lora_alpha,
                )
            )
    except Exception as error:
        _write_json_atomic(
            evidence,
            {
                "status": "failed",
                "job_id": job_id,
                "completed": [result.as_dict() for result in results],
                "error": f"{type(error).__name__}: {error}",
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise
    _write_json_atomic(
        evidence,
        {
            "status": "completed",
            "job_id": job_id,
            "checkpoints": [result.as_dict() for result in results],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return results


def _convert_shards(
    source: Path,
    destination: Path,
    *,
    lora_rank: int,
    lora_alpha: int,
) -> tuple[dict[str, str], int]:
    torch, load_file, save_file = _torch_safetensors()
    weight_map: dict[str, str] = {}
    total_size = 0
    for source_shard in _safetensors_files(source):
        state = load_file(str(source_shard), device="cpu")
        converted = _merge_shard(state, torch=torch, lora_rank=lora_rank, lora_alpha=lora_alpha)
        for key, tensor in converted.items():
            if key in weight_map:
                raise ExportCompatibilityError(f"duplicate converted weight key: {key}")
            weight_map[key] = source_shard.name
            total_size += tensor.numel() * tensor.element_size()
        save_file(converted, str(destination / source_shard.name), metadata={"format": "pt"})
        del state
        del converted
    return weight_map, total_size


def _merge_shard(
    state: Mapping[str, object],
    *,
    torch: object,
    lora_rank: int,
    lora_alpha: int,
) -> dict[str, object]:
    """Merge all LoRA pairs in one source shard and return standard tensor names."""
    converted: dict[str, object] = {}
    consumed_lora: set[str] = set()
    scale = lora_alpha / lora_rank
    for source_key, value in state.items():
        output_key = standard_weight_key(source_key)
        if output_key is None:
            continue
        if output_key in converted:
            raise ExportCompatibilityError(f"duplicate target weight key: {output_key}")
        tensor = value
        if source_key.endswith(".base_layer.weight"):
            stem = source_key.removesuffix(".base_layer.weight")
            a_key = f"{stem}.lora_A.default.weight"
            b_key = f"{stem}.lora_B.default.weight"
            has_a = a_key in state
            has_b = b_key in state
            if has_a != has_b:
                raise ExportCompatibilityError(f"incomplete LoRA pair for {source_key}")
            if has_a:
                lora_a = state[a_key]
                lora_b = state[b_key]
                if lora_a.shape[0] != lora_rank or lora_b.shape[1] != lora_rank:
                    raise ExportCompatibilityError(
                        f"LoRA rank mismatch for {source_key}: "
                        f"A={tuple(lora_a.shape)}, B={tuple(lora_b.shape)}, expected={lora_rank}"
                    )
                delta = torch.matmul(lora_b.float(), lora_a.float()) * scale
                if tuple(delta.shape) != tuple(tensor.shape):
                    raise ExportCompatibilityError(
                        f"LoRA/base shape mismatch for {source_key}: "
                        f"base={tuple(tensor.shape)}, delta={tuple(delta.shape)}"
                    )
                tensor = tensor.float().add(delta).to(dtype=torch.bfloat16)
                consumed_lora.update((a_key, b_key))
        if not getattr(tensor, "is_floating_point")():
            raise ExportCompatibilityError(f"non-floating model tensor is unsupported: {source_key}")
        converted[output_key] = tensor.to(dtype=torch.bfloat16).contiguous()

    all_lora = {key for key in state if ".lora_A." in key or ".lora_B." in key}
    unconsumed = sorted(all_lora - consumed_lora)
    if unconsumed:
        raise ExportCompatibilityError(
            "LoRA weights do not have a matching base_layer weight in the same shard: "
            + ", ".join(unconsumed[:3])
        )
    return converted


def _copy_support_files(source: Path, destination: Path) -> None:
    for path in source.iterdir():
        if not path.is_file() or path.suffix == ".safetensors" or path.name == "model.safetensors.index.json":
            continue
        shutil.copy2(path, destination / path.name)


def _safetensors_files(source: Path) -> list[Path]:
    files = sorted(Path(source).glob("*.safetensors"))
    if not files:
        raise ExportCompatibilityError(f"no safetensors files in {source}")
    return files


def _safe_open():
    try:
        from safetensors import safe_open
    except ModuleNotFoundError as error:  # pragma: no cover - pod dependency
        raise ExportCompatibilityError("export inspection requires safetensors") from error
    return safe_open


def _torch_safetensors():
    try:
        import torch
        from safetensors.torch import load_file, save_file
    except ModuleNotFoundError as error:  # pragma: no cover - pod dependency
        raise ExportCompatibilityError("export conversion requires torch and safetensors") from error
    return torch, load_file, save_file


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        value = json.loads((Path(path) / SERVEABLE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExportCompatibilityError(f"cannot read serveable manifest under {path}") from error
    if not isinstance(value, dict):
        raise ExportCompatibilityError(f"serveable manifest is not an object: {path}")
    return value


def _write_json_atomic(path: Path, payload: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    """Provide the explicit pod-only X2 batch conversion entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--runs-dir", default=os.environ.get("VF_RUNS_DIR", "./runs"))
    parser.add_argument("--lora-rank", type=int, required=True)
    parser.add_argument("--lora-alpha", type=int, required=True)
    parser.add_argument("--evidence-name", default="export-compat-v0127")
    args = parser.parse_args()
    results = convert_job_exports(
        Path(args.runs_dir),
        args.job,
        lora_rank=args.lora_rank,
        lora_alpha=args.lora_alpha,
        evidence_name=args.evidence_name,
    )
    print(json.dumps({"converted_steps": [int(result.destination.parents[2].name.removeprefix("step_")) for result in results]}))


if __name__ == "__main__":
    main()
