"""Feature-flagged Forge Agent analysis and approval routes."""

from __future__ import annotations

from dataclasses import dataclass
import html
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent.mock_client import MockAgentClient
from app.agent.runner import (
    AgentGuardError,
    AgentPersistenceError,
    AgentRunError,
    ForgeAgentRunner,
)
from app.agent.stores import (
    AgentDecisionStore,
    AgentTraceStore,
    ApprovalStore,
    S3AgentTraceStore,
    SQLiteAgentDecisionStore,
    SQLiteApprovalStore,
)
from app.agent.tools import ToolRegistry
from app.gpt import LLMClient, LLMConfigurationError, LLMSettings
from app.proxy.traffic import DEFAULT_DB_PATH
from core.agent_contracts import (
    AgentAnalysisResponse,
    AgentDecisionType,
    ApprovalRecord,
    ApprovalRequest,
)


router = APIRouter()


class AgentAnalyzeRequest(BaseModel):
    """Optional UI provenance for the already configured traffic database."""

    model_config = ConfigDict(extra="forbid")

    data_source: str = Field(min_length=1, max_length=2048)


@dataclass(frozen=True)
class AgentStores:
    decisions: AgentDecisionStore
    approvals: ApprovalStore


@dataclass(frozen=True)
class AgentServices:
    registry: ToolRegistry
    client: object
    provider: str
    decisions: AgentDecisionStore
    approvals: ApprovalStore
    traces: AgentTraceStore


def agent_enabled() -> bool:
    return os.environ.get("VF_AGENT_ENABLED", "false").strip().lower() == "true"


def _configured_db_path() -> Path:
    return Path(
        os.environ.get("VF_PROXY_DB_PATH", str(DEFAULT_DB_PATH))
    ).expanduser()


def _configured_source_label() -> str:
    return os.environ.get("VF_PROXY_DB_PATH", "app/proxy/traffic.db")


def _stores() -> AgentStores:
    db_path = _configured_db_path()
    return AgentStores(
        decisions=SQLiteAgentDecisionStore(db_path),
        approvals=SQLiteApprovalStore(db_path),
    )


def _services(cluster_id: str) -> AgentServices:
    binding = os.environ.get("VF_AGENT_BINDING", "real").strip().lower()
    if binding not in {"real", "mock"}:
        raise RuntimeError("VF_AGENT_BINDING must be real or mock")
    db_path = _configured_db_path()
    stores = _stores()
    registry = ToolRegistry(binding, db_path=db_path)
    if binding == "mock":
        client: object = MockAgentClient(cluster_id)
        provider = "mock"
    else:
        if os.environ.get("VF_AGENT_GATE_C_PASSED", "false").strip().lower() != "true":
            raise RuntimeError("real Forge Agent binding requires a Gate C deployment receipt")
        settings = LLMSettings.from_env()
        client = LLMClient(settings)
        provider = settings.provider
    return AgentServices(
        registry=registry,
        client=client,
        provider=provider,
        decisions=stores.decisions,
        approvals=stores.approvals,
        traces=S3AgentTraceStore.from_env(),
    )


@router.post(
    "/clusters/{cluster_id}/agent/analyze", response_model=AgentAnalysisResponse
)
def analyze_cluster(
    cluster_id: str, request: AgentAnalyzeRequest | None = None
) -> AgentAnalysisResponse:
    _require_enabled()
    _validate_data_source(request)
    try:
        services = _services(cluster_id)
        analysis = services.registry.call("analyze_traffic", {"cluster_id": cluster_id})
        cached = services.decisions.latest_for_cluster(
            cluster_id, str(analysis["evidence_fingerprint"])
        )
        if cached is not None and cached.decision is not None:
            return _analysis_response(cached, cached=True)
        summary = ForgeAgentRunner(
            client=services.client,
            registry=services.registry,
            decision_store=services.decisions,
            trace_store=services.traces,
            provider=services.provider,
        ).run(cluster_id)
    except AgentGuardError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (
        AgentPersistenceError,
        LLMConfigurationError,
        OSError,
        ValueError,
        RuntimeError,
    ) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AgentRunError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if summary.decision is None:
        raise HTTPException(status_code=502, detail="Agent returned no decision")
    return _analysis_response(summary, cached=False)


@router.get(
    "/clusters/{cluster_id}/agent/decision", response_model=AgentAnalysisResponse
)
def latest_cluster_decision(cluster_id: str) -> AgentAnalysisResponse:
    _require_enabled()
    try:
        summary = _stores().decisions.latest_for_cluster(cluster_id)
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if summary is None or summary.decision is None:
        raise HTTPException(status_code=404, detail="Agent decision not found")
    return _analysis_response(summary, cached=True)


@router.post(
    "/agent-decisions/{decision_id}/approvals", response_model=ApprovalRecord
)
def approve_decision(decision_id: str, request: ApprovalRequest) -> ApprovalRecord:
    _require_enabled()
    try:
        stores = _stores()
        summary = stores.decisions.get(decision_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="Agent decision not found")
        if (
            summary.run_status.value != "completed"
            or summary.trace_s3_key is None
            or summary.decision is None
            or summary.decision.decision != AgentDecisionType.FORGE
        ):
            raise HTTPException(
                status_code=409,
                detail="Only audited forge decisions may be approved",
            )
        return stores.approvals.put(decision_id, request.approved_by)
    except HTTPException:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get(
    "/agent-decisions/{decision_id}/approval", response_model=ApprovalRecord
)
def get_approval(decision_id: str) -> ApprovalRecord:
    _require_enabled()
    try:
        approval = _stores().approvals.get_by_decision(decision_id)
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return approval


@router.get("/discover", response_class=HTMLResponse)
def discover_page() -> HTMLResponse:
    _require_enabled()
    page = Path(__file__).resolve().parents[1] / "web" / "discover.html"
    content = page.read_text(encoding="utf-8").replace(
        "__VF_DISCOVER_DATA_SOURCE__",
        html.escape(_configured_source_label(), quote=True),
    )
    return HTMLResponse(content)


def _require_enabled() -> None:
    if not agent_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _validate_data_source(request: AgentAnalyzeRequest | None) -> None:
    if request is None:
        return
    supplied = Path(request.data_source).expanduser().resolve()
    configured = _configured_db_path().resolve()
    if supplied != configured:
        raise HTTPException(
            status_code=422,
            detail="data_source must match the configured VF_PROXY_DB_PATH",
        )


def _analysis_response(summary, *, cached: bool) -> AgentAnalysisResponse:
    return AgentAnalysisResponse(
        decision_id=summary.decision_id,
        cluster_id=summary.cluster_id,
        decision=summary.decision,
        cached=cached,
        created_at=summary.created_at,
    )
