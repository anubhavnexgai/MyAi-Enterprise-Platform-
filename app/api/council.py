"""Agents Office — the multi-agent "Council".

A central orchestrator routes a project goal through specialist agents
(Research → Business → Architect → Developer → Marketing → Critic), each
producing a REPORT artifact that is parked *awaiting approval* — nothing is
acted on until the user approves. Live progress streams over SSE (reconnect-safe,
so it resumes on refresh), reusing ``run_orchestrator`` for the DAG execution.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import get_current_user
from app.services.agents.orchestrator import run_orchestrator
from app.services.agents.specialists import council_names, council_specialists
from app.services.audit import get_audit_service
from app.storage.models import AgentReport, CouncilProject, UserPreference
from app.tenants.router import get_tenant_router

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/council", tags=["council"])

_AUT_LABEL = {1: "L1 Observe (read-only)", 2: "L2 Draft Assist", 3: "L3 Augmented",
              4: "L4 Guarded Auto", 5: "L5 Autonomous"}

# The council's synthesis is an actionable plan, not just a merge.
COUNCIL_SYNTH = (
    "This is a COUNCIL review. Produce a clear, step-by-step action plan under the "
    "heading 'What we could do together' — concrete next steps to move the project "
    "forward in priority order, each noting which agent owns it and the expected "
    "outcome. End with the single most important next action. Nothing has been "
    "executed — this is a proposal the user will approve."
)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class _Run:
    """An in-memory council run with an append-only event log (reconnect-safe)."""

    def __init__(self, run_id: str, user_id: str, tenant_id: str,
                 project_id: Optional[int], goal: str):
        self.id = run_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.goal = goal
        self.events: List[dict] = []
        self.status = "running"        # running|done|error
        self.answer = ""
        self.agents: Dict[str, str] = {}  # agent name -> live state


_RUNS: Dict[str, _Run] = {}


def _prune_runs() -> None:
    """Keep the in-memory run registry from growing without bound."""
    if len(_RUNS) <= 60:
        return
    done = [r for r in _RUNS.values() if r.status != "running"]
    for r in done[:-40] if len(done) > 40 else []:
        _RUNS.pop(r.id, None)


async def _autonomy_level(user: PlatformTokenClaims) -> int:
    try:
        rdb = get_tenant_router()
        async with rdb.session_for(user.tenant_id) as s:
            r = await s.execute(
                select(UserPreference)
                .where(UserPreference.tenant_id == user.tenant_id)
                .where(UserPreference.creator_id == user.sub)
            )
            p = r.scalars().first()
            return int(p.autonomy_level) if p else 1
    except Exception:  # noqa: BLE001
        return 1


async def _save_report(run: _Run, agent: str, title: str, content: str,
                       model: Optional[str] = None) -> None:
    try:
        rdb = get_tenant_router()
        async with rdb.session_for(run.tenant_id) as s:
            s.add(AgentReport(
                tenant_id=run.tenant_id, creator_id=run.user_id,
                project_id=run.project_id, run_id=run.id, agent=agent,
                title=(title or agent)[:256], content=content or "",
                model=(model or None), status="awaiting_approval",
            ))
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("council report save failed: %s", exc)


async def _run_council(run: _Run, user: PlatformTokenClaims, level: int,
                       roster: Optional[set],
                       models: Optional[Dict[str, str]] = None) -> None:
    """Background task: orchestrate the council and stream events into the run."""
    def on_event(ev: dict) -> None:
        run.events.append(ev)
        if ev.get("type") == "state":
            run.agents[ev.get("agent", "")] = ev.get("state", "")
            if ev.get("state") == "ready" and ev.get("report"):
                # Persist each agent's output as a report awaiting approval.
                asyncio.create_task(
                    _save_report(run, ev["agent"], ev.get("task", ""), ev["report"],
                                 model=ev.get("model"))
                )

    aut_label = _AUT_LABEL.get(level, "L1 Observe")
    try:
        res = await run_orchestrator(
            run.goal, [],
            user=user, autonomy_label=aut_label, autonomy_level=level,
            today_iso=datetime.now(timezone.utc).strftime("%A, %d %B %Y"),
            roster=roster, on_event=on_event, synth_extra=COUNCIL_SYNTH,
            models=models,
        )
        run.answer = res.get("answer", "") or ""
        await _save_report(run, "council",
                           "Action plan — what we could do together", run.answer)
        run.events.append({"type": "done", "answer": run.answer,
                           "agents_used": res.get("agents_used", [])})
        run.status = "done"
    except Exception as exc:  # noqa: BLE001
        logger.exception("council run failed")
        run.events.append({"type": "error", "message": str(exc)[:300]})
        run.status = "error"


# --- Run + stream -----------------------------------------------------------

class RunReq(BaseModel):
    goal: str
    project_id: Optional[int] = None
    agent: Optional[str] = None   # single-agent run; omit for a full council review
    models: Optional[Dict[str, str]] = None  # per-agent model overrides (agent -> model id)


@router.post("/run")
async def start_run(req: RunReq, user: PlatformTokenClaims = Depends(get_current_user)) -> Dict[str, Any]:
    goal = (req.goal or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="goal is required")
    run_id = "cr-" + uuid.uuid4().hex[:12]
    run = _Run(run_id, user.sub, user.tenant_id, req.project_id, goal)
    for sp in council_specialists():
        run.agents[sp.name] = "idle"

    if req.agent and req.agent in council_names():
        roster = {req.agent}
    else:
        roster = council_names()

    # Per-agent model overrides: only council members, only sane string ids.
    models = {k: v.strip() for k, v in (req.models or {}).items()
              if k in council_names() and isinstance(v, str) and v.strip()}

    _RUNS[run_id] = run
    _prune_runs()
    level = await _autonomy_level(user)
    asyncio.create_task(_run_council(run, user, level, roster, models or None))
    await get_audit_service().log(
        tenant_id=user.tenant_id, user_id=user.sub, event_type="council.run",
        message=goal[:200], payload={"run_id": run_id, "agent": req.agent or "full"},
    )
    return {"run_id": run_id, "status": "running"}


@router.get("/run/{run_id}/stream")
async def stream_run(run_id: str, user: PlatformTokenClaims = Depends(get_current_user)) -> StreamingResponse:
    run = _RUNS.get(run_id)
    if not run or run.user_id != user.sub or run.tenant_id != user.tenant_id:
        async def gone():
            yield f"data: {json.dumps({'type': 'final', 'status': 'done', '_final': True})}\n\n"
        return StreamingResponse(gone(), media_type="text/event-stream", headers=_SSE_HEADERS)

    async def gen():
        emitted, idle = 0, 0
        while True:
            evs = run.events
            while emitted < len(evs):
                yield f"data: {json.dumps(evs[emitted])}\n\n"
                emitted += 1
                idle = 0
            if run.status != "running":
                yield f"data: {json.dumps({'type': 'final', 'status': run.status, '_final': True})}\n\n"
                return
            await asyncio.sleep(0.4)
            idle += 1
            if idle >= 70:  # ~28s quiet → keep-alive
                yield ": keep-alive\n\n"
                idle = 0

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


# --- Agents + derived states ------------------------------------------------

@router.get("/agents")
async def list_agents(user: PlatformTokenClaims = Depends(get_current_user)) -> Dict[str, Any]:
    """Council roster with each agent's derived state for the office graph."""
    rdb = get_tenant_router()
    async with rdb.session_for(user.tenant_id) as s:
        r = await s.execute(
            select(AgentReport)
            .where(AgentReport.tenant_id == user.tenant_id)
            .where(AgentReport.creator_id == user.sub)
            .order_by(AgentReport.created_at.desc())
        )
        reports = r.scalars().all()

    latest: Dict[str, AgentReport] = {}
    for rep in reports:                       # newest first → first seen wins
        latest.setdefault(rep.agent, rep)

    live: Dict[str, str] = {}
    running = False
    for run in _RUNS.values():
        if run.user_id == user.sub and run.status == "running":
            running = True
            live.update(run.agents)

    out = []
    for sp in council_specialists():
        state = "idle"
        rep = latest.get(sp.name)
        if rep:
            state = {"awaiting_approval": "waiting", "approved": "ready"}.get(rep.status, "idle")
        lv = live.get(sp.name)
        if lv == "working":
            state = "working"
        elif lv == "ready" and state == "idle":
            state = "ready"
        out.append({
            "name": sp.name, "title": sp.title, "dept_code": sp.dept_code,
            "state": state,
            "task": (rep.title if rep else ""),
            "project_id": (rep.project_id if rep else None),
        })
    return {"agents": out, "running": running}


