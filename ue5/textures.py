import hashlib
import os
import re
import shutil

import numpy as np
import unreal

GENERATED_ROOT = '/Game/BNGLink/Generated'
_texture_cache = {}
# sRGB textures; every other texture field holds linear data
COLOR_KEYS = {
    'baseColorTexture',
    'emissiveTexture',
    'colorMapTexture',
    'detailBaseColorTexture',
    'overlayTexture',
    'specularTexture',
}
NORMAL_KEYS = {'normalTexture', 'detailNormalTexture'}


def clear_cache(keep_textures, cache_dir):
    global _texture_cache
    _texture_cache = {
        key: texture for key, texture in _texture_cache.items()
        if texture in keep_textures
    }
    shutil.rmtree(cache_dir, ignore_errors=True)


def _asset_name(name, index):
    base = os.path.splitext(os.path.basename(name))[0]
    return re.sub(r'[^A-Za-z0-9_]+', '_', base).strip('_') or f'texture_{index}'


def _texture_roles(material_defs):
    # A texture used as normal map anywhere is a normal map, otherwise color if any use is color, else mask
    roles = {}
    for material_def in material_defs:
        for source in (material_def, *material_def.get('layers', []), material_def.get('paint') or {}):
            for key, tex_idx in source.items():
                if not key.endswith('Texture'):
                    continue
                if key in NORMAL_KEYS:
                    roles[tex_idx] = 'normal'
                elif key in COLOR_KEYS and roles.get(tex_idx) != 'normal':
                    roles[tex_idx] = 'color'
                elif tex_idx not in roles:
                    roles[tex_idx] = 'mask'
    return roles


def _set_texture_settings(texture, role, refresh):
    if role == 'normal':
        texture.set_editor_property('srgb', False)
        texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_NORMALMAP)
    elif role == 'mask':
        texture.set_editor_property('srgb', False)
        texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_MASKS)
    else:
        texture.set_editor_property('srgb', True)
        texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_DEFAULT)
    # update_resource flushes the render thread. Skip it when the caller is already inside a slate tick.
    if not refresh:
        return
    update_resource = getattr(texture, 'update_resource', None)
    if update_resource is not None:
        update_resource()


def _import_texture(filepath, destination_path, destination_name):
    asset_path = f'{destination_path}/{destination_name}'
    task = unreal.AssetImportTask()
    task.set_editor_property('filename', filepath)
    task.set_editor_property('destination_path', destination_path)
    task.set_editor_property('destination_name', destination_name)
    task.set_editor_property('automated', True)
    task.set_editor_property('replace_existing', True)
    task.set_editor_property('save', False)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    return unreal.load_asset(asset_path)


def import_textures(vehicle_id, texture_defs, material_defs, out_dir, refresh=True, save=False):
    os.makedirs(out_dir, exist_ok=True)

    roles = _texture_roles(material_defs)
    textures = []
    for index, texture_def in enumerate(texture_defs):
        key = texture_def['key']
        content_hash = hashlib.sha1(texture_def['data']).hexdigest() if save else None
        cache_key = (key, content_hash) if content_hash else key
        texture = _texture_cache.get(cache_key)
        imported = False
        if texture is None:
            name = _asset_name(texture_def['name'], index)
            # Dynamic textures are keyed '<vehicle id>:<path>' and stay per vehicle
            shared = not key.startswith(f'{vehicle_id}:')
            if shared:
                destination_path = f'{GENERATED_ROOT}/Shared/Textures'
                destination_name = f'T_{index}_{name}_{hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]}'
            else:
                destination_path = f'{GENERATED_ROOT}/Vehicle_{vehicle_id}/Textures'
                destination_name = f'T_{index}_{name}'
            if content_hash:
                destination_name += f'_{content_hash[:12]}'
            asset_path = f'{destination_path}/{destination_name}'
            if (shared or content_hash) and unreal.EditorAssetLibrary.does_asset_exist(asset_path):
                texture = unreal.load_asset(asset_path)
            else:
                unreal.EditorAssetLibrary.make_directory(destination_path)
                extension = '.png' if texture_def['mime'] == 'image/png' else '.jpg'
                filepath = os.path.join(out_dir, f'{index}_{name}{extension}')
                with open(filepath, 'wb') as f:
                    f.write(texture_def['data'])
                texture = _import_texture(filepath, destination_path, destination_name)
                if texture is None:
                    raise RuntimeError(f'Could not import texture {filepath}')
                _set_texture_settings(texture, roles.get(index, 'color'), refresh)
                imported = True
            _texture_cache[cache_key] = texture
        if save and imported and not unreal.EditorAssetLibrary.save_loaded_asset(texture, only_if_is_dirty=False):
            raise RuntimeError(f'Could not save texture {texture.get_path_name()}')
        textures.append(texture)
    return textures


def update_textures(texture_assets, updates):
    for update in updates:
        data = np.frombuffer(update['data'], dtype=np.uint8)
        unreal.BngLinkBridge.update_texture(texture_assets[update['index']], int(data.ctypes.data), int(data.size))

