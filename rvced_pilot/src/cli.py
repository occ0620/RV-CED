from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
import traceback

from .common import (Blocked, ROOT, ensure_layout, failure, load_config, record_resource,
                     write_json, write_csv)


def main(argv=None):
    parser = argparse.ArgumentParser(description='RV-CED real-data pilot; no training or fake inference')
    parser.add_argument('command', choices=['inspect', 'doctor', 'inventory', 'build', 'media-smoke', 'audit-export', 'infer', 'analyze', 'report', 'all'])
    parser.add_argument('--config', default=str(ROOT / 'configs/pilot.yaml'))
    parser.add_argument('--run-dir', default=str(ROOT))
    parser.add_argument('--phase', choices=['smoke', 'main'], default='main')
    parser.add_argument('--smoke-dir', default=str(ROOT / 'runs/smoke-v1'))
    parser.add_argument('--shared-budget-dir')
    parser.add_argument('--inventory-from', help='SHA256-verified full local inventory snapshot; only preselected raw clips must be present remotely')
    parser.add_argument('--labels', help='CSV path for inspect only')
    parser.add_argument('--reviews', help='Completed independent human review CSV; never model-generated')
    args = parser.parse_args(argv)
    run = Path(args.run_dir).resolve()
    ensure_layout(run)
    c = load_config(args.config)
    commands = ['doctor', 'inventory', 'build', 'media-smoke', 'audit-export', 'infer', 'analyze', 'report'] if args.command == 'all' else [args.command]
    failed = False
    for cmd in commands:
        wall, cpu, state = time.perf_counter(), time.process_time(), 'success'
        try:
            if cmd == 'inspect':
                from .data import inspect_labels
                data = inspect_labels(args.labels or c['dataset']['labels_path'])
                write_json(run / 'evidence/label_schema.json', data)
                print(data)
            elif cmd == 'doctor':
                from .workflow import doctor
                env = doctor(run, c)
                if env['blockers']:
                    state, failed = 'blocked', True
            elif cmd == 'inventory':
                from .data import inventory, select_pairs
                if args.inventory_from:
                    from .migration import import_inventory
                    from .data import load_inventory
                    import_inventory(run, c, args.inventory_from)
                    rows = load_inventory(run)
                else:
                    rows = inventory(run, c)
                for phase in ('smoke', 'main'):
                    pairs, candidates, stats = select_pairs(rows, c, phase)
                    write_csv(run / f'results/candidate_counts.{phase}.csv', candidates,
                              ['source_id', 'modality', 'source_strength_eligible', 'a_candidates', 'b_candidates',
                               'compatible_ab_pairs', 'independent_groups_feasible'])
                    write_json(run / f'results/selection_stats.{phase}.json', stats)
                    print(phase, stats['source_counts'], 'triplets', len(pairs), flush=True)
            elif cmd == 'build':
                from .workflow import build
                print(build(run, c, args.phase, args.smoke_dir))
            elif cmd == 'infer':
                from .workflow import run_infer
                run_infer(run, c, args.phase, args.shared_budget_dir)
            elif cmd == 'media-smoke':
                from .workflow import media_smoke
                media_smoke(run, c)
            elif cmd == 'audit-export':
                from .audit import export_audit
                export_audit(run, c)
            elif cmd == 'analyze':
                from .analysis import analyze
                analyze(run, c, args.reviews)
            elif cmd == 'report':
                from .reporting import report
                report(run, c)
        except KeyboardInterrupt:
            state, failed = 'interrupted', True
            failure(run, cmd, 'KeyboardInterrupt', 'Interrupted; preserve partial files and resume with the same command')
            print(cmd + ': interrupted; partial artifacts retained', file=sys.stderr, flush=True)
            return 130
        except Exception as e:
            failed = True
            state = 'blocked' if isinstance(e, Blocked) else 'failed'
            failure(run, cmd, type(e).__name__, str(e))
            write_json(run / 'evidence' / (cmd + '_last_error.json'),
                       dict(stage=cmd, status=state, exception=type(e).__name__, message=str(e), traceback=traceback.format_exc()))
            print(f'{cmd}: {state}: {e}', file=sys.stderr, flush=True)
        finally:
            record_resource(run, cmd, wall, cpu, state)
    return 2 if failed else 0
