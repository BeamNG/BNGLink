# Skinning data model and the numba kernels that run on it every frame.

import math

import numpy as np
from numba import njit, prange

SKIN_DTYPE = np.dtype([('nodes', '<i4', 4), ('coords', '<f4', 3)])

SKIN_FIELDS = ('ref_idx', 'px_idx', 'py_idx', 'pz_idx', 'pz_valid', 'coords_clipped', 'coords_residual')
SKIN_NODE_FIELDS = ('ref_idx', 'px_idx', 'py_idx', 'pz_idx')

RIGID_KEEP = 0
RIGID_MOVED = 1
RIGID_RESKIN = 2
# 3x4 affine for deform_skin() callers that want the raw deformed position
IDENTITY_XFORM = np.concatenate((np.eye(3), np.zeros((3, 1))), axis=1)
RIGID_MAX_ITERATIONS = 20
# The vehicle frame is refitted on this fraction of the best-matching nodes, so nodes moving against the body (wheels, suspension) do not pull it
RIGID_FRAME_KEEP = 0.5
RIGID_FRAME_PASSES = 2


def merge_part_skins(parts):
    # Concatenates the SKIN_FIELDS of decoded parts so a whole body deforms with one deform_skin() call.
    # Returns (merged, ranges, total); ranges[i] is the (start, stop) row span of parts[i] in the merged output.
    # merged is None when parts is empty.
    ranges = []
    total = 0
    for part in parts:
        count = int(part['ref_idx'].shape[0])
        ranges.append((total, total + count))
        total += count
    if not parts:
        return None, ranges, total
    merged = {field: np.concatenate([part[field] for part in parts]) for field in SKIN_FIELDS}
    return merged, ranges, total


def face_locator_nodes(parts):
    face_nodes = []
    for part in parts:
        nodes = np.column_stack((
            part['ref_idx'],
            part['px_idx'],
            part['py_idx'],
            np.where(part['pz_valid'], part['pz_idx'], -1),
        ))
        face_nodes.append(nodes[part['faces']].reshape(-1, 12))
    return np.concatenate(face_nodes) if face_nodes else np.empty((0, 12), dtype=np.int32)


def build_face_node_index(locator_nodes):
    if locator_nodes.size == 0:
        return np.empty(0, dtype=np.int32), np.zeros(1, dtype=np.int32)

    width = locator_nodes.shape[1]
    nodes = locator_nodes.reshape(-1)
    valid = nodes >= 0
    nodes = nodes[valid]
    faces = np.repeat(np.arange(len(locator_nodes), dtype=np.int32), width)[valid]
    order = np.argsort(nodes, kind='stable')
    nodes = nodes[order]
    faces = faces[order]

    keep = np.empty(len(nodes), dtype=bool)
    keep[0] = True
    keep[1:] = (nodes[1:] != nodes[:-1]) | (faces[1:] != faces[:-1])
    nodes = nodes[keep]
    faces = np.ascontiguousarray(faces[keep])
    offsets = np.zeros(int(nodes[-1]) + 2, dtype=np.int32)
    offsets[1:] = np.cumsum(np.bincount(nodes, minlength=len(offsets) - 1))
    return faces, offsets


def _hide_broken_faces(face_node_index, broken_beams, hidden):
    node_faces, offsets = face_node_index
    node_count = len(offsets) - 1
    changed = False
    for node1, node2 in broken_beams:
        if node1 < 0 or node2 < 0 or node1 >= node_count or node2 >= node_count:
            continue
        first = node_faces[offsets[node1]:offsets[node1 + 1]]
        second = node_faces[offsets[node2]:offsets[node2 + 1]]
        faces = np.intersect1d(first, second, assume_unique=True)
        if not changed and not np.all(hidden[faces]):
            changed = True
        hidden[faces] = True
    return changed


def update_hidden_faces(face_node_index, previous_beams, broken_beams, hidden):
    if not previous_beams.issubset(broken_beams):
        previous = hidden.copy()
        hidden.fill(False)
        _hide_broken_faces(face_node_index, broken_beams, hidden)
        return not np.array_equal(previous, hidden)
    return _hide_broken_faces(face_node_index, broken_beams - previous_beams, hidden)


