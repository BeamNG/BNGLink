from mathutils import Matrix

BNG_TO_ROOT = Matrix((
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, -1.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
))
BNG_TO_ROOT_INV = BNG_TO_ROOT.inverted()


def mat12_to_matrix(m12):
    return Matrix((
        (m12[0], m12[1], m12[2], m12[3]),
        (m12[4], m12[5], m12[6], m12[7]),
        (m12[8], m12[9], m12[10], m12[11]),
        (0.0, 0.0, 0.0, 1.0),
    ))


def prop_offset(bind_m12, live_init_m12):
    return mat12_to_matrix(bind_m12) @ mat12_to_matrix(live_init_m12).inverted()


def prop_root_local(live_bng):
    return BNG_TO_ROOT_INV @ live_bng @ BNG_TO_ROOT
