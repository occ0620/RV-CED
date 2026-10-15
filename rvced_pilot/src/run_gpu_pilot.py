"""One-command smoke-gated continuation. Uses existing local data/model only."""
import argparse
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('--config', default=str(root / 'configs/gpu.yaml'))
p.add_argument('--smoke-dir', default=str(root / 'runs/smoke-v1'))
p.add_argument('--main-dir', default=str(root / 'runs/main-v1'))
p.add_argument('--inventory-from')
args = p.parse_args()


def execute(stage, run, phase):
    command = [sys.executable, str(root / 'pilot.py'), stage, '--config', args.config,
               '--run-dir', run, '--phase', phase, '--smoke-dir', args.smoke_dir]
    if stage == 'inventory' and args.inventory_from:
        command.extend(['--inventory-from', args.inventory_from])
    print('Executing:', ' '.join(command), flush=True)
    return subprocess.run(command, cwd=root).returncode


for run, phase in ((args.smoke_dir, 'smoke'), (args.main_dir, 'main')):
    for stage in ('doctor', 'inventory', 'build', 'audit-export', 'infer'):
        if execute(stage, run, phase):
            for final in ('analyze', 'report'):
                execute(final, run, phase)
            raise SystemExit('Stopped at ' + phase + '/' + stage + '; inspect saved failures before continuing')
    for stage in ('analyze', 'report'):
        if execute(stage, run, phase):
            raise SystemExit('Report/analysis failed; inspect preserved evidence')
print('Real technical inference completed. Human relation audit remains required.', flush=True)
