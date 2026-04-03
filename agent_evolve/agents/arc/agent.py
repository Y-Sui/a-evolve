"""ARC-AGI-3 agent -- plays interactive games via the arc-agi toolkit.

This agent wraps the ARC-AGI-3 arcade environment and uses an LLM to
decide actions based on game observations. The LLM sees the game grid
state and available actions, then chooses the next move.

The agent can operate in two modes:
1. **Tool mode** (default): LLM uses strands tools (observe_game, take_action,
   reset_level) to interact with the game environment step by step.
2. **Direct mode**: Agent runs a tight observation-action loop, formatting
   game state as text for the LLM at each step.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from strands import Agent
from strands.models import BedrockModel

from ...protocol.base_agent import BaseAgent
from ...types import Task, Trajectory

logger = logging.getLogger(__name__)

os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

# Map action names to GameAction enum values
ACTION_MAP = {
    "ACTION1": 1, "ACTION2": 2, "ACTION3": 3, "ACTION4": 4,
    "ACTION5": 5, "ACTION6": 6, "ACTION7": 7, "RESET": 0,
}


class ArcAgent(BaseAgent):
    """Evolvable agent for ARC-AGI-3 interactive games.

    Uses an LLM to observe game states and choose actions, playing
    through game levels with the goal of maximum efficiency (RHAE).
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        model_id: str = "us.anthropic.claude-opus-4-6-v1",
        region: str = "us-west-2",
        max_tokens: int = 8192,
        max_actions: int = 5000,
    ):
        super().__init__(workspace_dir)
        self.model_id = model_id
        self.region = region
        self.max_tokens = max_tokens
        self.max_actions = max_actions

    def solve(self, task: Task) -> Trajectory:
        """Play an ARC-AGI-3 game and return the trajectory.

        The task metadata should contain:
          - game_id: str (ARC-AGI-3 game identifier)
          - max_actions: int (action budget, default from self.max_actions)
          - api_key: str (optional ARC API key)
          - operation_mode: str (optional, default "normal")
        """
        game_id = task.metadata.get("game_id", task.id)
        max_actions = task.metadata.get("max_actions", self.max_actions)

        logger.info("Playing ARC-AGI-3 game: %s (budget: %d actions)", game_id, max_actions)

        try:
            return self._solve_with_tools(task, game_id, max_actions)
        except ImportError:
            logger.error("arc-agi package not installed. Install with: pip install arc-agi")
            return Trajectory(
                task_id=task.id,
                output=json.dumps({
                    "game_id": game_id,
                    "error": "arc-agi package not installed",
                    "game_completed": False,
                    "levels_completed": 0,
                    "total_levels": 0,
                    "total_actions": 0,
                    "score": 0.0,
                }),
                steps=[{"error": "arc-agi not installed"}],
            )

    def _solve_with_tools(self, task: Task, game_id: str, max_actions: int) -> Trajectory:
        """Play the game using strands tools for LLM-driven interaction."""
        import arc_agi
        from arcengine import GameAction

        # Initialize arcade
        arcade_kwargs: dict[str, Any] = {}
        api_key = task.metadata.get("api_key")
        if api_key:
            arcade_kwargs["arc_api_key"] = api_key

        op_mode = task.metadata.get("operation_mode", "normal")
        if op_mode != "normal":
            from arc_agi import OperationMode
            arcade_kwargs["operation_mode"] = getattr(OperationMode, op_mode.upper())

        arcade = arc_agi.Arcade(**arcade_kwargs)
        env = arcade.make(game_id, render_mode=None)

        # Game state tracked across tool calls
        game_state: dict[str, Any] = {
            "observation": None,
            "done": False,
            "total_actions": 0,
            "levels_completed": 0,
            "total_levels": 0,
            "per_level_actions": [],
            "current_level_actions": 0,
            "last_reward": 0.0,
            "last_info": {},
        }
        action_trace: list[dict] = []

        # Reset environment
        obs = env.reset()
        game_state["observation"] = self._format_observation(obs)

        # Build strands tools for game interaction
        from strands import tool

        @tool
        def observe_game() -> str:
            """Get the current game state.

            Returns the current observation grid and game status.
            Call this to see what the game looks like before deciding your action.
            """
            if game_state["done"]:
                return "Game is over. No more actions needed."
            return (
                f"Observation:\n{game_state['observation']}\n\n"
                f"Actions taken: {game_state['total_actions']}/{max_actions}\n"
                f"Levels completed: {game_state['levels_completed']}\n"
                f"Last reward: {game_state['last_reward']}"
            )

        @tool
        def take_action(action: str, x: int = -1, y: int = -1) -> str:
            """Take an action in the game.

            Args:
                action: One of ACTION1, ACTION2, ACTION3, ACTION4, ACTION5, ACTION6, ACTION7, RESET.
                    ACTION1-4 are directional (up/down/left/right).
                    ACTION5 is context-dependent (interact/select).
                    ACTION6 is coordinate-based (requires x, y).
                    ACTION7 is undo.
                    RESET restarts the current level.
                x: X coordinate for ACTION6 (0-63). Only used with ACTION6.
                y: Y coordinate for ACTION6 (0-63). Only used with ACTION6.
            """
            if game_state["done"]:
                return "Game is already over."

            if game_state["total_actions"] >= max_actions:
                game_state["done"] = True
                return f"Action budget exhausted ({max_actions} actions). Game over."

            action_upper = action.upper().strip()
            if action_upper not in ACTION_MAP:
                return f"Invalid action: {action}. Use one of: {', '.join(ACTION_MAP.keys())}"

            # Map to GameAction
            action_val = ACTION_MAP[action_upper]
            if action_upper == "ACTION6" and x >= 0 and y >= 0:
                # ACTION6 with coordinates -- encode as needed by arc-agi
                game_action = GameAction(action_val)
            else:
                game_action = GameAction(action_val)

            # Step the environment
            try:
                obs, reward, done, info = env.step(game_action)
            except Exception as e:
                return f"Error executing action: {e}"

            game_state["total_actions"] += 1
            game_state["current_level_actions"] += 1
            game_state["last_reward"] = reward
            game_state["last_info"] = info if isinstance(info, dict) else {}
            game_state["observation"] = self._format_observation(obs)

            # Track level transitions
            if reward > 0:
                game_state["levels_completed"] += 1
                game_state["per_level_actions"].append(
                    game_state["current_level_actions"]
                )
                game_state["current_level_actions"] = 0

            if done:
                game_state["done"] = True
                game_state["per_level_actions"].append(
                    game_state["current_level_actions"]
                )

            # Record action in trace
            action_trace.append({
                "type": "action",
                "action": action_upper,
                "x": x if action_upper == "ACTION6" else None,
                "y": y if action_upper == "ACTION6" else None,
                "reward": reward,
                "done": done,
                "actions_so_far": game_state["total_actions"],
                "level_changed": reward > 0,
            })

            status = "LEVEL COMPLETE!" if reward > 0 else ""
            if done:
                status = "GAME COMPLETE!" if game_state["levels_completed"] > 0 else "GAME OVER"

            return (
                f"Action: {action_upper} -> reward={reward}"
                f"{' ' + status if status else ''}\n\n"
                f"New observation:\n{game_state['observation']}\n"
                f"Actions: {game_state['total_actions']}/{max_actions} | "
                f"Levels: {game_state['levels_completed']}"
            )

        @tool
        def reset_level() -> str:
            """Reset the current level to start over.

            Use this if you're stuck or want to try a different approach.
            """
            if game_state["done"]:
                return "Game is already over."

            try:
                obs = env.reset()
                game_state["observation"] = self._format_observation(obs)
                game_state["current_level_actions"] = 0
                action_trace.append({
                    "type": "action",
                    "action": "RESET",
                    "reward": 0,
                    "done": False,
                    "actions_so_far": game_state["total_actions"],
                })
                return f"Level reset.\n\nObservation:\n{game_state['observation']}"
            except Exception as e:
                return f"Error resetting: {e}"

        # Build the strands agent
        model = BedrockModel(
            model_id=self.model_id,
            region_name=self.region,
            max_tokens=self.max_tokens,
        )

        system_prompt = self._build_system_prompt()
        tools = [observe_game, take_action, reset_level]

        # Add read_skill tool if skills exist
        if self.skills:
            skill_data = {}
            for skill in self.skills:
                content = self.get_skill_content(skill.name)
                if content:
                    body = content.split("---", 2)[-1].strip() if "---" in content else content
                    skill_data[skill.name] = body

            @tool
            def read_skill(skill_name: str) -> str:
                """Read a skill's full procedure. Call when a skill's description matches your situation.

                Args:
                    skill_name: Name of the skill to read
                """
                if skill_name in skill_data:
                    return skill_data[skill_name]
                return f"Skill '{skill_name}' not found. Available: {', '.join(skill_data.keys())}"

            tools.append(read_skill)

        agent = Agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
        )

        # Play the game
        user_prompt = self._build_user_prompt(task)
        t0 = time.time()

        try:
            response = agent(user_prompt)
        except Exception as e:
            logger.error("Agent error playing %s: %s", game_id, e)
            response = None

        elapsed = time.time() - t0
        logger.info("Game %s finished in %.1fs: %d actions, %d levels",
                     game_id, elapsed, game_state["total_actions"],
                     game_state["levels_completed"])

        # Extract usage
        usage = {}
        if response:
            try:
                u = response.metrics.accumulated_usage
                usage = {
                    "input_tokens": u.get("inputTokens", 0),
                    "output_tokens": u.get("outputTokens", 0),
                    "total_tokens": u.get("totalTokens", 0),
                }
            except Exception:
                pass

        # Compute score
        score = self._compute_score(game_state)

        # Build result
        result = {
            "game_id": game_id,
            "game_completed": game_state["done"] and game_state["levels_completed"] > 0,
            "levels_completed": game_state["levels_completed"],
            "total_levels": game_state.get("total_levels", game_state["levels_completed"]),
            "total_actions": game_state["total_actions"],
            "per_level_actions": game_state["per_level_actions"],
            "score": score,
            "elapsed_sec": elapsed,
            "usage": usage,
        }

        # Add summary step
        action_trace.append({
            "type": "summary",
            "llm_output": str(response)[:2000] if response else "(error)",
            "usage": usage,
            "score": score,
            "levels_completed": game_state["levels_completed"],
            "total_actions": game_state["total_actions"],
            "game_completed": result["game_completed"],
            "per_level_actions": game_state["per_level_actions"],
        })

        self.remember(
            f"Played {game_id}: completed={result['game_completed']}, "
            f"levels={game_state['levels_completed']}, "
            f"actions={game_state['total_actions']}, score={score:.3f}",
            category="episodic",
            task_id=game_id,
        )

        traj = Trajectory(
            task_id=task.id,
            output=json.dumps(result),
            steps=action_trace,
        )

        # Generate skill proposal
        if response:
            skill_proposal = self._generate_skill_proposal(agent, game_id)
            traj._skill_proposal = skill_proposal

        return traj

    # ── Observation formatting ───────────────────────────────────────

    @staticmethod
    def _format_observation(obs: Any) -> str:
        """Format a game observation into text the LLM can understand."""
        if obs is None:
            return "(no observation)"

        # Handle different observation formats
        if isinstance(obs, str):
            return obs

        if isinstance(obs, dict):
            # Grid-based observation
            grid = obs.get("grid", obs.get("state", obs.get("frame")))
            if grid and isinstance(grid, list):
                lines = []
                for row in grid:
                    if isinstance(row, list):
                        lines.append(" ".join(str(cell) for cell in row))
                    else:
                        lines.append(str(row))
                return "\n".join(lines)
            return json.dumps(obs, indent=2)[:3000]

        if isinstance(obs, list):
            # Could be a list of frames
            if obs and isinstance(obs[0], dict):
                # Take the last frame
                return ArcAgent._format_observation(obs[-1])
            # Direct grid
            lines = []
            for row in obs:
                if isinstance(row, list):
                    lines.append(" ".join(str(cell) for cell in row))
                else:
                    lines.append(str(row))
            return "\n".join(lines)

        return str(obs)[:3000]

    # ── Score computation ────────────────────────────────────────────

    @staticmethod
    def _compute_score(game_state: dict) -> float:
        """Compute a 0-1 score based on game completion and efficiency.

        Uses a simplified RHAE-inspired scoring:
        - Base score from fraction of levels completed
        - Efficiency bonus for completing levels in fewer actions
        """
        levels = game_state.get("levels_completed", 0)
        total_actions = game_state.get("total_actions", 0)

        if levels == 0:
            return 0.0

        # Base: fraction of completion (assume we don't know total levels)
        base_score = min(1.0, levels / max(1, levels))  # 1.0 if any levels

        # Efficiency: penalize excessive actions per level
        # Rough heuristic: 50 actions per level is "efficient"
        avg_actions = total_actions / levels if levels > 0 else total_actions
        efficiency = max(0.0, 1.0 - (avg_actions - 50) / 200)
        efficiency = min(1.0, max(0.1, efficiency))

        return base_score * efficiency

    # ── Prompt construction ──────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """Assemble the full system prompt from workspace files."""
        parts = [self.system_prompt]

        # Include evolved prompt fragments
        fragments = self.workspace.list_fragments()
        if fragments:
            for frag_name in fragments:
                content = self.workspace.read_fragment(frag_name)
                if content and content.strip():
                    marker = f"<!-- evolve:{frag_name.removesuffix('.md')} -->"
                    if marker not in self.system_prompt:
                        parts.append(f"\n\n## {frag_name.removesuffix('.md').replace('_', ' ').title()}")
                        parts.append(content)

        # Skills section
        parts.append("\n\n## Skills\n")
        if self.skills:
            parts.append(
                "You have skills learned from previous games. Call `read_skill(skill_name)` "
                "to load any that match your situation.\n"
            )
            for skill in self.skills:
                parts.append(f"- **{skill.name}**: {skill.description}")
        else:
            parts.append("No skills available yet. They will be learned through evolution.\n")

        return "\n".join(parts)

    def _build_user_prompt(self, task: Task) -> str:
        """Build the user prompt for a game task."""
        game_id = task.metadata.get("game_id", task.id)
        max_actions = task.metadata.get("max_actions", self.max_actions)

        memory_section = ""
        if self.memories:
            relevant = [m for m in self.memories if m.get("task_id") == game_id]
            if relevant:
                memory_section = "\n\n## Previous Attempts\n"
                for mem in relevant[-5:]:
                    memory_section += f"- {mem.get('content', '')}\n"
                memory_section += "\nLearn from these and try a different strategy.\n"

        return f"""\
{task.input}

Action budget: {max_actions} actions
{memory_section}
Start by calling observe_game() to see the initial state, then use take_action()
to play. Think carefully about each move -- efficiency matters!
"""

    # ── Skill proposals ──────────────────────────────────────────────

    def _generate_skill_proposal(self, agent: Agent, game_id: str) -> str:
        """Ask the agent to propose a reusable skill after playing."""
        try:
            skill_context = ""
            if self.skills:
                skill_list = "\n".join(f"- {s.name}: {s.description}" for s in self.skills)
                skill_context = f"You had these skills available:\n{skill_list}\n\n"

            proposal_response = agent(
                f"{skill_context}"
                "Based on the game you just played, propose a reusable skill "
                "that could help in future ARC-AGI-3 games.\n\n"
                "RULES:\n"
                "- NAME must be GENERIC (e.g., navigate_maze, pattern_matching, "
                "explore_then_exploit)\n"
                "- DESCRIPTION must include TRIGGER and DO NOT TRIGGER conditions\n\n"
                "OPTION A -- ENHANCE existing skill:\n"
                "ACTION: ENHANCE\nTARGET: skill_name\n"
                "NAME: same_name\nDESCRIPTION: one sentence\nCONTENT: (under 500 words)\n\n"
                "OPTION B -- NEW skill:\n"
                "ACTION: NEW\nNAME: pattern_name\n"
                "DESCRIPTION: one sentence\nCONTENT: (under 500 words)\n\n"
                "OPTION C -- No proposal:\nACTION: NONE"
            )
            return str(proposal_response).strip()[:2500]
        except Exception as e:
            logger.warning("Skill proposal failed for %s: %s", game_id, e)
            return ""
