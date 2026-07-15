from __future__ import annotations

import pytest

from trainer import flash_attn_compat


torch = pytest.importorskip("torch")

from trainer.flash_attn_compat import index_first_axis, pad_input, rearrange, unpad_input


def test_unpad_and_pad_match_flash_attention_layout_contract() -> None:
    hidden_states = torch.arange(24, dtype=torch.float32).reshape(2, 4, 3).requires_grad_()
    attention_mask = torch.tensor([[0, 1, 1, 0], [1, 1, 1, 0]])

    unpadded, indices, cumulative_lengths, max_sequence_length, used_lengths = unpad_input(
        hidden_states, attention_mask
    )

    assert indices.tolist() == [1, 2, 4, 5, 6]
    assert cumulative_lengths.tolist() == [0, 2, 5]
    assert max_sequence_length == 3
    assert used_lengths.tolist() == [2, 3]
    assert torch.equal(unpadded, hidden_states.flatten(0, 1)[indices])

    restored = pad_input(unpadded, indices, batch=2, seqlen=4)
    assert torch.equal(restored[attention_mask.bool()], hidden_states[attention_mask.bool()])
    assert torch.equal(restored[~attention_mask.bool()], torch.zeros_like(restored[~attention_mask.bool()]))

    restored.sum().backward()
    assert hidden_states.grad is not None


def test_index_and_rearrange_cover_verl_helper_patterns() -> None:
    values = torch.arange(12).reshape(4, 3)
    assert torch.equal(index_first_axis(values, torch.tensor([3, 1])), values[[3, 1]])

    batched = torch.arange(12).reshape(2, 2, 3)
    flattened = rearrange(batched, "b s ... -> (b s) ...")
    assert torch.equal(flattened, batched.flatten(0, 1))
    assert torch.equal(rearrange(flattened, "(b s) ... -> b s ...", b=2), batched)


def test_lazy_resolver_uses_fallback_only_after_flash_attention_is_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    fallback = (object(), object(), object(), object())
    monkeypatch.setattr(flash_attn_compat, "_transformers_padding_functions", lambda: fallback)

    class AttentionUtils:
        calls = 0

        @staticmethod
        def _get_attention_functions() -> tuple[object, ...]:
            AttentionUtils.calls += 1
            raise ModuleNotFoundError("missing flash_attn", name="flash_attn")

    module = AttentionUtils()
    assert flash_attn_compat.install_verl_padding_fallback(module)
    assert AttentionUtils.calls == 0
    assert module._get_attention_functions() is fallback
    assert module._get_attention_functions() is fallback
    assert AttentionUtils.calls == 1
    assert not flash_attn_compat.install_verl_padding_fallback(module)
