#!/usr/bin/env python3
"""Preserve the locked Linux Cargo graph's supplied license files, deduplicated."""

import hashlib
import json
import pathlib
import subprocess
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DESTINATION = ROOT / 'public/vendor/licenses/rust'


def main():
    metadata = json.loads(subprocess.check_output(['cargo', 'metadata', '--locked', '--format-version', '1', '--filter-platform', 'x86_64-unknown-linux-gnu'], cwd=ROOT))
    locked = tomllib.loads((ROOT / 'Cargo.lock').read_text())
    checksums = {(package['name'], package['version'], package.get('source')): package.get('checksum') for package in locked['package']}
    selected = {node['id'] for node in metadata['resolve']['nodes']} - set(metadata['workspace_members'])
    records = []
    blobs = {}
    missing = []
    for package in sorted(metadata['packages'], key=lambda package: (package['name'], package['version'])):
        if package['id'] not in selected:
            continue
        directory = pathlib.Path(package['manifest_path']).parent.resolve()
        candidates = [path for path in directory.iterdir() if path.is_file() and path.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'COPYRIGHT', 'NOTICE', 'UNLICENSE'))]
        if package.get('license_file'):
            candidates.append(directory / package['license_file'])
        files = []
        for path in sorted(set(candidates)):
            path.resolve().relative_to(directory)
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            blobs[digest] = data
            files.append({'source': str(path.relative_to(directory)), 'sha256': digest, 'file': digest + '.txt'})
        record = {'name': package['name'], 'version': package['version'], 'license': package['license'], 'source': package['source'], 'archive_checksum': checksums.get((package['name'], package['version'], package['source'])), 'files': files}
        records.append(record)
        if not files:
            missing.append(package['name'] + '@' + package['version'])
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for digest, data in sorted(blobs.items()):
        (DESTINATION / (digest + '.txt')).write_bytes(data)
    (DESTINATION / 'UPSTREAM.json').write_text(json.dumps({'target': 'x86_64-unknown-linux-gnu', 'packages': records, 'missing_supplied_license_files': missing}, indent=2) + '\n')
    print(json.dumps({'packages': len(records), 'unique_license_files': len(blobs), 'bytes': sum(map(len, blobs.values())), 'missing_supplied_license_files': missing}, indent=2))
    if missing:
        raise SystemExit('Some locked packages supply no root license file; review the manifest before release')


if __name__ == '__main__':
    main()
