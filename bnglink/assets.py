import copy
import hashlib
import json


class MissingAsset(Exception):
    def __init__(self, vehicle_id, key):
        super().__init__(f'missing asset {key} for vehicle {vehicle_id}')
        self.vehicle_id = vehicle_id
        self.key = key


class AssetCache:
    # Geometry templates and textures by content key. BeamNG only sends the key once the receiver confirmed it,
    # so a miss means the receiver restarted and asks for a full init.
    def __init__(self):
        self.geometries = {}
        self.textures = {}

    def store_geometry(self, key, template):
        self.geometries[key] = template

    def geometry(self, vehicle_id, key):
        return _lookup(self.geometries, vehicle_id, key)

    def store_texture(self, key, texture):
        self.textures[key] = texture

    def texture(self, vehicle_id, key):
        return _lookup(self.textures, vehicle_id, key)


def _lookup(store, vehicle_id, key):
    if key not in store:
        raise MissingAsset(vehicle_id, key)
    return store[key]


def _replace_texture_indices(value, texture_keys):
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key.endswith('Texture') and isinstance(item, int) and 0 <= item < len(texture_keys):
                value[key] = texture_keys[item]
            else:
                _replace_texture_indices(item, texture_keys)
    elif isinstance(value, list):
        for item in value:
            _replace_texture_indices(item, texture_keys)


def material_key(material_def, texture_keys):
    # Content hash of a material with texture indices replaced by texture keys, so identical materials of different vehicles share one platform material
    data = copy.deepcopy(material_def)
    _replace_texture_indices(data, texture_keys)
    encoded = json.dumps(data, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha1(encoded).hexdigest()
