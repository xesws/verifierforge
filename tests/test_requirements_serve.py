from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_serving_requirements_lock_the_verified_vllm_tokenizer_stack():
    lines = (REPOSITORY_ROOT / "requirements-serve.txt").read_text(encoding="utf-8").splitlines()
    requirements = {line for line in lines if line and not line.startswith("#")}

    assert {
        "vllm==0.10.2",
        "torch==2.8.0",
        "transformers==4.57.6",
        "tokenizers==0.22.2",
        "huggingface_hub==0.36.2",
    } <= requirements
    assert all(">" not in requirement and "<" not in requirement for requirement in requirements)
