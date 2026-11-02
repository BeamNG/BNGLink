# BNGLink USD Take Format

Reference for the `.usdz` takes recorded by BNGLink and read by the bundled importers.

A take stores the recorded vehicles in a consumer-neutral form: standard USD meshes and transforms, plus `bng:` attributes holding the BeamNG data an importer needs to rebuild the live-link scene (skinning, materials, damage, lights, paints and texture changes).

- [Package](#package)
- [Stage](#stage)
- [Prim hierarchy](#prim-hierarchy)
- [Vehicle segments](#vehicle-segments)
- [Meshes](#meshes)
- [Materials and textures](#materials-and-textures)
- [Bundled importers](#bundled-importers)

## Package

A take is a USDZ package:

```
bnglink_take_<date>_<time>.usdz
├── take.usdc                                        root layer
└── textures/
    └── <segment name>/
        └── <index>_<name>_v<version>.<png|jpg>      one file per texture version
```

- `<segment name>` is the vehicle segment prim name, for example `Vehicle_32575`.
- `<index>` is the texture's index in the init `textures[]`, zero-padded to four digits.
- `<version>` starts at `0` and increases each time a dynamic texture changes during the take.

## Stage

| Setting | Value |
| --- | --- |
| Default prim | `/BeamNG` |
| `upAxis` | `Z` |
| `metersPerUnit` | `1` |
| `framesPerSecond`, `timeCodesPerSecond` | The capture rate, 1000 by default |
| `startTimeCode` | `1` |
| `endTimeCode` | Last recorded frame |

### Time base

Samples are placed on BeamNG's simulation clock, not on the time they were received:

- The first recorded sync defines time zero, at frame 1.
- A sync `t` simulation seconds later lands on frame `1 + round(t * captureFps)`.
- When two syncs fall on the same frame, the newest one is kept.

Because the simulation clock stops while BeamNG is paused and slows down in slow motion, takes are always in real-world time. The capture rate only sets the time resolution, it is not a playback rate.

The playback frame rate is chosen when the take is read. `bngusd.reader.Take(path, fps=24)` serves frames on a 24 fps timeline, each sampling the nearest capture frame. Both bundled importers take the same `fps` argument.

### Coordinate conventions

- All positions are in meters, Z up, in BeamNG's world axes.
- **Vehicles are stored relative to their own position.** Vehicle prims have no transform op: node positions, deformed points and prop transforms are relative to the vehicle position sampled in `bng:origin`. Translate by `bng:origin` to get world positions.
- UVs follow the USD convention: `st = (u, 1 - v)` of the BeamNG UVs.
- Meshes are triangle lists.

## Prim hierarchy

```
/BeamNG                                  Xform, default prim
├── Materials                            Xform
│   └── Vehicle_<id>                     Xform
│       └── <material name>              Material
├── Vehicle_<id>                         Xform, vehicle segment
│   ├── Body                             Xform
│   │   └── <part name>                  Mesh, skinned body part
│   ├── Nodes                            Points, skinned takes only
│   └── Props                            Xform
│       └── <prop name>_<index>          Xform, time-sampled matrix
│           └── Mesh                     Mesh
└── Vehicle_<id>_1                       Later segment of the same vehicle
```

Prim names are sanitized: characters other than letters, digits and `_` become `_`, and a leading digit gets a `_` prefix. The original names are kept in `bng:name`.

## Vehicle segments

A segment is one vehicle from its init until it is reinitialized (for example after a part change), removed, or the take ends. The first segment of a vehicle is `/BeamNG/Vehicle_<id>`, later ones are `/BeamNG/Vehicle_<id>_<n>`, with `n` starting at 1. A segment starts with its first sync after the init.

### Attributes

| Attribute | Type | Sampled | Content |
| --- | --- | --- | --- |
| `bng:vehicleId` | `int` | | BeamNG object id |
| `bng:geometryKey` | `string` | | Geometry key from the vehicle init |
| `bng:mode` | `string` | | `skinned` or `baked`, see [Skinned and baked takes](#skinned-and-baked-takes) |
| `bng:endFrame` | `int` | | First frame after the segment (exclusive end) |
| `bng:init` | `string` | | Compact JSON of the decoded init: `vehicle_id`, `geometry_key`, `node_ids`, `materials`, `paints`, and `textures` without their data |
| `bng:origin` | `float3` | yes | Vehicle world position |
| `bng:textures` | `asset[]` | step | Texture files, indexed like the material texture fields |
| `bng:brokenBeams` | `int[]` | step | Broken beams, as pairs of node indices |
| `bng:hiddenProps` | `int[]` | step | Hidden prop ids (`pid`) |
| `bng:damagedSwitches` | `int[]` | step | Pairs of material switch id and damaged material id |
| `bng:paint<i>:baseColor` | `float4` | step | Paint slot `i` (0 to 2) base color, linear RGBA |
| `bng:paint<i>:metallic`, `:roughness`, `:clearcoat`, `:clearcoatRoughness` | `float` | step | Paint slot `i` scalars |
| `bng:switch<id>:materialId` | `int` | step | Material shown by switch `id`, `-1` for the base material |
| `bng:switch<id>:emissiveScale` | `float` | step | Light intensity of switch `id`, `0` when off |

Node indices refer to the sync node order: the `node_ids` order in `bng:init` and in `Nodes`.

The damage attributes are cumulative: each sample holds the full damage state, not the changes. A body triangle is hidden when both endpoints of a broken beam are among the skin locator nodes of its vertices.

### Step-sampled values

Values marked **step** hold discrete state. They are only sampled when they change, together with the previous value one frame earlier, so USD's linear interpolation never blends two states. To read them exactly, take the last sample at or before the frame instead of letting USD interpolate. Asset arrays are not interpolated by USD, so each `bng:textures` sample simply holds until the next one.

## Meshes

Body parts and prop meshes share the same attributes:

| Attribute | Interpolation | Content |
| --- | --- | --- |
| `bng:name` | | Original BeamNG mesh name |
| `points` | vertex | Rest positions |
| `faceVertexCounts` | | All `3` |
| `faceVertexIndices` | | Triangle corner vertex indices |
| `normals` | vertex | Rest normals |
| `primvars:st` | faceVarying | UV1 |
| `primvars:st1` | faceVarying | UV2 |
| `primvars:bng:tangents` | vertex | Tangents as `float4`, `w` = handedness. In skin axes `(x, z, -y)`, not converted to stage axes. |
| `primvars:bng:materialIds` | uniform | Material id per triangle, index into the vehicle's materials |
| `primvars:bng:switchIds` | uniform | Material switch id per triangle, `-1` when static |

Each mesh is bound to the material with its lowest material id, as a preview for generic USD viewers. The full per-triangle assignment is in `primvars:bng:materialIds`.

**Props** are rigid. Each `Props/<prop name>_<index>` Xform carries one time-sampled matrix transform op, already computed as `bindMatrix * inverse(liveInitMatrix) * liveMatrix` from the [protocol](Protocol%20Description.md#realtime-sync). Its `Mesh` child has a `bng:pid` attribute with the BeamNG prop id, matching `bng:hiddenProps`.

## Materials and textures

Materials are written under `/BeamNG/Materials/Vehicle_<id>/`, one `Material` prim per vehicle material:

| Attribute | Content |
| --- | --- |
| `bng:id` | Material id, the index used by `primvars:bng:materialIds` and the switch attributes |
| `bng:definition` | The original BeamNG material definition as JSON, see [Materials](Protocol%20Description.md#materials) |

`bng:definition` is the authoritative material data. The Material prims have no USD shading network.

Texture files are packed in the USDZ as described in [Package](#package). `bng:textures` on the vehicle segment maps each texture index to its file. Dynamic textures (license plates, dynamic decals, dashboard screens) that change during the take get a new file version, and the whole `bng:textures` array is sampled again at the frame of the change. Paths are written as `./textures/...` so USDZ packaging keeps them unchanged.

## Bundled importers

Both importers place the first recorded vehicle's starting position at the scene origin, and keep the other vehicles relative to it.

**Blender** (`blender/import_take.py`) bakes a skinned take into keyframes, using the same code as the live link:

- transforms on the vehicle root, body and props
- one shape key per frame where the body was reskinned
- visibility of broken triangles and hidden props
- paints, switch materials and light emission
- texture changes, as image sequences selected by a keyed `image_user.frame_offset` on every image node using them

All keys use constant interpolation. The scene frame rate and frame range are set from the chosen playback fps.

**Unreal** (`ue5/import_take.py`) converts a skinned take into persistent actors in the current level, and a Level Sequence under `/Game/BNGLink/Generated/Takes`:

- Root and prop motion use standard transform tracks.
- The body component stores the capture samples and interpolates them from its Sequencer-driven `Time` property before running the GPU skinning. It switches to the recorded broken-triangle state at each damage event.
- Paints, material switches, lights and texture versions are baked into persistent material variants, material slot tracks and material parameter tracks.

The importer saves the current level and the sequence, so the level must already be saved.
