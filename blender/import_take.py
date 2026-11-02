# Bakes a skinned USD take into keyframes with the live endpoint's vehicle code:
# transforms on the root, body and props, one shape key per reskinned frame, and keys on paints,
# switch materials, emission and changing textures, all constant-interpolated.
# Used by the add-on's "Load record from file" button.

import os

import bpy
import numpy as np

import bnglink
import bngusd.reader as usd_reader
import vehicle
from textures import TEX_DIR


def log_event(message):
    print(f'BNGLink Blender USD import: {message}')


def save_image_sequence(image, textures, name):
    # Image pointers are not animatable
    # The versions become sequence frames 1..N and each image node picks one through a keyed image_user.frame_offset
    os.makedirs(TEX_DIR, exist_ok=True)
    ext = '.png' if textures[0]['mime'] == 'image/png' else '.jpg'
    paths = [os.path.join(TEX_DIR, f'{name}.{number:04d}{ext}') for number in range(1, len(textures) + 1)]
    for path, texture in zip(paths, textures):
        with open(path, 'wb') as f:
            f.write(texture['data'])
    image.source = 'SEQUENCE'
    image.filepath = paths[0]
    image.reload()


class VehicleBake:
    def __init__(self, segment, state):
        self.playhead = usd_reader.Playhead(segment)
        self.state = state
        self.body = state['body']
        self.objects = [state['root']] + ([self.body['obj']] if self.body else []) + [prop['obj'] for prop in state['props']]
        for obj in self.objects:
            obj.rotation_mode = 'QUATERNION'
        # The root empty survives rebuilds and would keep the keys of a previous import
        state['root'].animation_data_clear()
        self.quats = {}
        self.snapshots = []
        self.slot_keys = True
        # Material swaps can only be keyed on object-linked slots
        self.slots = [(obj, index) for targets in state['switch_targets'].values()
                      for obj, indices in targets for index in indices]
        for obj, index in self.slots:
            obj.material_slots[index].link = 'OBJECT'
            obj.material_slots[index].material = obj.data.materials[index]
        self.sequences = self._texture_sequences()

    def key(self, frame, sample, decoder):
        state = self.state
        step = self.playhead.step(sample)
        # Before the switch updates: switch materials are copies and must inherit the image keys
        for index, version in step.textures.items():
            for tree, node in self.sequences[index]:
                node.image_user.frame_offset = version
                path = f'nodes["{bpy.utils.escape_identifier(node.name)}"].image_user.frame_offset'
                tree.keyframe_insert(path, frame=frame)
        if step.paints is not None and vehicle.update_paint(state, step.paints):
            for entry in state['paint_nodes']:
                entry['node'].outputs[0].keyframe_insert('default_value', frame=frame)
        if step.switches is not None and vehicle.update_materials(state, step.switches):
            self._key_switches(step.switches, frame)

        vehicle.apply_damage(state, step.sync)
        vehicle.apply_body(state, step.sync, decoder)
        vehicle.apply_transforms(state, step.sync)
        for obj in self.objects:
            self._key_transform(obj, frame)
        for prop in state['props']:
            prop['obj'].keyframe_insert('hide_viewport', frame=frame)
            prop['obj'].keyframe_insert('hide_render', frame=frame)
        if self.body is not None and state['rigid']['mode'][0] == bnglink.RIGID_RESKIN:
            # The deformed vertices were just written into the mesh
            self.snapshots.append((frame, np.array(self.body['positions'])))

    def build_shape_keys(self, end_frame):
        # One key per reskinned frame, at weight 1 until the next reskin.
        # Absolute positions go through the key block API: overwriting the mesh buffer desyncs the deltas from the basis
        if not self.snapshots:
            return
        obj = self.body['obj']
        obj.shape_key_clear()
        obj.shape_key_add(name='Basis', from_mix=False).data.foreach_set('co', self.snapshots[0][1].ravel())
        ends = [frame for frame, _ in self.snapshots[1:]] + [end_frame + 1]
        for (frame, positions), end in zip(self.snapshots, ends):
            key_block = obj.shape_key_add(name=f'BNG_{frame}', from_mix=False)
            key_block.data.foreach_set('co', positions.ravel())
            # The leading 0 is required because a fcurve holds its first value backwards,
            # which would stack every later key's delta into the early frames
            for at, value in ((1, 0.0), (frame, 1.0), (end, 0.0)):
                key_block.value = value
                key_block.keyframe_insert('value', frame=at)

    def _texture_sequences(self):
        # Slot index: [(node tree, image node)] for the texture slots that change during the take
        segment = self.playhead.segment
        trees = {material.node_tree for material in self.state['materials']}
        sequences = {}
        for index in self.playhead.versions:
            image = self.state['images'][index]
            textures = [texture for _, texture in segment.texture_versions[index]]
            save_image_sequence(image, textures, f'V{segment.vehicle_id}_{index}')
            nodes = [(tree, node) for tree in trees for node in tree.nodes
                     if node.type == 'TEX_IMAGE' and node.image == image]
            for _, node in nodes:
                # One-frame cyclic range: the displayed sequence frame is always 1 + frame_offset
                node.image_user.frame_start = 1
                node.image_user.frame_duration = 1
                node.image_user.use_cyclic = True
                node.image_user.use_auto_refresh = True
            sequences[index] = nodes
        return sequences

    def _key_switches(self, switches, frame):
        for switch in switches:
            material_id = switch.get('materialId')
            if material_id is None or material_id < 0:
                continue
            _, strengths = self.state['switch_materials'][(switch['id'], material_id)]
            for socket in strengths:
                socket.keyframe_insert('default_value', frame=frame)
        # update_materials swaps the data slots, which are not animatable; the object slots mirror them
        for obj, index in self.slots:
            slot = obj.material_slots[index]
            if slot.material == obj.data.materials[index]:
                continue
            slot.material = obj.data.materials[index]
            if self.slot_keys:
                try:
                    slot.keyframe_insert('material', frame=frame)
                except TypeError:
                    self.slot_keys = False
                    log_event('Blender rejects material slot keys, material swaps are not animated')

    def _key_transform(self, obj, frame):
        # A negated quaternion is the same rotation
        # Matching the previous key's sign keeps spins from taking the long way round if the keys are later interpolated
        quat = obj.rotation_quaternion
        previous = self.quats.get(obj)
        if previous is not None and previous.dot(quat) < 0.0:
            obj.rotation_quaternion = -quat
        self.quats[obj] = obj.rotation_quaternion.copy()
        obj.keyframe_insert('location', frame=frame)
        obj.keyframe_insert('rotation_quaternion', frame=frame)