def part_node_sets(skin, ranges):
    # Unique node indices each part's vertices depend on, flattened: part p owns node_idx[node_start[p]:node_start[p + 1]].
    sets = [
        np.unique(np.concatenate([skin[field][start:stop] for field in SKIN_NODE_FIELDS]))
        for start, stop in ranges
    ]
    node_start = np.zeros(len(sets) + 1, dtype=np.int64)
    node_start[1:] = np.cumsum([len(nodes) for nodes in sets])
    node_idx = np.concatenate(sets).astype(np.int32) if sets else np.empty(0, dtype=np.int32)
    return node_idx, node_start


@njit(parallel=True, fastmath=True, cache=True)
def deform_skin(node_pos, ref, px, py, pz, pz_valid, clip, resid, xform, out):
    # Writes xform @ (x, z, -y, 1):
    # the platform axis flip and the 3x4 affine are baked in to avoid a second per-frame pass over out, which costs more than the deform itself
    m = xform
    for v in prange(ref.shape[0]):
        r = ref[v]
        rx, ry, rz = node_pos[r, 0], node_pos[r, 1], node_pos[r, 2]
        ax, ay, az = node_pos[px[v], 0] - rx, node_pos[px[v], 1] - ry, node_pos[px[v], 2] - rz
        bx, by, bz = node_pos[py[v], 0] - rx, node_pos[py[v], 1] - ry, node_pos[py[v], 2] - rz
        if pz_valid[v]:
            cx, cy, cz = node_pos[pz[v], 0] - rx, node_pos[pz[v], 1] - ry, node_pos[pz[v], 2] - rz
        else:
            cx, cy, cz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
            inv = 1.0 / (math.sqrt(cx * cx + cy * cy + cz * cz) + 1e-6)
            cx, cy, cz = cx * inv, cy * inv, cz * inv
        fx = clip[v, 0] + resid[v, 0] / (math.sqrt(ax * ax + ay * ay + az * az) + 1e-6)
        fy = clip[v, 1] + resid[v, 1] / (math.sqrt(bx * bx + by * by + bz * bz) + 1e-6)
        fz = clip[v, 2] + resid[v, 2] / (math.sqrt(cx * cx + cy * cy + cz * cz) + 1e-6)
        x = rx + ax * fx + bx * fy + cx * fz
        y = rz + az * fx + bz * fy + cz * fz
        z = -(ry + ay * fx + by * fy + cy * fz)
        out[v, 0] = m[0, 0] * x + m[0, 1] * y + m[0, 2] * z + m[0, 3]
        out[v, 1] = m[1, 0] * x + m[1, 1] * y + m[1, 2] * z + m[1, 3]
        out[v, 2] = m[2, 0] * x + m[2, 1] * y + m[2, 2] * z + m[2, 3]


@njit(fastmath=True, cache=True)
def _orthonormal_axes(pos, r, ix, iy, iz, z_valid):
    # The locator axes of deform_skin, Gram-Schmidt'd into a rotation-like frame
    rx, ry, rz = pos[r, 0], pos[r, 1], pos[r, 2]
    ax, ay, az = pos[ix, 0] - rx, pos[ix, 1] - ry, pos[ix, 2] - rz
    bx, by, bz = pos[iy, 0] - rx, pos[iy, 1] - ry, pos[iy, 2] - rz
    if z_valid:
        cx, cy, cz = pos[iz, 0] - rx, pos[iz, 1] - ry, pos[iz, 2] - rz
    else:
        cx, cy, cz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
    inv = 1.0 / (math.sqrt(ax * ax + ay * ay + az * az) + 1e-6)
    ax, ay, az = ax * inv, ay * inv, az * inv
    d = bx * ax + by * ay + bz * az
    bx, by, bz = bx - ax * d, by - ay * d, bz - az * d
    inv = 1.0 / (math.sqrt(bx * bx + by * by + bz * bz) + 1e-6)
    bx, by, bz = bx * inv, by * inv, bz * inv
    d = cx * ax + cy * ay + cz * az
    cx, cy, cz = cx - ax * d, cy - ay * d, cz - az * d
    d = cx * bx + cy * by + cz * bz
    cx, cy, cz = cx - bx * d, cy - by * d, cz - bz * d
    sq = cx * cx + cy * cy + cz * cz
    if sq < 1e-12:
        cx, cy, cz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
        sq = cx * cx + cy * cy + cz * cz
    inv = 1.0 / (math.sqrt(sq) + 1e-6)
    return ax, ay, az, bx, by, bz, cx * inv, cy * inv, cz * inv


