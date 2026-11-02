import os

import bpy

TEX_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'bng_textures')

_image_cache = {}


def clear_cache(keep_images):
    for key, image in list(_image_cache.items()):
        if image not in keep_images:
            del _image_cache[key]
    if not os.path.isdir(TEX_DIR):
        return
    # Matching up to the first dot also keeps every frame of an image sequence (<name>.<frame>.<ext>)
    keep = tuple(os.path.basename(image.filepath).split('.')[0] + '.' for image in keep_images)
    for entry in os.scandir(TEX_DIR):
        if entry.is_file() and not entry.name.startswith(keep):
            os.remove(entry.path)


def save_textures(textures, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    images = []
    for i, tex in enumerate(textures):
        image = _image_cache.get(tex['key'])
        if image is None:
            ext = '.png' if tex['mime'] == 'image/png' else '.jpg'
            base = os.path.splitext(tex['name'])[0] if tex['name'] else f'texture_{i}'
            filepath = os.path.join(out_dir, f'{i}_{base}{ext}')
            with open(filepath, 'wb') as f:
                f.write(tex['data'])
            image = bpy.data.images.load(filepath)
            _image_cache[tex['key']] = image
        images.append(image)
    return images


def update_textures(images, updates):
    for update in updates:
        image = images[update['index']]
        with open(bpy.path.abspath(image.filepath), 'wb') as f:
            f.write(update['data'])
        image.reload()
