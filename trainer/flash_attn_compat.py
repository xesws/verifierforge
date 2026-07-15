"""Torch-only replacements for verl's FlashAttention padding helpers.

verl 0.8 imports ``flash_attn.bert_padding`` for data layout even when the
model's attention implementation is PyTorch SDPA.  These helpers implement
only that layout contract; they never provide or advertise FlashAttention
kernels.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def index_first_axis(input_tensor: Any, indices: Any) -> Any:
    """Select entries from a flattened first axis using PyTorch autograd ops."""
    return input_tensor.index_select(0, indices)


def unpad_input(hidden_states: Any, attention_mask: Any, unused_mask: Any = None) -> tuple[Any, Any, Any, int, Any]:
    """Match ``flash_attn.bert_padding.unpad_input`` without CUDA kernels."""
    import torch
    import torch.nn.functional as functional

    all_masks = attention_mask if unused_mask is None else attention_mask + unused_mask
    sequence_lengths = all_masks.sum(dim=-1, dtype=torch.int32)
    used_sequence_lengths = attention_mask.sum(dim=-1, dtype=torch.int32)
    indices = torch.nonzero(all_masks.flatten(), as_tuple=False).flatten()
    max_sequence_length = sequence_lengths.max().item()
    cumulative_lengths = functional.pad(torch.cumsum(sequence_lengths, dim=0, dtype=torch.int32), (1, 0))
    flattened_states = hidden_states.flatten(0, 1)
    return (
        index_first_axis(flattened_states, indices),
        indices,
        cumulative_lengths,
        max_sequence_length,
        used_sequence_lengths,
    )


def pad_input(hidden_states: Any, indices: Any, batch: int, seqlen: int) -> Any:
    """Restore an unpadded tensor to ``(batch, seqlen, ...)`` with zeros."""
    output = hidden_states.new_zeros((batch * seqlen, *hidden_states.shape[1:]))
    output = output.index_copy(0, indices, hidden_states)
    return output.reshape(batch, seqlen, *hidden_states.shape[1:])


def rearrange(input_tensor: Any, pattern: str, **axes_lengths: int) -> Any:
    """Support the two reshape patterns exposed by FlashAttention's helpers."""
    if pattern == "b s ... -> (b s) ...":
        return input_tensor.flatten(0, 1)
    if pattern == "(b s) ... -> b s ...":
        batch = axes_lengths["b"]
        return input_tensor.reshape(batch, input_tensor.shape[0] // batch, *input_tensor.shape[1:])
    raise ValueError(f"unsupported FlashAttention compatibility rearrange pattern: {pattern!r}")


def _transformers_padding_functions() -> tuple[Callable[..., Any], ...]:
    """Use Transformers' matching helpers when the pinned version exposes them."""
    try:
        from einops import rearrange as einops_rearrange
        from transformers.modeling_flash_attention_utils import _index_first_axis, _pad_input, _unpad_input
    except ImportError:
        return (index_first_axis, pad_input, rearrange, unpad_input)
    return (_index_first_axis, _pad_input, einops_rearrange, _unpad_input)


def install_verl_padding_fallback() -> bool:
    """Install the layout-only fallback when FlashAttention is unavailable.

    The function replaces only verl's dynamic helper resolver.  vLLM retains
    its normal backend detection and never sees a pretend ``flash_attn`` module.
    """
    import verl.utils.attention_utils as attention_utils

    try:
        attention_utils._get_attention_functions()
    except ModuleNotFoundError as error:
        if error.name != "flash_attn":
            raise
    else:
        return False

    functions = _transformers_padding_functions()
    attention_utils._get_attention_functions = lambda: functions
    return True
