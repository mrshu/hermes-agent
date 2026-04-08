import threading

import tools.terminal_tool as terminal_tool_module


def test_approval_callback_is_thread_local():
    main_cb = object()
    worker_cb = object()
    ready = threading.Event()
    release = threading.Event()
    seen = {}

    terminal_tool_module.set_approval_callback(main_cb)

    def worker():
        terminal_tool_module.set_approval_callback(worker_cb)
        seen["worker_before"] = terminal_tool_module.get_approval_callback()
        ready.set()
        release.wait(timeout=5)
        seen["worker_after"] = terminal_tool_module.get_approval_callback()
        terminal_tool_module.set_approval_callback(None)

    thread = threading.Thread(target=worker)
    try:
        thread.start()
        assert ready.wait(timeout=5)
        seen["main"] = terminal_tool_module.get_approval_callback()
        terminal_tool_module.set_approval_callback(None)
        release.set()
        thread.join(timeout=5)
    finally:
        terminal_tool_module.set_approval_callback(None)
        release.set()
        thread.join(timeout=5)

    assert seen["main"] is main_cb
    assert seen["worker_before"] is worker_cb
    assert seen["worker_after"] is worker_cb
    assert terminal_tool_module.get_approval_callback() is None
