# Builds bnglink_blender.zip for Blender's Add-ons > Install from Disk.
# The shared bnglink and bngusd packages are bundled next to the add-on modules.

import argparse
import re
import zipfile
from pathlib import Path

# Must not be 'bnglink', which would shadow the shared package
ADDON_NAME = 'bnglink_blender'
SHARED_PACKAGES = ('bnglink', 'bngusd')
# Development scripts that only run from the source tree
EXCLUDED = {'bench.py', 'package_addon.py'}

blender_dir = Path(__file__).resolve().parent
root_dir = blender_dir.parent


def stamped_init(version):
    text = (blender_dir / '__init__.py').read_text(encoding='utf-8')
    numbers = re.match(r'(\d+)(?:\.(\d+))?(?:\.(\d+))?', version)
    if numbers is None:
        raise SystemExit(f'cannot stamp version {version!r} into bl_info: expected a leading X.Y.Z number')
    tuple_version = repr(tuple(int(p) for p in numbers.groups() if p is not None))
    text, hits = re.subn(r"'version':\s*\([^)]*\)", f"'version': {tuple_version}", text, count=1)
    if hits != 1:
        raise SystemExit("blender/__init__.py: could not find the bl_info 'version' tuple to stamp")
    return text


def build(version=None, out_dir=None):
    files = {path: path.name for path in blender_dir.glob('*.py') if path.name not in EXCLUDED}
    for package in SHARED_PACKAGES:
        files.update({path: path.relative_to(root_dir).as_posix() for path in (root_dir / package).glob('*.py')})
    out = (Path(out_dir) if out_dir else Path.cwd()) / f'{ADDON_NAME}.zip'
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, name in sorted(files.items()):
            if version and name == '__init__.py':
                archive.writestr(f'{ADDON_NAME}/{name}', stamped_init(version))
            else:
                archive.write(path, f'{ADDON_NAME}/{name}')
    print(f'Wrote {out}' + (f' (v{version})' if version else ''))
    return out


def main():
    parser = argparse.ArgumentParser(description='Package the BNGLink Blender add-on zip.')
    parser.add_argument('--version', help='Version stamped into bl_info (default: value in the source file)')
    parser.add_argument('--out', help='Output directory (default: current folder)')
    args = parser.parse_args()
    build(version=args.version, out_dir=args.out)


if __name__ == '__main__':
    main()
