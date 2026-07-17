from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from trainer.export_compat import (
    ExportCompatibilityError,
    classify_weight_keys,
    convert_job_exports,
    convert_prefixed_full_export,
    inspect_export,
    is_serveable_export,
    serveable_export_path,
)


def _write_prefixed_export(root: Path, *, complete_pair: bool = True) -> Path:
    torch = pytest.importorskip("torch")
    save_file = pytest.importorskip("safetensors.torch").save_file
    source = root / "actor" / "huggingface"
    source.mkdir(parents=True)
    state = {
        "base_model.model.model.embed_tokens.weight": torch.tensor(
            [[1.0, 2.0], [3.0, 4.0]], dtype=torch.bfloat16
        ),
        "base_model.model.model.layers.0.mlp.down_proj.base_layer.weight": torch.tensor(
            [[1.0, 2.0], [3.0, 4.0]], dtype=torch.bfloat16
        ),
        "base_model.model.model.layers.0.mlp.down_proj.lora_A.default.weight": torch.tensor(
            [[1.0, 2.0]], dtype=torch.bfloat16
        ),
        "base_model.model.model.layers.0.mlp.down_proj.base_layer.bias": torch.tensor(
            [5.0, 6.0], dtype=torch.bfloat16
        ),
        "base_model.model.lm_head.weight": torch.tensor([[7.0, 8.0]], dtype=torch.bfloat16),
    }
    if complete_pair:
        state["base_model.model.model.layers.0.mlp.down_proj.lora_B.default.weight"] = torch.tensor(
            [[3.0], [4.0]], dtype=torch.bfloat16
        )
    save_file(state, str(source / "model-00001-of-00001.safetensors"), metadata={"format": "pt"})
    index = {key: "model-00001-of-00001.safetensors" for key in state}
    (source / "model.safetensors.index.json").write_text(
        json.dumps({"metadata": {"total_size": 1}, "weight_map": index}), encoding="utf-8"
    )
    (source / "config.json").write_text('{"model_type":"qwen2"}', encoding="utf-8")
    (source / "tokenizer.json").write_text("{}", encoding="utf-8")
    return source


def test_classify_weight_keys_distinguishes_current_export_shapes() -> None:
    assert classify_weight_keys(
        (
            "base_model.model.model.layers.0.mlp.up_proj.lora_A.default.weight",
            "base_model.model.model.layers.0.mlp.up_proj.lora_B.default.weight",
        ),
        has_adapter_config=True,
    ) == "pure_adapter"
    assert classify_weight_keys(
        (
            "base_model.model.model.layers.0.mlp.up_proj.base_layer.weight",
            "base_model.model.model.layers.0.mlp.up_proj.lora_A.default.weight",
            "base_model.model.model.layers.0.mlp.up_proj.lora_B.default.weight",
        ),
        has_adapter_config=False,
    ) == "prefixed_full"
    assert classify_weight_keys(("model.layers.0.weight",), has_adapter_config=False) == "other"


def test_converter_merges_lora_without_mutating_source_and_is_idempotent(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    load_file = pytest.importorskip("safetensors.torch").load_file
    source = _write_prefixed_export(tmp_path)
    original = {
        path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source.rglob("*")
        if path.is_file()
    }

    inspection = inspect_export(source)
    assert inspection.layout == "prefixed_full"
    assert inspection.base_layer_keys == 2
    assert inspection.lora_a_keys == inspection.lora_b_keys == 1
    assert inspection.first_keys[0] == "base_model.model.lm_head.weight"

    destination = serveable_export_path(tmp_path)
    result = convert_prefixed_full_export(source, destination, lora_rank=1, lora_alpha=2)

    assert result.already_present is False
    assert is_serveable_export(destination)
    assert original == {
        path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source.rglob("*")
        if path.is_file()
    }
    converted = load_file(str(destination / "model-00001-of-00001.safetensors"), device="cpu")
    assert set(converted) == {
        "lm_head.weight",
        "model.embed_tokens.weight",
        "model.layers.0.mlp.down_proj.bias",
        "model.layers.0.mlp.down_proj.weight",
    }
    assert torch.equal(
        converted["model.layers.0.mlp.down_proj.weight"],
        torch.tensor([[7.0, 14.0], [11.0, 20.0]], dtype=torch.bfloat16),
    )
    manifest = json.loads((destination / "verifierforge-serveable.json").read_text(encoding="utf-8"))
    assert manifest["lora_rank"] == 1
    assert manifest["lora_alpha"] == 2
    assert convert_prefixed_full_export(source, destination, lora_rank=1, lora_alpha=2).already_present


def test_converter_rejects_incomplete_lora_pair_without_publishing_destination(tmp_path: Path) -> None:
    source = _write_prefixed_export(tmp_path, complete_pair=False)
    destination = serveable_export_path(tmp_path)

    with pytest.raises(ExportCompatibilityError, match="incomplete LoRA pair"):
        convert_prefixed_full_export(source, destination, lora_rank=1, lora_alpha=2)

    assert not destination.exists()
    assert (source / "model-00001-of-00001.safetensors").is_file()


def test_batch_converter_writes_an_atomic_job_summary(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    for step in (50, 100):
        _write_prefixed_export(runs / "m3" / "ckpt" / f"step_{step}" / f"global_step_{step}")

    results = convert_job_exports(runs, "m3", lora_rank=1, lora_alpha=2)

    assert len(results) == 2
    summary = json.loads(
        (runs / "m3" / "evidence" / "export-compat-v0127" / "conversion-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["status"] == "completed"
    assert [Path(item["destination"]).parents[2].name for item in summary["checkpoints"]] == [
        "step_50",
        "step_100",
    ]
