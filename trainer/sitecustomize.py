"""Enable optional D2 runtime compatibility hooks in spawned verl workers."""

from __future__ import annotations

import os


if os.environ.get("VF_VERL_TORCH_PADDING_FALLBACK") == "1":
    from trainer.flash_attn_compat import install_import_hook

    install_import_hook()