@njit(parallel=True, fastmath=True, cache=True)
def deform_normals(node_pos, ref_nodes, ref, px, py, pz, pz_valid, normals, out):
    # n' = U_now * U_rest^T * n, the locator frame rotation from the ref_nodes pose to
    # the current one; positions also shear, which normals approximate as pure rotation
    for v in prange(ref.shape[0]):
        rest = _orthonormal_axes(ref_nodes, ref[v], px[v], py[v], pz[v], pz_valid[v])
        now = _orthonormal_axes(node_pos, ref[v], px[v], py[v], pz[v], pz_valid[v])
        x, y, z = normals[v, 0], normals[v, 1], normals[v, 2]
        fx = rest[0] * x + rest[1] * y + rest[2] * z
        fy = rest[3] * x + rest[4] * y + rest[5] * z
        fz = rest[6] * x + rest[7] * y + rest[8] * z
        x = now[0] * fx + now[3] * fy + now[6] * fz
        y = now[1] * fx + now[4] * fy + now[7] * fz
        z = now[2] * fx + now[5] * fy + now[8] * fz
        inv = 1.0 / (math.sqrt(x * x + y * y + z * z) + 1e-6)
        out[v, 0], out[v, 1], out[v, 2] = x * inv, y * inv, z * inv


@njit(fastmath=True)
def positions_changed(positions, previous, epsilon):
    changed = False
    for i in range(positions.shape[0]):
        if (abs(positions[i, 0] - previous[i, 0]) > epsilon
                or abs(positions[i, 1] - previous[i, 1]) > epsilon
                or abs(positions[i, 2] - previous[i, 2]) > epsilon):
            changed = True
            break

    if changed:
        for i in range(positions.shape[0]):
            previous[i, 0] = positions[i, 0]
            previous[i, 1] = positions[i, 1]
            previous[i, 2] = positions[i, 2]
    return changed

@njit(fastmath=True, cache=True)
def nodes_to_skin_axes(node_pos, out):
    for i in range(node_pos.shape[0]):
        out[i, 0] = node_pos[i, 0]
        out[i, 1] = node_pos[i, 2]
        out[i, 2] = -node_pos[i, 1]


@njit(fastmath=True, cache=True)
def quat_rotation(q, r):
    w, x, y, z = q[0], q[1], q[2], q[3]
    r[0, 0] = 1.0 - 2.0 * (y * y + z * z)
    r[0, 1] = 2.0 * (x * y - w * z)
    r[0, 2] = 2.0 * (x * z + w * y)
    r[1, 0] = 2.0 * (x * y + w * z)
    r[1, 1] = 1.0 - 2.0 * (x * x + z * z)
    r[1, 2] = 2.0 * (y * z - w * x)
    r[2, 0] = 2.0 * (x * z - w * y)
    r[2, 1] = 2.0 * (y * z + w * x)
    r[2, 2] = 1.0 - 2.0 * (x * x + y * y)


