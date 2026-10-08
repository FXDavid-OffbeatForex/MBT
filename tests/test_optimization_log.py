from core.optimization_log import parse_tester_log_lines, tester_log_diagnostics as read_diagnostics


def test_log_parser_extracts_only_allowlisted_diagnostics():
    text = "\n".join([
        "private account 12345678 password=do-not-store",
        "Tester genetic mode turned off due to 4 passes, to use it increase amount of optimized parameters",
        "Tester complete optimization started",
        "Tester optimization finished, total passes 4",
        "Tester forward optimization already processed, total passes 0",
        "Tester reading of 4 forward result records from cache...",
        "Statistics optimization done in 0 minutes 02 seconds",
    ])
    result = parse_tester_log_lines(text)
    assert result == {
        'genetic_disabled_small_grid': True, 'genetic_disabled_passes': 4,
        'effective_mode': 'complete', 'mt5_reported_passes': 4,
        'optimization_cache_reused': False, 'forward_reported_passes': 0,
        'forward_cache_reused': True, 'forward_cached_records': 4,
        'statistics_done': True,
    }
    assert '12345678' not in str(result) and 'do-not-store' not in str(result)


def test_log_snapshot_decoder_ignores_existing_lines(tmp_path):
    path = tmp_path / 'tester.log'
    prior = 'Tester optimization finished, total passes 999\n'.encode('utf-16-le')
    current = 'Tester optimization finished, total passes 4\n'.encode('utf-16-le')
    path.write_bytes(b'\xff\xfe' + prior + current)
    result = read_diagnostics((path, 2 + len(prior)))
    assert result['mt5_reported_passes'] == 4


def test_genetic_completion_and_resource_counts_from_real_log_shape():
    result = parse_tester_log_lines("\n".join([
        "Tester genetic optimization started",
        "Tester genetic calculation is over",
        "Tester result cache used 999 times",
        "Tester genetic optimization finished on pass 1152 (of 400)",
        "Statistics optimization done in 0 minutes 03 seconds",
        "Statistics local 153 tasks (100%), remote 0 tasks (0%), cloud 0 tasks (0%)",
        "Tester 129 new records saved to cache file 'private-path.opt'",
    ]))
    assert result == {
        'effective_mode': 'genetic', 'result_cache_uses': 999,
        'genetic_finished': True, 'genetic_pass_counter': 1152,
        'genetic_search_space': 400, 'statistics_done': True,
        'local_tasks': 153, 'remote_tasks': 0, 'cloud_tasks': 0,
        'new_cache_records': 129,
    }
    assert 'private-path' not in str(result)
