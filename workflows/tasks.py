from workflows.queue import huey
from workflows.executor import _thread_target

@huey.task()
def execute_workflow_task(run_id: str, workflow: dict, inputs: dict, dry_run: bool, plugin_configs: dict):
    # This runs in the background worker!
    _thread_target(run_id, workflow, inputs, dry_run, plugin_configs)
