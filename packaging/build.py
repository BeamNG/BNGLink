# Single entry point for BNGLink releases. Builds the three distributables
# (BeamNG mod, Blender add-on, Unreal plugin) with one common version, so
# every artifact reports the same number.
#
# Version resolution: --version argument > latest reachable git tag (v1.2.3)
# > root VERSION file. Tag a release with:
#   git tag v1.2.3 && git push --tags   (or pass --version 1.2.3)
#
# Usage:
#   python packaging/build.py                    # all three into dist/
#   python packaging/build.py --target beamng    # one part only
#   python packaging/build.py --version 1.2.3 --out dist

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

packaging_dir = Path(__file__).resolve().parent
root_dir = packaging_dir.parent

TARGETS = {
    'beamng': packaging_dir / 'package_mod.py',
    'blender': root_dir / 'blender' / 'package_addon.py',
    'ue5': root_dir / 'ue5' / 'package_plugin.py',
}


def load_builder(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f'cannot load packaging script {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build


def resolve_version(cli_version):
    if cli_version:
        return cli_version.lstrip('vV')
    tag = ''
    try:
        tag = subprocess.run(
            ['git', 'describe', '--tags', '--abbrev=0', '--match', 'v[0-9]*'],
            cwd=root_dir, capture_output=True, text=True, check=False,
        ).stdout.strip()
    except OSError:
        pass  # git unavailable; fall through to the VERSION file
    if tag:
        return tag.lstrip('vV')
    version_file = root_dir / 'VERSION'
    if version_file.exists():
        return version_file.read_text(encoding='utf-8').strip()
    raise SystemExit('no version: pass --version, create a git tag like v1.0.0, or add a VERSION file')


def main():
    parser = argparse.ArgumentParser(description='Build BNGLink distributables with one shared version.')
    parser.add_argument('--target', choices=[*TARGETS, 'all'], default='all', help='Which part to build (default: all)')
    parser.add_argument('--version', help='Release version, e.g. 1.2.3 (default: git tag, then VERSION file)')
    parser.add_argument('--out', help='Output directory (default: dist/)')
    args = parser.parse_args()

    version = resolve_version(args.version)
    out_dir = Path(args.out) if args.out else root_dir / 'dist'
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = TARGETS.values() if args.target == 'all' else [TARGETS[args.target]]
    for target_path in targets:
        load_builder(target_path)(version=version, out_dir=out_dir)
    print(f'BNGLink v{version}: {len(list(out_dir.glob("*.zip")))} zip(s) in {out_dir}')


if __name__ == '__main__':
    sys.exit(main())
