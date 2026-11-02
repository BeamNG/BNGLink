import numpy as np
from numba import njit, prange

BNG_SCALE = 100.0
INVALID_NODE = np.uint32(0xffffffff)

SKIN_VERTEX_DTYPE = np.dtype([
    ('nodes', '<u4', 4),
    ('coords', '<f4', 3),
    ('residual', '<f4', 3),
    ('normal', '<f4', 3),
    ('tangent', '<f4', 3),
    ('binormal_sign', '<f4'),
])




@njit(parallel=True, fastmath=True, cache=True)
def _frame_coordinates(nodes, refs, pxs, pys, values, out):
    for i in prange(refs.shape[0]):
        r = refs[i]
        px = pxs[i]
        py = pys[i]
        ax = nodes[px, 0] - nodes[r, 0]
        ay = nodes[px, 1] - nodes[r, 1]
        az = nodes[px, 2] - nodes[r, 2]
        bx = nodes[py, 0] - nodes[r, 0]
        by = nodes[py, 1] - nodes[r, 1]
        bz = nodes[py, 2] - nodes[r, 2]
        cx = by * az - bz * ay
        cy = bz * ax - bx * az
        cz = bx * ay - by * ax
        clen = (cx * cx + cy * cy + cz * cz) ** 0.5
        if clen <= 1e-12:
            out[i, 0] = 0.0
            out[i, 1] = 0.0
            out[i, 2] = 0.0
            continue
        cx /= clen
        cy /= clen
        cz /= clen

        det = ax * (by * cz - bz * cy) - bx * (ay * cz - az * cy) + cx * (ay * bz - az * by)
        if abs(det) <= 1e-12:
            out[i, 0] = 0.0
            out[i, 1] = 0.0
            out[i, 2] = 0.0
            continue
        inv = 1.0 / det
        vx = values[i, 0]
        vy = values[i, 1]
        vz = values[i, 2]
        out[i, 0] = ((by * cz - bz * cy) * vx + (bz * cx - bx * cz) * vy + (bx * cy - by * cx) * vz) * inv
        out[i, 1] = ((cy * az - cz * ay) * vx + (cz * ax - cx * az) * vy + (cx * ay - cy * ax) * vz) * inv
        out[i, 2] = ((ay * bz - az * by) * vx + (az * bx - ax * bz) * vy + (ax * by - ay * bx) * vz) * inv


def build_skin_vertices(skin, source, normals, tangents, vehicle_nodes):
    source = np.asarray(source, dtype=np.int32)
    records = np.empty(len(source), dtype=SKIN_VERTEX_DTYPE)
    records['nodes'][:, 0] = skin['ref_idx'][source].astype(np.uint32)
    records['nodes'][:, 1] = skin['px_idx'][source].astype(np.uint32)
    records['nodes'][:, 2] = skin['py_idx'][source].astype(np.uint32)
    records['nodes'][:, 3] = skin['pz_idx'][source].astype(np.uint32)
    records['nodes'][~skin['pz_valid'][source], 3] = INVALID_NODE

    coords = records['coords']
    coords[:] = skin['coords_clipped'][source]
    coords[~skin['pz_valid'][source], 2] *= BNG_SCALE

    records['residual'] = skin['coords_residual'][source] * BNG_SCALE

    nodes = np.asarray(vehicle_nodes, dtype=np.float32)
    refs = records['nodes'][:, 0].astype(np.int32)
    pxs = records['nodes'][:, 1].astype(np.int32)
    pys = records['nodes'][:, 2].astype(np.int32)
    _frame_coordinates(nodes, refs, pxs, pys, np.asarray(normals, dtype=np.float32), records['normal'])
    _frame_coordinates(nodes, refs, pxs, pys, np.asarray(tangents[:, :3], dtype=np.float32), records['tangent'])
    records['binormal_sign'] = np.where(tangents[:, 3] < 0.0, -1.0, 1.0).astype(np.float32)
    return np.ascontiguousarray(records)
