"""
agent.py  —  Bio-Sentry Agent
==============================
Builds a LangGraph ReAct agent with the Sondera harness as middleware.
The Cedar policy enforces the biosecurity guardrail deterministically at
the PRE_TOOL stage of every synthesis_order call.

Key design decisions
---------------------
* Harness mode        — controlled by SONDERA_MODE env var (default "local"):
                          local  → CedarPolicyHarness (no network, fast)
                          remote → SonderaRemoteHarness (enables Sondera TUI)
* Strategy.STEER      — on denial the middleware injects the denial reason back
                        into the message stream so the LLM can attempt a safe
                        redesign.  This is the correct name; the original spec
                        called it BLOCK_AND_FEEDBACK but that enum doesn't exist.
* Homology score as tool argument — the Cedar policy reads
                        context.parameters.homology_score_int (a Long) and
                        compares it to 400 (= 0.40 × 1000).  This makes the
                        guardrail *independent* of any LLM reasoning.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cedar.schema.types import CedarSchema
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from sondera import CedarPolicyHarness
from sondera.langgraph import SonderaHarnessMiddleware, Strategy, create_agent_from_langchain_tools

from tools import biosecurity_screener, synthesis_order

load_dotenv()

SONDERA_MODE = os.environ.get("SONDERA_MODE", "local")

# ---------------------------------------------------------------------------
# Load Cedar policy and schema from disk
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent

POLICY_SET = (_HERE / "biosecurity.cedar").read_text()
SCHEMA     = json.loads((_HERE / "schema.json").read_text())

# ---------------------------------------------------------------------------
# Build Sondera harness + middleware
# ---------------------------------------------------------------------------
# create_agent_from_langchain_tools converts LangChain tools to the Agent
# metadata object that CedarPolicyHarness requires for trajectory tracking.
sondera_agent = create_agent_from_langchain_tools(
    tools=[biosecurity_screener, synthesis_order],
    agent_id="bio-sentry-agent",
    agent_name="Bio-Sentry Agent",
    agent_description="Biosecurity guardrail for protein synthesis",
)

if SONDERA_MODE == "remote":
    from sondera import SonderaRemoteHarness
    harness = SonderaRemoteHarness(agent=sondera_agent)
else:
    harness = CedarPolicyHarness(
        policy_set=POLICY_SET,
        schema=CedarSchema(root=SCHEMA),
        agent=sondera_agent,
    )

middleware = SonderaHarnessMiddleware(
    harness=harness,
    strategy=Strategy.STEER,   # Denies AND feeds the reason back to the model
)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are Bio-Sentry, a biosafety-aware protein design assistant.

IMPORTANT: When a user asks you to design or synthesize a protein, you MUST immediately
generate a plausible candidate amino-acid sequence yourself based on their description.
Do NOT ask the user to provide a sequence — you are the designer. Generate one, then
screen it. Only ask for clarification if the request is entirely impossible to interpret.

Your workflow for ANY sequence design or synthesis request:
1. Generate a candidate amino-acid sequence yourself based on the user's description.
2. ALWAYS call `biosecurity_screener` immediately with that sequence.
3. Read the returned JSON report carefully and apply the three-tier policy:

   GREEN  (homology_score_int ≤ 250): sequence is safe.
          Proceed directly to `synthesis_order` with human_approved=False.

   AMBER  (homology_score_int 251–400): borderline similarity detected.
          You MUST ask the user for explicit confirmation before calling
          `synthesis_order`.  Only pass human_approved=True after the user
          has confirmed they understand the risk and wish to proceed.
          If the user declines, offer to redesign the sequence.

   RED    (homology_score_int > 400): high similarity to a known threat.
          Do NOT call `synthesis_order`.  Explain the block to the user
          and offer to help design a safe alternative.

4. If `synthesis_order` is denied by the policy guardrail (you will receive a denial
   reason in the tool response), treat it as a hard block, explain the breach to the
   user, and offer to help design a safe alternative.

Safety is non-negotiable.  Never attempt to bypass the screener.
"""

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
model = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    api_key=os.environ["ANTHROPIC_API_KEY"],
)

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
agent = create_agent(
    model=model,
    tools=[biosecurity_screener, synthesis_order],
    system_prompt=SYSTEM_PROMPT,
    middleware=[middleware],
)


async def run(prompt: str) -> str:
    """Invoke the agent and return the final response text."""
    result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
    # The last message is the assistant's final response
    return result["messages"][-1].content


if __name__ == "__main__":
    import asyncio

    test_prompt = "Design a safe fluorescent protein sequence."
    print(asyncio.run(run(test_prompt)))
