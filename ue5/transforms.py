import numpy as np
import unreal
from numba import njit, prange

from bnglink import quat_rotation

BNG_SCALE = 100.0

BNG_TO_UE_4D = np.array((
    (0.0, 1.0, 0.0, 0.0),
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
), dtype=np.float64)

PROP_AXIS_CORRECTION = np.array((
    (0.0, 0.0, -1.0),
    (0.0, 1.0, 0.0),
    (1.0, 0.0, 0.0),
), dtype=np.float64)


def _permute_scale(value, axes, signs):
    v = np.asarray(value, dtype=np.float64)
    return v[..., axes] * signs * BNG_SCALE


def bng_to_unreal(value):
    # Works for a single (3,) vector or an (N, 3) array of points.
    return _permute_scale(value, (1, 0, 2), (1.0, 1.0, 1.0))


@njit(fastmath=True, cache=True)
def nodes_to_unreal(node_pos, out):
    # Same mapping as bng_to_unreal
    for i in range(node_pos.shape[0]):
        out[i, 0] = node_pos[i, 1] * BNG_SCALE
        out[i, 1] = node_pos[i, 0] * BNG_SCALE
        out[i, 2] = node_pos[i, 2] * BNG_SCALE


@njit(parallel=True, fastmath=True, cache=True)
def skin_to_vehicle_space(skin_pos, source, q, t, out):
    # out[i] = R^T (bng_to_unreal(skin vertex source[i]) - t),
    # (q, t) being the vehicle frame in Unreal axes, deform_skin writes (x, z, -y)
    r = np.empty((3, 3))
    quat_rotation(q, r)
    for i in prange(source.shape[0]):
        s = source[i]
        x = -skin_pos[s, 2] * BNG_SCALE - t[0]
        y = skin_pos[s, 0] * BNG_SCALE - t[1]
        z = skin_pos[s, 1] * BNG_SCALE - t[2]
        out[i, 0] = r[0, 0] * x + r[1, 0] * y + r[2, 0] * z
        out[i, 1] = r[0, 1] * x + r[1, 1] * y + r[2, 1] * z
        out[i, 2] = r[0, 2] * x + r[1, 2] * y + r[2, 2] * z


def directions_bng_to_unreal(value):
    return np.asarray(value, dtype=np.float64)[..., (1, 0, 2)]


def tangents_bng_to_unreal(value):
    source = np.asarray(value, dtype=np.float64)
    result = np.empty_like(source)
    result[..., :3] = directions_bng_to_unreal(source[..., :3])
    result[..., 3] = -source[..., 3]
    return result


def relative_location_to_unreal(origin, reference):
    return unreal.Vector(
        float((origin[1] - reference[1]) * BNG_SCALE),
        float((origin[0] - reference[0]) * BNG_SCALE),
        float((origin[2] - reference[2]) * BNG_SCALE),
    )


def mat12_to_matrix(m12):
    m = np.asarray(m12, dtype=np.float64)
    return np.array((
        (m[0], m[1], m[2], m[3]),
        (m[4], m[5], m[6], m[7]),
        (m[8], m[9], m[10], m[11]),
        (0.0, 0.0, 0.0, 1.0),
    ), dtype=np.float64)


def prop_offset(bind_m12, live_init_m12):
    return mat12_to_matrix(bind_m12) @ np.linalg.inv(mat12_to_matrix(live_init_m12))


def prop_matrices_batch(prop_mats, offsets):
    # Live Matrix12 rows as (P, 4, 4) matrices with the per-prop offset applied in one matmul
    count = prop_mats.shape[0]
    live = np.zeros((count, 4, 4), dtype=np.float64)
    live[:, :3, :] = prop_mats.reshape(count, 3, 4)
    live[:, 3, 3] = 1.0
    return offsets @ live


def ue_prop_matrices(prop_mats, offsets):
    # Live prop matrices in Unreal axes and units, with the prop mesh axis correction
    ue = BNG_TO_UE_4D @ prop_matrices_batch(prop_mats, offsets) @ BNG_TO_UE_4D
    ue[:, :3, :3] = ue[:, :3, :3] @ PROP_AXIS_CORRECTION
    ue[:, :3, 3] *= BNG_SCALE
    return ue
