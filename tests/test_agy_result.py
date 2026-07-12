from scripts.agy_result import classify


def test_timeout_error_prose_with_nonstandard_nonzero_exit_is_failed():
    assert classify(7, "Error: timeout waiting for response") == "failed"


def test_machine_failure_marker_is_failed_even_if_exit_zero():
    assert classify(0, "AGY_PTY_ERROR: exception:RuntimeError:boom") == "failed"


def test_real_content_and_zero_exit_is_done():
    assert classify(0, "Completed requested work") == "done"
