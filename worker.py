"""
Background worker script for consuming workflow execution jobs from the SQLite Huey queue.
Run this script to start processing background jobs.
"""
from workflows.queue import huey

if __name__ == "__main__":
    print("Starting background worker...")
    # Start the huey consumer
    consumer = huey.create_consumer()
    consumer.run()