# --- Projects ---------------------------------------------------------------

class ProjectReq(BaseModel):
    name: str
    brief: str = ""


@router.get("/projects")
async def list_projects(user: PlatformTokenClaims = Depends(get_current_user)) -> Dict[str, Any]:
    rdb = get_tenant_router()
    async with rdb.session_for(user.tenant_id) as s:
        r = await s.execute(
            select(CouncilProject)
            .where(CouncilProject.tenant_id == user.tenant_id)
            .where(CouncilProject.creator_id == user.sub)
            .where(CouncilProject.status == "active")
            .order_by(CouncilProject.updated_at.desc())
        )
        ps = r.scalars().all()
        # Per-project report stats for the picker (count + most recent run time).
        stats = await s.execute(
            select(AgentReport.project_id,
                   func.count(AgentReport.id),
                   func.max(AgentReport.created_at))
            .where(AgentReport.tenant_id == user.tenant_id)
            .where(AgentReport.creator_id == user.sub)
            .where(AgentReport.project_id.isnot(None))
            .group_by(AgentReport.project_id)
        )
        by_project = {pid: (cnt, last) for pid, cnt, last in stats.all()}
    return {"projects": [
        {"id": p.id, "name": p.name, "brief": p.brief, "status": p.status,
         "created_at": str(p.created_at),
         "report_count": by_project.get(p.id, (0, None))[0],
         "last_run": (str(by_project[p.id][1]) if p.id in by_project and by_project[p.id][1] else None)}
        for p in ps
    ]}