@njit(fastmath=True, cache=True)
def _fit_rotation(a, q, r):
    # Refines the warm-start quaternion q (w, x, y, z) towards the rotation maximizing trace(R^T a).
    # Mueller et al. 2016, "A Robust Method to Extract the Rotational Part of Deformations".
    for _ in range(RIGID_MAX_ITERATIONS):
        quat_rotation(q, r)
        ox = 0.0
        oy = 0.0
        oz = 0.0
        d = 0.0
        for c in range(3):
            rx, ry, rz = r[0, c], r[1, c], r[2, c]
            ax, ay, az = a[0, c], a[1, c], a[2, c]
            ox += ry * az - rz * ay
            oy += rz * ax - rx * az
            oz += rx * ay - ry * ax
            d += rx * ax + ry * ay + rz * az
        inv = 1.0 / (abs(d) + 1e-12)
        ox, oy, oz = ox * inv, oy * inv, oz * inv
        angle = math.sqrt(ox * ox + oy * oy + oz * oz)
        if angle < 1e-9:
            break
        s = math.sin(angle * 0.5) / angle
        dw, dx, dy, dz = math.cos(angle * 0.5), ox * s, oy * s, oz * s
        w, x, y, z = q[0], q[1], q[2], q[3]
        q0 = dw * w - dx * x - dy * y - dz * z
        q1 = dw * x + dx * w + dy * z - dz * y
        q2 = dw * y - dx * z + dy * w + dz * x
        q3 = dw * z + dx * y - dy * x + dz * w
        n = 1.0 / math.sqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3)
        q[0], q[1], q[2], q[3] = q0 * n, q1 * n, q2 * n, q3 * n
    quat_rotation(q, r)


@njit(fastmath=True, cache=True)
def _fit_rigid(ref, cur, idx, q, t, a, r):
    # Best rigid transform (q, t) mapping ref[i] onto cur[idx[i]]
    k = idx.shape[0]
    rcx = rcy = rcz = 0.0
    ccx = ccy = ccz = 0.0
    for i in range(k):
        n = idx[i]
        rcx += ref[i, 0]
        rcy += ref[i, 1]
        rcz += ref[i, 2]
        ccx += cur[n, 0]
        ccy += cur[n, 1]
        ccz += cur[n, 2]
    inv = 1.0 / k
    rcx, rcy, rcz = rcx * inv, rcy * inv, rcz * inv
    ccx, ccy, ccz = ccx * inv, ccy * inv, ccz * inv

    a[:, :] = 0.0
    for i in range(k):
        n = idx[i]
        cx, cy, cz = cur[n, 0] - ccx, cur[n, 1] - ccy, cur[n, 2] - ccz
        px, py, pz = ref[i, 0] - rcx, ref[i, 1] - rcy, ref[i, 2] - rcz
        a[0, 0] += cx * px
        a[0, 1] += cx * py
        a[0, 2] += cx * pz
        a[1, 0] += cy * px
        a[1, 1] += cy * py
        a[1, 2] += cy * pz
        a[2, 0] += cz * px
        a[2, 1] += cz * py
        a[2, 2] += cz * pz

    _fit_rotation(a, q, r)
    t[0] = ccx - (r[0, 0] * rcx + r[0, 1] * rcy + r[0, 2] * rcz)
    t[1] = ccy - (r[1, 0] * rcx + r[1, 1] * rcy + r[1, 2] * rcz)
    t[2] = ccz - (r[2, 0] * rcx + r[2, 1] * rcy + r[2, 2] * rcz)


@njit(fastmath=True, cache=True)
def _error_sq(r, t, ref, i, cur, n):
    x, y, z = ref[i, 0], ref[i, 1], ref[i, 2]
    dx = r[0, 0] * x + r[0, 1] * y + r[0, 2] * z + t[0] - cur[n, 0]
    dy = r[1, 0] * x + r[1, 1] * y + r[1, 2] * z + t[1] - cur[n, 1]
    dz = r[2, 0] * x + r[2, 1] * y + r[2, 2] * z + t[2] - cur[n, 2]
    return dx * dx + dy * dy + dz * dz


@njit(fastmath=True, cache=True)
def _max_error(ref, cur, idx, q, t, r):
    # Largest distance between (q, t) applied to ref[i] and cur[idx[i]]
    quat_rotation(q, r)
    worst = 0.0
    for i in range(idx.shape[0]):
        e = _error_sq(r, t, ref, i, cur, idx[i])
        if e > worst:
            worst = e
    return math.sqrt(worst)


