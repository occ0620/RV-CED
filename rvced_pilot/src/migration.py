"""Import full origin-verified inventory without reselecting after model outputs."""
from pathlib import Path
import shutil

from .common import Blocked, file_hash, read_json, write_json


def import_inventory(run, c, source):
    run, source = Path(run), Path(source)
    if read_json(run / 'manifests/freeze.json', {}).get('status') == 'frozen':
        raise Blocked('Cannot replace a frozen inventory')
    provenance = read_json(source / 'provenance.json')
    if provenance['predictions_used_for_selection'] is not False:
        raise Blocked('Migrated sample selection is not preregistered independently of predictions')
    if file_hash(source / 'inventory.csv') != provenance['inventory_sha256']:
        raise Blocked('Migrated inventory SHA256 mismatch')
    if file_hash(c['dataset']['labels_path']) != provenance['labels_sha256']:
        raise Blocked('Remote real labels differ from original verified labels')
    shutil.copyfile(source / 'inventory.csv', run / 'manifests/inventory.csv')
    stats = read_json(source / 'inventory_stats.json')
    stats.update(status='migrated_origin_verified_real_inventory', migration=provenance)
    write_json(run / 'results/inventory_stats.json', stats)
    write_json(run / 'evidence/migration_provenance.json', provenance)
