window.MMOI_ANALYTICS = {
  "_meta": {
    "schema": 2,
    "generated_at": "2026-06-23T09:02:01Z",
    "source_files": {
      "tasks": "tasks/active_tasks.json",
      "ptme_decisions": "logs/ptme_decisions.jsonl",
      "activity": "dashboard/agent_activity.json",
      "live_tasks": "dashboard/live_tasks.json",
      "lessons": "BKM/AGENT_LESSONS.md",
      "usage_logs": []
    }
  },
  "sources": {
    "tasks_total": 0,
    "task_status_counts": {},
    "task_complexity_counts": {},
    "tasks_with_complexity": 0,
    "tasks_missing_complexity": 0,
    "activity_entries": 14,
    "running_agents": 0,
    "live_task_count": 0,
    "ptme_decision_count": 0,
    "usage_log_file_count": 0,
    "usage_log_files": [],
    "lessons_count": 0
  },
  "runtime": {
    "tasks": [],
    "agents": []
  },
  "live_tasks": {
    "empty_state": "no live tasks recorded yet",
    "rows": []
  },
  "decisions": {
    "empty_state": "no PTME decisions logged yet",
    "rows": [],
    "summary": {
      "logged_count": 0,
      "accepted_count": 0,
      "overridden_count": 0,
      "by_complexity": {},
      "by_decided_model": {},
      "by_decided_effort": {}
    }
  },
  "per_agent_usage": {
    "rows": []
  },
  "learning_loop": {
    "lessons_count": 0,
    "last_updated": null,
    "recent_sections": [],
    "recent_lessons": [],
    "decision_logging_status": "no PTME decisions logged yet",
    "qa_rounds": "metric pending — needs more logged runs",
    "rework_trend": "metric pending — needs more logged runs"
  }
};
