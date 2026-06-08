"""Multi-agent layer: a roster of focused specialist sub-agents and a lead
orchestrator that decomposes a goal, runs the right specialists in parallel,
and synthesizes one answer.

Specialists reuse the existing tool-calling loop (``agent_loop.run_agent``) with
a focused system specialization + a tool subset — no separate agent framework.
"""
from app.services.agents.specialists import SPECIALISTS, get_specialist  # noqa: F401
