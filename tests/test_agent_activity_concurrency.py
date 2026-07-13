import json
import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import agent_activity as aa  # noqa: E402


def _set_agent_activity(activity_path: str, agent: str) -> None:
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import agent_activity as worker_aa

    worker_aa.set(
        agent=agent,
        model="gpt-5",
        effort="low",
        task="Concurrent update",
        reason="Lock regression test",
        path=Path(activity_path),
    )


def test_parallel_set_calls_preserve_all_entries(tmp_path):
    activity_file = tmp_path / "agent_activity.json"
    aa.write_activity(aa.seed_activity_data(), activity_file)

    agents = ["codex", "codex-2", "agy", "agy-w1", "claude-w1"]
    with ProcessPoolExecutor(
        max_workers=len(agents),
        mp_context=multiprocessing.get_context("spawn"),
    ) as pool:
        futures = [
            pool.submit(_set_agent_activity, str(activity_file), agent)
            for agent in agents
        ]
        for future in futures:
            future.result(timeout=30)

    payload = json.loads(activity_file.read_text(encoding="utf-8"))
    entries = {entry["agent"]: entry for entry in payload["entries"]}
    for agent in agents:
        entry = entries[agent]
        assert entry["status"] == "running"
        assert entry["current_task"] == "Concurrent update"
        assert entry["reason"] == "Lock regression test"
