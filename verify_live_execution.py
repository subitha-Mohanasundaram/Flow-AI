#!/usr/bin/env python3
"""
Verify that live plugin execution works with the bug fix.
This script tests the REST API plugin with real HTTP calls.
"""

import json
import time
from pathlib import Path
from workflows.executor import start_run, get_run

def create_rest_api_workflow():
    """Create a simple REST API workflow for testing."""
    return {
        "id": "test_wf_live",
        "name": "Live REST API Test",
        "description": "Test live HTTP execution",
        "nodes": [
            {
                "id": "get_post",
                "type": "rest_api",
                "config": {
                    "url": "https://jsonplaceholder.typicode.com/posts/1",
                    "method": "GET",
                    "headers": {},
                    "timeout": 30
                }
            }
        ],
        "edges": []
    }


def main():
    print("=" * 70)
    print("Live Plugin Execution Verification")
    print("=" * 70)
    print("\nThis test verifies that the bug fix allows real HTTP calls.")
    print("\nBug Fix:")
    print("  File: workflows/executor.py:314")
    print("  Change: NodeExecutor(dry_run=True) -> NodeExecutor(dry_run=dry_run)")
    print("  Impact: Plugins now respect live execution mode\n")
    
    # Create test workflow
    workflow = create_rest_api_workflow()
    print(f"Workflow: {workflow['name']}")
    print(f"Node Type: {workflow['nodes'][0]['type']}")
    print(f"URL: {workflow['nodes'][0]['config']['url']}")
    
    # Test 1: Dry-run mode (simulation)
    print("\n" + "-" * 70)
    print("TEST 1: Dry-Run Mode (Simulation)")
    print("-" * 70)
    try:
        run_id = start_run(workflow, dry_run=True)
        print(f"Run ID: {run_id}")
        
        # Wait for completion
        time.sleep(2)
        result = get_run(run_id)
        
        if result and result.get('status') in ('succeeded', 'completed', 'in_progress'):
            print(f"Status: {result.get('status')}")
            print("✓ Dry-run executed successfully")
            print("  (Note: Data is simulated, not from real API)")
        else:
            print(f"✗ Dry-run failed: {result}")
    except Exception as e:
        print(f"✗ Error during dry-run: {e}")
        import traceback
        traceback.print_exc()
    
    # Test 2: Live mode (real HTTP)
    print("\n" + "-" * 70)
    print("TEST 2: Live Mode (Real HTTP Call)")
    print("-" * 70)
    try:
        run_id = start_run(workflow, dry_run=False)
        print(f"Run ID: {run_id}")
        
        # Wait for completion
        time.sleep(3)
        result = get_run(run_id)
        
        if result:
            print(f"Status: {result.get('status')}")
            
            if result.get('status') in ('succeeded', 'completed'):
                print("✓ Live execution succeeded")
                
                # Check if we got real data
                node_states = result.get('node_states', {})
                get_post_state = node_states.get('get_post', {})
                outputs = get_post_state.get('outputs', {})
                
                if isinstance(outputs.get('response'), dict) and 'userId' in outputs.get('response', {}):
                    response_data = outputs.get('response')
                    print(f"\n✓ Real HTTP response received from JSONPlaceholder API:")
                    print(f"  User ID: {response_data.get('userId')}")
                    print(f"  Post ID: {response_data.get('id')}")
                    print(f"  Title: {response_data.get('title', '')[:60]}...")
                    print(f"  Status Code: {outputs.get('status_code')}")
                    
                    print("\n✓ SUCCESS: Live plugin execution is working!")
                    print("  The bug fix allows real HTTP calls to be made.")
                    return True
                else:
                    print(f"\n? Outputs: {outputs}")
                    print(f"? Node state: {get_post_state}")
                    return False
            else:
                print(f"Status: {result.get('status')}")
                print(f"Error: {result.get('error')}")
                print(f"Full result: {json.dumps(result, indent=2, default=str)[:500]}")
                return False
        else:
            print("✗ No result returned")
            return False
    except Exception as e:
        print(f"✗ Error during live execution: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    
    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    if success:
        print("✓ Live plugin execution is working correctly")
        print("  The bug fix in workflows/executor.py:314 is effective")
        print("\nNext: Test more complex workflows with multiple plugins")
    else:
        print("✗ Live plugin execution has issues")
        print("  Check the errors above for details")
    print("=" * 70)
