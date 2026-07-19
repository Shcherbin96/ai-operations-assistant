"""FastAPI surface over :class:`OpsService`.

Thin by design: the endpoints translate HTTP to service calls and map typed domain
errors to status codes. All the logic lives in the service and the layers beneath.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ops_assistant import __version__
from ops_assistant.errors import ErrorCode, OpsAssistantError
from ops_assistant.observability import compute_metrics
from ops_assistant.service import OpsService, WorkflowView

_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.APPROVAL_NOT_FOUND: 404,
    ErrorCode.APPROVAL_EXPIRED: 409,
    ErrorCode.APPROVAL_ALREADY_DECIDED: 409,
    ErrorCode.PLAN_CHANGED: 409,
    ErrorCode.DUPLICATE_EXECUTION: 409,
    ErrorCode.CONFLICT: 409,
    ErrorCode.STATE_TRANSITION: 409,
    ErrorCode.TOOL_EXECUTION: 502,
}


class SubmitBody(BaseModel):
    text: str
    user: str
    source: str = "api"


class DecisionBody(BaseModel):
    actor: str
    reason: str | None = None


def create_app(service: OpsService | None = None) -> FastAPI:
    svc = service or OpsService()
    app = FastAPI(
        title="AI Operations Assistant",
        version=__version__,
        description=(
            "The model proposes the plan; the server decides what runs. "
            "Submit a plain-language request, the server re-derives each step's risk, "
            "auto-runs read-only work, and gates every external side-effect behind a "
            "human approval — with an append-only audit trail. Interactive docs below."
        ),
    )

    @app.exception_handler(OpsAssistantError)
    async def _handle_domain_error(_: Request, exc: OpsAssistantError) -> JSONResponse:
        status = _STATUS_BY_CODE.get(exc.code, 400)
        return JSONResponse(
            status_code=status, content={"code": exc.code.value, "message": exc.message}
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics() -> dict[str, object]:
        return compute_metrics(svc.all_audit())

    @app.post("/requests", status_code=201, response_model=WorkflowView)
    def submit(body: SubmitBody) -> WorkflowView:
        return svc.submit(text=body.text, user=body.user, source=body.source)

    @app.get("/workflows/{workflow_id}", response_model=WorkflowView)
    def get_workflow(workflow_id: str) -> WorkflowView:
        return svc.get(workflow_id)

    @app.post(
        "/workflows/{workflow_id}/approvals/{approval_id}/approve", response_model=WorkflowView
    )
    def approve(workflow_id: str, approval_id: str, body: DecisionBody) -> WorkflowView:
        return svc.approve(workflow_id, approval_id, actor=body.actor, reason=body.reason)

    @app.post(
        "/workflows/{workflow_id}/approvals/{approval_id}/reject", response_model=WorkflowView
    )
    def reject(workflow_id: str, approval_id: str, body: DecisionBody) -> WorkflowView:
        return svc.reject(workflow_id, approval_id, actor=body.actor, reason=body.reason)

    @app.get("/workflows/{workflow_id}/audit")
    def audit(workflow_id: str) -> list[dict[str, object]]:
        svc.get(workflow_id)  # 404 if unknown
        return [e.model_dump(mode="json") for e in svc.audit_for(workflow_id)]

    return app


app = create_app()
