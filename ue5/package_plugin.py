import argparse
import json
import re
import zipfile
from pathlib import Path

PLUGIN_NAME = 'BngLinkBridge'
SHARED_PACKAGES = ('bnglink', 'bngusd')
EXCLUDED_UE_MODULES = {'bench.py', 'package_plugin.py'}
EXCLUDED_PARTS = {'Binaries', 'Intermediate', '__pycache__'}

ue5_dir = Path(__file__).resolve().parent
root_dir = ue5_dir.parent
plugin_dir = ue5_dir / 'UnrealPlugin' / PLUGIN_NAME
uplugin_path = plugin_dir / f'{PLUGIN_NAME}.uplugin'


def stamped_uplugin(version):
    descriptor = json.loads(uplugin_path.read_text(encoding='utf-8'))
    numbers = re.match(r'(\d+)(?:\.(\d+))?(?:\.(\d+))?', version)
    if numbers is None:
        raise SystemExit(f'cannot stamp version {version!r} into the .uplugin: expected a leading X.Y.Z number')
    major, minor, patch = (int(p) if p else 0 for p in numbers.groups())
    # UE shows VersionName; the integer Version only needs to grow per release
    descriptor['Version'] = major * 10000 + minor * 100 + patch
    descriptor['VersionName'] = version
    return json.dumps(descriptor, indent=4)


def build(version=None, out_dir=None):
    files = {
        path.relative_to(plugin_dir): path
        for path in plugin_dir.rglob('*')
        if path.is_file() and not EXCLUDED_PARTS.intersection(path.parts)
    }
    python_dir = Path('Content/Python')
    files.update({
        python_dir / path.name: path
        for path in ue5_dir.glob('*.py')
        if path.name not in EXCLUDED_UE_MODULES
    })
    for package in SHARED_PACKAGES:
        files.update({
            python_dir / package / path.name: path
            for path in (root_dir / package).glob('*.py')
        })

    out = (Path(out_dir) if out_dir else Path.cwd()) / 'bnglink_unreal.zip'
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for relative_path, path in sorted(files.items()):
            arcname = Path(PLUGIN_NAME) / relative_path
            if version and relative_path == Path(f'{PLUGIN_NAME}.uplugin'):
                archive.writestr(arcname.as_posix(), stamped_uplugin(version))
            else:
                archive.write(path, arcname)
    print(f'Wrote {out}' + (f' (v{version})' if version else ''))
    return out


def main():
    parser = argparse.ArgumentParser(description='Package the BngLinkBridge Unreal plugin zip.')
    parser.add_argument('--version', help='Version stamped into the .uplugin descriptor (default: value in the source file)')
    parser.add_argument('--out', help='Output directory (default: current folder)')
    args = parser.parse_args()
    build(version=args.version, out_dir=args.out)


if __name__ == '__main__':
    main()