def import_take(path, fps=None):
    take = usd_reader.Take(path, fps)
    scene = bpy.context.scene
    # Blender plays fps / fps_base, which also covers 29.97-style rates
    scene.render.fps = round(take.fps)
    scene.render.fps_base = scene.render.fps / take.fps
    first, last = take.frame_range
    scene.frame_start, scene.frame_end = first, last

    vehicle.world_origin = None
    states = {}
    for segment in take.segments:
        vehicle.build_meshes(segment.init, states, TEX_DIR)
    bakes = [VehicleBake(segment, states[segment.vehicle_id]) for segment in take.segments]
    decoder = bnglink.BngDecoder()
    existing_actions = set(bpy.data.actions)
    for frame in range(first, last + 1):
        # Evaluates the keys so far, so the new state is applied on top of the take, not overwritten
        scene.frame_set(frame)
        sample = take.to_capture_frame(frame)
        for bake in bakes:
            bake.key(frame, sample, decoder)
    for bake in bakes:
        bake.build_shape_keys(last)
    # Constant keys reproduce the sampled frames exactly
    # Python-inserted keys ignore the new-key interpolation preference, so they are converted afterwards
    for action in set(bpy.data.actions) - existing_actions:
        for layer in action.layers:
            for strip in layer.strips:
                for channelbag in strip.channelbags:
                    for fcurve in channelbag.fcurves:
                        for point in fcurve.keyframe_points:
                            point.interpolation = 'CONSTANT'
    scene.frame_set(first)
    log_event(f'imported {path}')
    return states
