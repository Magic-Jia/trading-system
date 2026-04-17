branch/worktree: feat/full-market-task2-instrument-metadata @ /tmp/hermes-trade-task2-full-market
current objective: Fix raw-market import normalization so accepted manifest aliases/casing stay canonical in ImportedRawMarketSeries and remain usable by the phase1 importer
last verified command + result: `UV_CACHE_DIR=/tmp/uv-cache uv run --with pytest pytest -q trading_system/tests/test_backtest_archive_importer.py::test_load_phase1_raw_market_series_reads_manifest_backed_files_into_importer_structures trading_system/tests/test_backtest_archive_importer.py::test_load_phase1_raw_market_imports_groups_supported_binance_futures_series trading_system/tests/test_backtest_archive_dataset_importer.py::test_build_phase1_dataset_bundle_materials_requires_complete_phase1_symbol_set trading_system/tests/test_backtest_archive_dataset_importer.py::test_build_phase1_dataset_bundle_materials_uses_canonicalized_import_scope_from_valid_manifest_aliases` => 5 passed
last commit: 9705bf71f6ae378c847a043565c9545bb15ad369
next action: commit the narrow raw-market normalization fix and stop; do not merge back to the main workspace
last user update time: 2026-04-17 14:04 GMT+2
