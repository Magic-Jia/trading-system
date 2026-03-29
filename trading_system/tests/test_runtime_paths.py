from trading_system.app.runtime_paths import build_runtime_paths


def test_build_runtime_paths_buckets_paper_outputs_under_env_directory(tmp_path):
    paths = build_runtime_paths("paper", runtime_root=tmp_path, runtime_env="prod")

    assert paths.mode == "paper"
    assert paths.runtime_env == "prod"
    assert paths.runtime_root == tmp_path
    assert paths.bucket_dir == tmp_path / "paper" / "prod"
    assert paths.state_file == tmp_path / "paper" / "prod" / "runtime_state.json"
    assert paths.paper_ledger_file == tmp_path / "paper" / "prod" / "paper_ledger.jsonl"
    assert paths.execution_log_file == tmp_path / "paper" / "prod" / "execution_log.jsonl"


def test_build_runtime_paths_isolates_paper_outputs_across_runtime_envs(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_RUNTIME_ENV", "prod")
    prod_paths = build_runtime_paths("paper", runtime_root=tmp_path)

    monkeypatch.setenv("TRADING_RUNTIME_ENV", "testnet")
    testnet_paths = build_runtime_paths("paper", runtime_root=tmp_path)

    assert prod_paths.bucket_dir == tmp_path / "paper" / "prod"
    assert testnet_paths.bucket_dir == tmp_path / "paper" / "testnet"
    assert prod_paths.bucket_dir != testnet_paths.bucket_dir
    assert prod_paths.state_file != testnet_paths.state_file
    assert prod_paths.paper_ledger_file != testnet_paths.paper_ledger_file
    assert prod_paths.execution_log_file != testnet_paths.execution_log_file
