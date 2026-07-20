from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from core.serving_contracts import ServingState, ServingStatus, ServingWakeRequest


def test_serving_wake_request_is_strict_and_requires_spend_confirmation() -> None:
    request = ServingWakeRequest(confirm_provider_spend=True)
    assert request.model_id == "vf-demo"

    with pytest.raises(ValidationError):
        ServingWakeRequest(confirm_provider_spend=False)
    with pytest.raises(ValidationError):
        ServingWakeRequest(confirm_provider_spend=True, unexpected=True)


def test_serving_status_round_trip() -> None:
    status = ServingStatus(
        session_id="serve-01",
        model_id="vf-demo",
        state=ServingState.READY,
        url="https://example.trycloudflare.com/v1",
        detail="ready",
        gpu_model="NVIDIA L4",
        hourly_price_usd=0.39,
        cost_accrued_usd=0.02,
        cold_start_seconds=87.5,
        updated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    restored = ServingStatus.model_validate_json(status.model_dump_json())
    assert restored == status


@pytest.mark.parametrize(
    ("state", "session_id", "url"),
    [
        (ServingState.READY, "serve-01", None),
        (ServingState.LOADING, "serve-01", "https://example.test/v1"),
        (ServingState.PROVISIONING, None, None),
    ],
)
def test_serving_status_rejects_invalid_state_shapes(
    state: ServingState, session_id: str | None, url: str | None
) -> None:
    with pytest.raises(ValidationError):
        ServingStatus(
            session_id=session_id,
            model_id="vf-demo",
            state=state,
            url=url,
        )
