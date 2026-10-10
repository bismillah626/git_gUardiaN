"""Concurrency regression tests for specialist-agent orchestration."""

import asyncio

from app.agents import supervisor
from app.models.schemas import AgentResult


def test_all_agents_publish_running_before_slow_agent_completes(monkeypatch):
    """Test that all agents get 'running' status and run concurrently.
    
    The slow agent should not block other agents from starting and completing.
    """
    events = []

    def record_status(review_id, agent_name, status, message):
        events.append((status, agent_name))

    async def slow_agent():
        await asyncio.sleep(0.02)
        return AgentResult(agent_name="security")

    def quick_agent(name):
        async def run():
            return AgentResult(agent_name=name)
        return run

    monkeypatch.setattr(supervisor, "update_agent_status", record_status)

    async def run_agents():
        await asyncio.gather(
            supervisor._run_single_agent(slow_agent, "security_result", "security", 1),
            supervisor._run_single_agent(quick_agent("quality"), "quality_result", "quality", 1),
            supervisor._run_single_agent(quick_agent("test_gap"), "test_gap_result", "test_gap", 1),
            supervisor._run_single_agent(
                quick_agent("documentation"),
                "documentation_result",
                "documentation",
                1,
            ),
        )

    asyncio.run(run_agents())

    # All agents should get "running" status
    running_events = [e for e in events if e[0] == "running"]
    assert len(running_events) == 4
    running_agents = {e[1] for e in running_events}
    assert running_agents == {"security", "quality", "test_gap", "documentation"}

    # All quick agents should complete before the slow agent completes
    done_events = [e for e in events if e[0] == "done"]
    done_agents = {e[1] for e in done_events}
    assert "quality" in done_agents
    assert "test_gap" in done_agents
    assert "documentation" in done_agents

    # Security should be the last to complete (it's the slow one)
    last_done = [e for e in events if e[0] == "done"][-1]
    assert last_done[1] == "security"
