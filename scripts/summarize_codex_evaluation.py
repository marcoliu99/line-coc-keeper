"""Summarize two completed OAuth runs without treating failed calls as successes."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path


def strict_tool_correct(row):
    calls = row['tools']
    read_only = {'get_character_sheet', 'search_scenario', 'search_memory',
                 'get_combat_status', 'search_scenario_images'}
    kind = row['kind']
    if kind in {'ooc', 'pending'}:
        return all(c['name'] in read_only and c['ok'] for c in calls)
    expected = 'skill_check' if kind.startswith('check_') else 'add_carried_item'
    targets = [c for c in calls if c['name'] == expected]
    if len(targets) != 1 or not all(c['ok'] and c['name'] in read_only | {expected} for c in calls):
        return False
    args = targets[0]['arguments']
    if args.get('investigator') != 'Marco':
        return False
    if expected == 'skill_check':
        return (args.get('skill') == '偵查' and args.get('difficulty', 'regular') == 'regular'
                and not args.get('bonus_dice') and not args.get('penalty_dice')
                and not args.get('pushed') and targets[0]['pending'])
    return '鑰匙' in args.get('item', '')


def summary(rows):
    times = sorted(row['elapsed_seconds'] for row in rows)
    successful = [r['elapsed_seconds'] for r in rows if r['ok']]
    return {
        'cases': len(rows), 'state_narrative_pass': sum(r['ok'] for r in rows),
        'strict_tool_pass': sum(strict_tool_correct(r) for r in rows),
        'p50_seconds': round(statistics.median(times), 3),
        'p95_seconds': times[math.ceil(.95 * len(times)) - 1],
        'successful_p50_seconds': round(statistics.median(successful), 3) if successful else None,
        'decision_requests': sum(r['requests'] for r in rows),
        'game_tool_calls': sum(len(r['tools']) for r in rows),
        'successful_game_tool_calls': sum(c['ok'] for r in rows for c in r['tools']),
        'input_bytes': sum(r['input_bytes'] for r in rows),
        'python_rolls': sum(r.get('python_rolls', 0) for r in rows),
        'failures': [{'repetition': r['repetition'], 'kind': r['kind'], 'failures': r['failures'],
                      'strict_tool_pass': strict_tool_correct(r)} for r in rows
                     if not r['ok'] or not strict_tool_correct(r)],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('exec_path', type=Path)
    parser.add_argument('server_path', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {}
    groups = {}
    for name, path in [('exec', args.exec_path), ('app-server', args.server_path)]:
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(rows) == 25, f'{name}: expected 25 complete cases'
        assert len({(r['kind'], r['repetition']) for r in rows}) == 25
        result[name] = summary(rows)
        groups[name] = {(r['kind'], r['repetition']): r for r in rows}
    deltas = [groups['app-server'][key]['elapsed_seconds'] - row['elapsed_seconds']
              for key, row in groups['exec'].items()
              if row['ok'] and groups['app-server'][key]['ok']]
    result['paired_successes'] = len(deltas)
    result['paired_median_server_minus_exec_seconds'] = round(statistics.median(deltas), 3) if deltas else None
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
