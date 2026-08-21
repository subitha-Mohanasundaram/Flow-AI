import sys, traceback

print('=== PHASE 1: SYSTEM HEALTH CHECK ===')

results = {}

# Backend imports
try:
    import web_app
    results['Backend'] = 'PASS'
    print('Backend imports: PASS')
except Exception as e:
    results['Backend'] = f'FAIL: {e}'
    print(f'Backend imports: FAIL - {e}')
    traceback.print_exc()

# DB check
try:
    from assignment_intel.auth import list_users
    users = list_users()
    results['Database'] = f'PASS ({len(users)} users)'
    print(f'Database: PASS - {len(users)} user(s): {[u.get("username") for u in users]}')
except Exception as e:
    results['Database'] = f'FAIL: {e}'
    print(f'Database: FAIL - {e}')

# APScheduler
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = BackgroundScheduler()
    sched.start()
    sched.shutdown()
    results['APScheduler'] = 'PASS'
    print('APScheduler: PASS')
except Exception as e:
    results['APScheduler'] = f'FAIL: {e}'
    print(f'APScheduler: FAIL - {e}')

# ChromaDB
try:
    from ai_builder.rag import collection, search_context
    count = collection.count()
    docs = search_context("email automation")
    results['ChromaDB'] = f'PASS ({count} docs, retrieval: {len(docs)} results)'
    print(f'ChromaDB: PASS - {count} doc(s) stored, retrieved {len(docs)} for test query')
    if docs:
        print(f'  Sample retrieved: {docs[0][:80]}...')
except Exception as e:
    results['ChromaDB'] = f'FAIL: {e}'
    print(f'ChromaDB: FAIL - {e}')

# PluginRegistry
try:
    from plugins.sdk.registry import PluginRegistry
    reg = PluginRegistry()
    reg.load_all()
    plugin_ids = reg.ids()
    results['PluginRegistry'] = f'PASS ({len(reg)} plugins: {plugin_ids})'
    print(f'PluginRegistry: PASS - {len(reg)} plugin(s): {plugin_ids}')
except Exception as e:
    results['PluginRegistry'] = f'FAIL: {e}'
    print(f'PluginRegistry: FAIL - {e}')

# AI Client
try:
    from ai_builder.ai_client import AIClient
    client = AIClient()
    results['AI Client'] = f'PASS (provider={client._provider})'
    print(f'AI Client: PASS - provider={client._provider}, model={client._model}')
except Exception as e:
    results['AI Client'] = f'FAIL: {e}'
    print(f'AI Client: FAIL - {e}')

# Auth system
try:
    from assignment_intel.auth import create_session_token, decode_session_token
    token = create_session_token({'username': 'test', 'role': 'user'})
    payload = decode_session_token(token)
    username = payload['username']
    results['Authentication'] = 'PASS'
    print(f'Authentication: PASS - JWT roundtrip OK, user={username}')
except Exception as e:
    results['Authentication'] = f'FAIL: {e}'
    print(f'Authentication: FAIL - {e}')

# Huey queue
try:
    from workflows.queue import huey, execute_workflow_task
    storage_type = type(huey.storage).__name__
    results['Huey'] = f'PASS (storage={storage_type})'
    print(f'Huey: PASS - storage backend={storage_type}')
except Exception as e:
    results['Huey'] = f'FAIL: {e}'
    print(f'Huey: FAIL - {e}')

# Encryption
try:
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    f = Fernet(key)
    enc = f.encrypt(b'test-secret')
    dec = f.decrypt(enc)
    assert dec == b'test-secret'
    results['Fernet'] = 'PASS'
    print('Fernet encryption: PASS - roundtrip OK')
except Exception as e:
    results['Fernet'] = f'FAIL: {e}'
    print(f'Fernet encryption: FAIL - {e}')

# Trigger runtime
try:
    from workflows.trigger_runtime import TriggerRuntime
    rt = TriggerRuntime()
    results['TriggerRuntime'] = 'PASS'
    print('TriggerRuntime: PASS - class loads OK')
except Exception as e:
    results['TriggerRuntime'] = f'FAIL: {e}'
    print(f'TriggerRuntime: FAIL - {e}')

# Executor
try:
    from workflows.executor import start_run, get_run
    results['Executor'] = 'PASS'
    print('Executor: PASS')
except Exception as e:
    results['Executor'] = f'FAIL: {e}'
    print(f'Executor: FAIL - {e}')

print('\n=== HEALTH SUMMARY ===')
for k, v in results.items():
    status = 'PASS' if v.startswith('PASS') else 'FAIL'
    print(f'{k}: {status}')
