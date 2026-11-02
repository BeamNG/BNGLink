import argparse
import io
import json
import time
import zipfile
from pathlib import Path

MOD_NAME = 'bnglink_beamng'
ICON_SIZE = (96, 96)
THUMB_WIDTH = 400
ICON_QUALITY = 90
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg'}

root_dir = Path(__file__).resolve().parent.parent
packaging_dir = root_dir / 'packaging'
gelua_source_dir = root_dir / 'beamng' / 'gelua'
vlua_source_dir = root_dir / 'beamng' / 'vlua'
ui_source_dir = root_dir / 'ui'
meta_path = packaging_dir / 'modinfo.json'
thumbnail_path = packaging_dir / 'thumbnail.png'
icon_override_path = packaging_dir / 'icon.jpg'
screenshots_dir = packaging_dir / 'screenshots'


def open_image(path):
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit(
            f'Pillow is needed to derive mod manager art from {path} '
            '(pip install pillow) - or provide packaging/icon.jpg and empty packaging/screenshots/'
        )
    from PIL import Image
    return Image.open(path)


def save_jpeg(image, size, archive, arcname):
    from PIL import Image
    if image.mode != 'RGB':
        image = image.convert('RGB')
    image = image.resize(size, getattr(Image, 'Resampling', Image).LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=ICON_QUALITY)
    archive.writestr(arcname, buffer.getvalue())


def pack_manager_art(archive, info_dir, attachments):
    if icon_override_path.exists():
        archive.write(icon_override_path, (info_dir / 'icon.jpg').as_posix())
    elif thumbnail_path.exists():
        icon = open_image(thumbnail_path)
        side = min(icon.size)
        left = (icon.width - side) // 2
        top = (icon.height - side) // 2
        save_jpeg(icon.crop((left, top, left + side, top + side)), ICON_SIZE, archive, (info_dir / 'icon.jpg').as_posix())
    else:
        print(f'Warning: neither {icon_override_path} nor {thumbnail_path} exists, mod manager shows a placeholder icon')

    sources = sorted(p for p in screenshots_dir.glob('*') if p.suffix.lower() in IMAGE_SUFFIXES) if screenshots_dir.is_dir() else []
    if not sources and thumbnail_path.exists():
        sources = [thumbnail_path]
    for index, source in enumerate(sources, start=1):
        data_name = f'{index}{source.suffix.lower()}'
        thumb_name = f'{index}.jpg'
        archive.write(source, (info_dir / 'images' / data_name).as_posix())
        image = open_image(source)
        save_jpeg(image, (THUMB_WIDTH, max(1, round(image.height * THUMB_WIDTH / image.width))), archive, (info_dir / 'thumbs' / thumb_name).as_posix())
        attachments.append({'data_filename': data_name, 'original_filename': source.name, 'thumb_filename': f'thumbs/{thumb_name}'})


def build_info_json(meta, version, attachments):
    # Same field set repo-packed mods ship; the manager reads title, tag_line,
    # username, version_string and message from here.
    return {
        'attachments': attachments,
        'category_title': '',
        'current_version_id': 1,
        'download_count': 0,
        'filename': f'{MOD_NAME}.zip',
        'hashes': [],
        'last_update': int(time.time()),
        'message': meta['message'],
        'path': f"{meta['tagid']}/1/",
        'prefix_title': '',
        'rating_avg': 0,
        'rating_count': 0,
        'rating_sum': 0,
        'resource_category_id': 0,
        'resource_date': meta['resource_date'],
        'resource_id': 1,
        'resource_version_id': 1,
        'review_count': 0,
        'tag_line': meta['tag_line'],
        'tagid': meta['tagid'],
        'title': meta['title'],
        'update_count': 0,
        'user_id': 0,
        'username': meta['username'],
        'version_string': version,
        'via': 'packed by BNGLink packaging/package_mod.py',
    }


def build(version=None, out_dir=None):
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    version = version or (root_dir / 'VERSION').read_text(encoding='utf-8').strip()
    info_dir = Path('mod_info') / meta['tagid']

    content_files = {Path('lua/ge/extensions/BNGLink') / path.name: path for path in gelua_source_dir.glob('*.lua')}
    content_files.update({Path('lua/vehicle/extensions') / path.name: path for path in vlua_source_dir.glob('*.lua')})
    content_files.update({
        Path('ui') / path.relative_to(ui_source_dir): path
        for path in ui_source_dir.rglob('*')
        if path.is_file() and path.name != 'README.md'
    })
    for source_dir in (gelua_source_dir, vlua_source_dir):
        if not (source_dir / 'BNGLink.lua').exists():
            raise SystemExit(f'{source_dir} does not look like the BNGLink sender tree')

    out = (Path(out_dir) if out_dir else Path.cwd()) / f'{MOD_NAME}.zip'
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        attachments = []
        pack_manager_art(archive, info_dir, attachments)
        archive.writestr((info_dir / 'info.json').as_posix(), json.dumps(build_info_json(meta, version, attachments), indent=2))
        for relative_path, path in sorted(content_files.items()):
            archive.write(path, relative_path.as_posix())
    print(f'Wrote {out} (v{version})')
    return out


def main():
    parser = argparse.ArgumentParser(description='Package the BNGLink BeamNG mod zip (mod repo format).')
    parser.add_argument('--version', help='Version written to info.json version_string (default: value in the VERSION file)')
    parser.add_argument('--out', help='Output directory (default: current folder)')
    args = parser.parse_args()
    build(version=args.version, out_dir=args.out)


if __name__ == '__main__':
    main()