@njit(fastmath=True, cache=True)
def _fit_vehicle_frame(vehicle_ref, nodes, q, t, a, r):
    idx = np.arange(nodes.shape[0])
    _fit_rigid(vehicle_ref, nodes, idx, q, t, a, r)
    errors = np.empty(nodes.shape[0])
    keep = max(3, int(nodes.shape[0] * RIGID_FRAME_KEEP))
    for _ in range(RIGID_FRAME_PASSES):
        quat_rotation(q, r)
        for i in range(nodes.shape[0]):
            errors[i] = _error_sq(r, t, vehicle_ref, i, nodes, i)
        subset = np.sort(np.argsort(errors)[:keep])
        _fit_rigid(vehicle_ref[subset], nodes, subset, q, t, a, r)


@njit(fastmath=True, cache=True)
def fit_vehicle_frame(vehicle_ref, nodes, q, t):
    a = np.empty((3, 3))
    r = np.empty((3, 3))
    _fit_vehicle_frame(vehicle_ref, nodes, q, t, a, r)


@njit(fastmath=True, cache=True)
def to_vehicle_space(points, q, t, out):
    # out = R^T (points - t); in place is allowed
    r = np.empty((3, 3))
    quat_rotation(q, r)
    for i in range(points.shape[0]):
        x = points[i, 0] - t[0]
        y = points[i, 1] - t[1]
        z = points[i, 2] - t[2]
        out[i, 0] = r[0, 0] * x + r[1, 0] * y + r[2, 0] * z
        out[i, 1] = r[0, 1] * x + r[1, 1] * y + r[2, 1] * z
        out[i, 2] = r[0, 2] * x + r[1, 2] * y + r[2, 2] * z


@njit(fastmath=True, cache=True)
def vehicle_space_xform(q, t, out):
    # The 3x4 form of to_vehicle_space: [R^T | -R^T t]
    r = np.empty((3, 3))
    quat_rotation(q, r)
    for i in range(3):
        out[i, 0] = r[0, i]
        out[i, 1] = r[1, i]
        out[i, 2] = r[2, i]
        out[i, 3] = -(r[0, i] * t[0] + r[1, i] * t[1] + r[2, i] * t[2])


@njit(fastmath=True, cache=True)
def rigidify(node_pos, vehicle_ref, vehicle_q, vehicle_t, nodes, node_idx, node_start,
             ref_nodes, part_q, part_t, tolerance, modes):
    # Fits the vehicle frame (vehicle_q, vehicle_t) against vehicle_ref, writes the current nodes
    # in vehicle space to nodes, then classifies every part into modes:
    #   RIGID_KEEP:   its current transform still matches its nodes within tolerance
    #   RIGID_MOVED:  part_q/part_t were refitted and match within tolerance
    #   RIGID_RESKIN: it deformed; ref_nodes and its transform are reset, the caller re-uploads it in vehicle space
    a = np.empty((3, 3))
    r = np.empty((3, 3))
    nodes_to_skin_axes(node_pos, nodes)
    _fit_vehicle_frame(vehicle_ref, nodes, vehicle_q, vehicle_t, a, r)
    to_vehicle_space(nodes, vehicle_q, vehicle_t, nodes)

    for p in range(modes.shape[0]):
        s, e = node_start[p], node_start[p + 1]
        if s == e:
            modes[p] = RIGID_KEEP
            continue
        ref = ref_nodes[s:e]
        idx = node_idx[s:e]
        if _max_error(ref, nodes, idx, part_q[p], part_t[p], r) < tolerance:
            modes[p] = RIGID_KEEP
            continue
        _fit_rigid(ref, nodes, idx, part_q[p], part_t[p], a, r)
        if _max_error(ref, nodes, idx, part_q[p], part_t[p], r) < tolerance:
            modes[p] = RIGID_MOVED
            continue
        modes[p] = RIGID_RESKIN
        for i in range(e - s):
            n = idx[i]
            ref[i, 0] = nodes[n, 0]
            ref[i, 1] = nodes[n, 1]
            ref[i, 2] = nodes[n, 2]
        part_q[p, 0] = 1.0
        part_q[p, 1] = 0.0
        part_q[p, 2] = 0.0
        part_q[p, 3] = 0.0
        part_t[p, 0] = 0.0
        part_t[p, 1] = 0.0
        part_t[p, 2] = 0.0
