import py_compile, sys
files = [
    'backend/agent_adapter_local_LLM_harness.py',
    'backend/agent_tools.py',
]
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f'OK: {f}')
    except py_compile.PyCompileError as e:
        print(f'FAIL: {f} - {e}')
        sys.exit(1)
print('All OK')
