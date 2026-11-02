# Stage conventions shared by the take writer and reader. The stage is Z-up BeamNG node space,
# decoded meshes are in skin axes (x, z, -y).

import numpy as np

ROOT_PATH = '/BeamNG'
PAINT_SCALARS = ('metallic', 'roughness', 'clearcoat', 'clearcoatRoughness')


def skin_to_world(points):
    points = np.asarray(points)
    return np.stack((points[..., 0], -points[..., 2], points[..., 1]), axis=-1)


def world_to_skin(points):
    points = np.asarray(points)
    return np.stack((points[..., 0], points[..., 2], -points[..., 1]), axis=-1)


def flip_uv_v(uvs):
    uvs = np.array(uvs, dtype=np.float32).reshape(-1, 2)
    uvs[:, 1] = 1.0 - uvs[:, 1]
    return uvs
