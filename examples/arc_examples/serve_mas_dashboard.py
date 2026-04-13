#!/usr/bin/env python3
"""Dashboard server for MAS ARC-AGI-3 agent.
Supports multiple runs under mas_logs/{run_id}/{game_id}/.
Serves on port 7889.
"""

import json
from pathlib import Path
from flask import Flask, jsonify, send_file, request

app = Flask(__name__)
BASE = Path(__file__).parent.parent.parent
LOG_ROOT = BASE / "mas_logs"


def _get_run_dir(run_id: str | None = None) -> Path:
    """Get the run directory. If no run_id, use the most recent."""
    if not LOG_ROOT.exists():
        return LOG_ROOT / "empty"
    if run_id:
        return LOG_ROOT / run_id
    # Find most recent run dir (by mtime)
    runs = sorted(
        [d for d in LOG_ROOT.iterdir() if d.is_dir()],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    return runs[0] if runs else LOG_ROOT / "empty"


@app.route("/")
def index():
    return send_file(str(Path(__file__).parent / "mas_dashboard.html"))


@app.route("/api/runs")
def api_runs():
    """List all run IDs."""
    if not LOG_ROOT.exists():
        return jsonify([])
    runs = []
    for d in sorted(LOG_ROOT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if d.is_dir():
            # Count games in this run
            game_count = sum(1 for g in d.iterdir() if g.is_dir())
            runs.append({"id": d.name, "games": game_count})
    return jsonify(runs)


@app.route("/api/games")
def api_games():
    """List all games for a run. Use ?run=ID or defaults to latest."""
    run_dir = _get_run_dir(request.args.get("run"))
    if not run_dir.exists():
        return jsonify([])

    games = []
    for d in sorted(run_dir.iterdir()):
        if not d.is_dir():
            continue
        game = {"game_id": d.name, "name": d.name.split("-")[0]}

        actions_file = d / "actions.jsonl"
        if actions_file.exists():
            actions = []
            with open(actions_file) as f:
                for line in f:
                    if line.strip():
                        actions.append(json.loads(line))
            if actions:
                last = actions[-1]
                game["total_actions"] = last.get("count", len(actions))
                game["levels_completed"] = last.get("level", 0)
                game["win_levels"] = last.get("win_levels", 0)
                game["state"] = last.get("state", "UNKNOWN")
            else:
                game["total_actions"] = 0
                game["levels_completed"] = 0

        trace_file = d / "agent_trace.jsonl"
        if trace_file.exists():
            agents_spawned = 0
            total_input = 0
            total_output = 0
            total_cache_read = 0
            total_cache_write = 0
            with open(trace_file) as f:
                for line in f:
                    if line.strip():
                        ev = json.loads(line)
                        if ev.get("event") == "spawn":
                            agents_spawned += 1
                        if ev.get("event") in ("finish", "call_finish", "orchestrator_finish"):
                            total_input += ev.get("input_tokens", 0)
                            total_output += ev.get("output_tokens", 0)
                            total_cache_read += ev.get("cache_read_tokens", 0)
                            total_cache_write += ev.get("cache_write_tokens", 0)
            game["agents_spawned"] = agents_spawned
            game["input_tokens"] = total_input
            game["output_tokens"] = total_output
            game["cache_read_tokens"] = total_cache_read
            game["cache_write_tokens"] = total_cache_write
            game["total_tokens"] = total_input + total_output
            # Opus 4.6 pricing: $5/M input, $25/M output, cache read $0.5/M, cache write $6.25/M
            uncached_input = total_input - total_cache_read
            cost = (uncached_input * 5 + total_cache_read * 0.5 + total_cache_write * 6.25 + total_output * 25) / 1_000_000
            game["cost_usd"] = round(cost, 4)

        games.append(game)
    return jsonify(games)


@app.route("/api/actions/<game_id>")
def api_actions(game_id):
    run_dir = _get_run_dir(request.args.get("run"))
    path = run_dir / game_id / "actions.jsonl"
    if not path.exists():
        return jsonify([])
    actions = []
    with open(path) as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                if not request.args.get("full"):
                    entry.pop("grid", None)
                actions.append(entry)
    return jsonify(actions)


@app.route("/api/trace/<game_id>")
def api_trace(game_id):
    run_dir = _get_run_dir(request.args.get("run"))
    path = run_dir / game_id / "agent_trace.jsonl"
    if not path.exists():
        return jsonify([])
    events = []
    with open(path) as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return jsonify(events)


@app.route("/api/knowledge/<game_id>")
def api_knowledge(game_id):
    run_dir = _get_run_dir(request.args.get("run"))
    game_dir = run_dir / game_id

    # Try final knowledge dump first
    final_path = game_dir / "knowledge.json"
    if final_path.exists():
        with open(final_path) as f:
            return jsonify(json.load(f))

    # Try live wiki file (written by GameWiki._flush_to_disk during gameplay)
    wiki_path = game_dir / f"{game_id}.json"
    if wiki_path.exists():
        with open(wiki_path) as f:
            data = json.load(f)
        # Normalize to same format as knowledge.json
        pages = {}
        for name, info in data.get("pages", {}).items():
            pages[name] = info.get("content", "") if isinstance(info, dict) else info
        return jsonify({
            "type": "wiki",
            "game_id": game_id,
            "pages": pages,
            "history": data.get("history", []),
        })

    return jsonify({"type": "none"})


if __name__ == "__main__":
    print(f"Serving MAS dashboard on http://0.0.0.0:7889")
    print(f"Log root: {LOG_ROOT}")
    app.run(host="0.0.0.0", port=7889, debug=False)
