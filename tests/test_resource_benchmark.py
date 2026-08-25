from benchmarks.resource_handle_benchmark import run_benchmark


def test_resource_benchmark_compares_full_pruning_and_handle() -> None:
    full, pruning, handle = run_benchmark(64 * 1024, read_bytes=4 * 1024)

    assert [full.strategy, pruning.strategy, handle.strategy] == [
        "Full",
        "V0.4 Pruning",
        "V0.5 Handle",
    ]
    assert full.raw_retained and pruning.raw_retained and handle.raw_retained
    assert full.restart_read_ok and pruning.restart_read_ok
    assert pruning.model_visible_bytes < full.model_visible_bytes
    assert handle.model_visible_bytes < full.model_visible_bytes
    assert handle.session_artifact_bytes < full.session_artifact_bytes
    assert handle.resource_artifact_bytes >= handle.input_bytes
    assert handle.restart_read_ok is True
