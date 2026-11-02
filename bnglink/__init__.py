# Shared BeamNG link code, reused as-is across platforms (Blender, Unreal, ...).
# Platform endpoints live in blender/ and ue5/.
#   transport: packet header, message kinds, UdpComm (UDP receive + chunk reassembly)
#   assets:    receiver-side cache of geometry and textures shared between inits
#   decoder:   BngDecoder, payloads -> platform-neutral data in BeamNG display space
#   skinning:  skin data model and the per-frame numba kernels
#   stats:     RateStats debug throughput report, bench lap timer
#   endpoint:  LinkSession, session bootstrap + message dispatch
# Tests: python -m unittest discover -s tests -p "test_*.py"
#   tests/test_protocol.py covers this package and the ue5 mesh/transform/skin math,
#   tests/test_sender.py runs the Lua sender against it

from .assets import AssetCache, MissingAsset, material_key
from .decoder import BngDecoder
from .endpoint import LinkSession
from .material_state import MaterialState, build_damage_variant_index
from .skinning import (
    IDENTITY_XFORM,
    RIGID_KEEP,
    RIGID_MOVED,
    RIGID_RESKIN,
    SKIN_DTYPE,
    SKIN_FIELDS,
    build_face_node_index,
    deform_skin,
    face_locator_nodes,
    fit_vehicle_frame,
    merge_part_skins,
    nodes_to_skin_axes,
    part_node_sets,
    positions_changed,
    quat_rotation,
    rigidify,
    to_vehicle_space,
    update_hidden_faces,
    vehicle_space_xform,
)
from .stats import RateStats, lap
from .transport import (
    CONTROL_MESSAGES,
    HEADER,
    HEADER_MAGIC,
    KIND_BULK,
    KIND_CONTROL,
    KIND_STREAM,
    KIND_UPDATE,
    UdpComm,
)
