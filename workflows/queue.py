import os
import asyncio
from huey import SqliteHuey

# SQLite-based message queue for background workflow execution.
# Stores tasks in huey_queue.db
huey = SqliteHuey(filename='workflows/huey_queue.db')

@huey.task()
def execute_workflow_task(workflow, run_id, dry_run, inputs, p_cfg):
    from workflows.executor import _execute_async, get_run
    
    record = get_run(run_id)
    if not record:
        # Load from disk if not in memory (since we are in a worker process)
        import json
        from pathlib import Path
        wf_id = workflow.get("id") or workflow.get("workflow_id", "unknown")
        run_file = Path("workflows") / "runs" / wf_id / f"{run_id}.json"
        if run_file.exists():
            record = json.loads(run_file.read_text(encoding="utf-8"))
        else:
            print(f"Warning: Record for {run_id} not found!")
            return
            
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # We pass on_update=None because the worker updates disk directly
        loop.run_until_complete(
            _execute_async(workflow, run_id, record, dry_run, inputs, None, p_cfg)
        )
    finally:
        loop.close()