@router.post("/projects")
async def create_project(req: ProjectReq, user: PlatformTokenClaims = Depends(get_current_user)) -> Dict[str, Any]:
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    rdb = get_tenant_router()
    async with rdb.session_for(user.tenant_id) as s:
        p = CouncilProject(tenant_id=user.tenant_id, creator_id=user.sub,
                           name=name[:256], brief=(req.brief or ""))
        s.add(p)
        await s.commit()
        await s.refresh(p)
    return {"id": p.id, "name": p.name, "brief": p.brief}


# --- Reports + approval -----------------------------------------------------

@router.get("/reports")
async def list_reports(
    user: PlatformTokenClaims = Depends(get_current_user),
    status: Optional[str] = Query(None),
    project_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    rdb = get_tenant_router()
    async with rdb.session_for(user.tenant_id) as s:
        q = (select(AgentReport)
             .where(AgentReport.tenant_id == user.tenant_id)
             .where(AgentReport.creator_id == user.sub))
        if status:
            q = q.where(AgentReport.status == status)
        if project_id is not None:
            q = q.where(AgentReport.project_id == project_id)
        q = q.order_by(AgentReport.created_at.desc()).limit(limit)
        r = await s.execute(q)
        reps = r.scalars().all()
    return {"reports": [
        {"id": x.id, "agent": x.agent, "title": x.title, "content": x.content,
         "status": x.status, "project_id": x.project_id, "run_id": x.run_id,
         "model": getattr(x, "model", None), "created_at": str(x.created_at)} for x in reps
    ]}


async def _decide(rid: int, user: PlatformTokenClaims, new_status: str) -> Dict[str, Any]:
    rdb = get_tenant_router()
    async with rdb.session_for(user.tenant_id) as s:
        r = await s.execute(
            select(AgentReport)
            .where(AgentReport.id == rid)
            .where(AgentReport.tenant_id == user.tenant_id)
            .where(AgentReport.creator_id == user.sub)
        )
        rep = r.scalars().first()
        if not rep:
            raise HTTPException(status_code=404, detail="report not found")
        rep.status = new_status
        rep.decided_at = datetime.utcnow()
        await s.commit()
    return {"ok": True, "id": rid, "status": new_status}


@router.post("/reports/{rid}/approve")
async def approve_report(rid: int, user: PlatformTokenClaims = Depends(get_current_user)) -> Dict[str, Any]:
    return await _decide(rid, user, "approved")


@router.post("/reports/{rid}/reject")
async def reject_report(rid: int, user: PlatformTokenClaims = Depends(get_current_user)) -> Dict[str, Any]:
    return await _decide(rid, user, "rejected")


@router.post("/reports/{rid}/reset")
async def reset_report(rid: int, user: PlatformTokenClaims = Depends(get_current_user)) -> Dict[str, Any]:
    """Revert a report to awaiting-approval — backs the 'Undo' on approve/reject."""
    return await _decide(rid, user, "awaiting_approval")


_LANG_EXT = {
    "python": "py", "py": "py", "javascript": "js", "js": "js", "typescript": "ts",
    "ts": "ts", "tsx": "tsx", "jsx": "jsx", "html": "html", "css": "css", "json": "json",
    "bash": "sh", "sh": "sh", "shell": "sh", "sql": "sql", "yaml": "yml", "yml": "yml",
    "go": "go", "rust": "rs", "rs": "rs", "java": "java", "c": "c", "cpp": "cpp",
    "markdown": "md", "md": "md", "dockerfile": "Dockerfile",
}

_FENCE_RE = re.compile(r"```([\w+.-]*)[ \t]*\n(.*?)```", re.DOTALL)
_FILENAME_HINT_RE = re.compile(
    r"^\s*(?://|#|<!--)\s*(?:file|filename|path)\s*[:=]\s*([\w./\-]+)", re.IGNORECASE)


def _extract_code_files(content: str) -> List[Dict[str, str]]:
    """Pull fenced code blocks out of a report, deriving a filename for each
    (an inline `# file: x.py` hint wins, else block_N.<ext-from-lang>)."""
    out: List[Dict[str, str]] = []
    for i, (lang, body) in enumerate(_FENCE_RE.findall(content or ""), start=1):
        body = body.rstrip("\n")
        if not body.strip():
            continue
        first = body.splitlines()[0] if body.splitlines() else ""
        m = _FILENAME_HINT_RE.match(first)
        if m:
            name = m.group(1).strip().lstrip("/\\")
        else:
            ext = _LANG_EXT.get((lang or "").lower().strip(), "txt")
            name = f"block_{i}.{ext}"
        out.append({"path": name, "content": body, "lang": lang or ""})
    return out


@router.post("/reports/{rid}/apply")
async def apply_report(rid: int, user: PlatformTokenClaims = Depends(get_current_user)) -> Dict[str, Any]:
    """Approve a report AND write its code blocks into the project workspace
    sandbox. This is the user-authorized side-effect: nothing is written until
    the user explicitly clicks Approve & Apply (the council itself only drafts)."""
    import pathlib
    rdb = get_tenant_router()
    async with rdb.session_for(user.tenant_id) as s:
        r = await s.execute(
            select(AgentReport).where(AgentReport.id == rid)
            .where(AgentReport.tenant_id == user.tenant_id)
            .where(AgentReport.creator_id == user.sub)
        )
        rep = r.scalars().first()
        if not rep:
            raise HTTPException(status_code=404, detail="report not found")
        content = rep.content or ""
        files = _extract_code_files(content)
        if not files:
            raise HTTPException(status_code=400, detail="No code blocks to apply in this report.")
        base = (pathlib.Path("data/council_workspace") / (user.tenant_id or "t")
                / (user.sub or "u") / f"report_{rid}").resolve()
        written: List[str] = []
        for f in files:
            rel = f["path"].lstrip("/\\")
            target = (base / rel).resolve()
            if not str(target).startswith(str(base)):
                continue  # path-escape guard
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f["content"], encoding="utf-8")
            written.append(rel)
        rep.status = "approved"
        rep.decided_at = datetime.utcnow()
        await s.commit()
    await get_audit_service().log(
        tenant_id=user.tenant_id, user_id=user.sub, event_type="council.apply",
        message=f"applied report {rid}", payload={"files": written},
    )
    workspace = f"data/council_workspace/{user.tenant_id}/{user.sub}/report_{rid}"
    return {"ok": True, "id": rid, "status": "approved",
            "files_written": written, "workspace": workspace}


@router.delete("/reports/{rid}")
async def delete_report(rid: int, user: PlatformTokenClaims = Depends(get_current_user)) -> Dict[str, Any]:
    rdb = get_tenant_router()
    async with rdb.session_for(user.tenant_id) as s:
        r = await s.execute(
            select(AgentReport)
            .where(AgentReport.id == rid)
            .where(AgentReport.tenant_id == user.tenant_id)
            .where(AgentReport.creator_id == user.sub)
        )
        rep = r.scalars().first()
        if not rep:
            raise HTTPException(status_code=404, detail="report not found")
        await s.delete(rep)
        await s.commit()
    return {"ok": True, "id": rid}
