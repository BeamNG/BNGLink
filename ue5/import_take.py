import os
import re
import sys
from fractions import Fraction

import numpy as np
import unreal

pkg_dir = os.path.dirname(__file__)
root_dir = os.path.dirname(pkg_dir)
for p in (root_dir, pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

import bnglink
from bnglink import settings
import bngusd.reader as usd_reader
import materials
import textures
import vehicle

TEX_DIR = os.path.join(pkg_dir, 'bng_textures')
TAKES_DIR = settings.default_takes_dir()
TAKE_ASSET_DIR = '/Game/BNGLink/Generated/Takes'


def _frame_rate(value):
    rate = Fraction(str(value)).limit_denominator(1001)
    return unreal.FrameRate(numerator=rate.numerator, denominator=rate.denominator)


def _create_sequence(path, take):
    name = re.sub(r'[^A-Za-z0-9_]+', '_', os.path.splitext(os.path.basename(path))[0]).strip('_') or 'Take'
    unreal.EditorAssetLibrary.make_directory(TAKE_ASSET_DIR)
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    package_name, asset_name = asset_tools.create_unique_asset_name(
        f'{TAKE_ASSET_DIR}/{name}_Sequence', '')
    package_path = package_name.rsplit('/', 1)[0]
    sequence = asset_tools.create_asset(
        asset_name, package_path, unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
    if sequence is None:
        raise RuntimeError('Level Sequence creation failed')

    duration = (take.capture_end - take.capture_start) / take.capture_fps
    sequence.set_tick_resolution_directly(_frame_rate(take.capture_fps))
    sequence.set_display_rate(_frame_rate(take.fps))
    sequence.set_playback_start_seconds(0.0)
    sequence.set_playback_end_seconds(duration + 1.0 / take.fps)
    return sequence, duration


def _add_key(channel, frame, value):
    channel.add_key(
        time=unreal.FrameNumber(int(frame)),
        new_value=float(value),
        time_unit=unreal.MovieSceneTimeUnit.TICK_RESOLUTION,
        interpolation=unreal.MovieSceneKeyInterpolation.LINEAR,
    )


def _add_unbounded_section(track):
    section = track.add_section()
    section.set_start_frame_bounded(False)
    section.set_end_frame_bounded(False)
    return section


def _add_visibility_track(binding, start_frame, end_frame, take_end_frame, keys=()):
    if start_frame == 0 and end_frame > take_end_frame and not keys:
        return
    track = binding.add_track(unreal.MovieSceneVisibilityTrack)
    section = _add_unbounded_section(track)
    channel = section.get_all_channels()[0]
    if start_frame > 0:
        channel.add_key(
            unreal.FrameNumber(0), False,
            time_unit=unreal.MovieSceneTimeUnit.TICK_RESOLUTION)
    channel.add_key(
        unreal.FrameNumber(start_frame), True,
        time_unit=unreal.MovieSceneTimeUnit.TICK_RESOLUTION)
    for frame, visible in keys:
        channel.add_key(
            unreal.FrameNumber(frame), visible,
            time_unit=unreal.MovieSceneTimeUnit.TICK_RESOLUTION)
    if end_frame <= take_end_frame:
        channel.add_key(
            unreal.FrameNumber(end_frame), False,
            time_unit=unreal.MovieSceneTimeUnit.TICK_RESOLUTION)


def _add_time_track(binding, duration, capture_fps):
    track = binding.add_track(unreal.MovieSceneDoubleTrack)
    track.set_property_name_and_path('Time', 'Time')
    section = _add_unbounded_section(track)
    channel = section.get_all_channels()[0]
    _add_key(channel, 0, 0.0)
    end_frame = round(duration * capture_fps)
    if end_frame > 0:
        _add_key(channel, end_frame, duration)


def _add_material_tracks(binding, component, material_keys):
    for material_index, keys in enumerate(material_keys):
        if not keys:
            continue
        component.set_material(material_index, keys[0][1])
        if len(keys) == 1:
            continue
        track = binding.add_track(unreal.MovieScenePrimitiveMaterialTrack)
        track.set_material_info(unreal.ComponentMaterialInfo(
            material_slot_index=material_index,
            material_type=unreal.ComponentMaterialType.INDEXED_MATERIAL,
        ))
        section = _add_unbounded_section(track)
        channel = section.get_all_channels()[0]
        for frame, material in keys:
            channel.add_key(
                time=unreal.FrameNumber(frame),
                new_value=material,
                time_unit=unreal.MovieSceneTimeUnit.TICK_RESOLUTION,
            )


def _add_parameter_keys(section, name, value_track, capture_start, color=False):
    parameter = unreal.MaterialParameterInfo(
        name=unreal.Name(name),
        association=unreal.MaterialParameterAssociation.GLOBAL_PARAMETER,
        index=-1,
    )
    add_key = section.add_color_parameter_key if color else section.add_scalar_parameter_key
    for sample in value_track.times:
        value = value_track(sample)
        if color:
            value = unreal.LinearColor(float(value[0]), float(value[1]), float(value[2]), 1.0)
        else:
            value = float(value)
        add_key(
            parameter,
            unreal.FrameNumber(round(sample - capture_start)),
            value,
            '',
            '',
        )


def _add_material_parameter_track(
        binding, material_index, segment, section_key, paint_tracks, switch_tracks, capture_start):
    base_material_id, switch_id = section_key
    switch_track = switch_tracks.get(switch_id)
    material_ids = {base_material_id}
    if switch_track is not None:
        material_ids.update(
            material_id
            for sample in switch_track[0].times
            if (material_id := int(switch_track[0](sample))) >= 0
        )
    animate_paints = paint_tracks and any(
        segment.init['materials'][material_id].get('paint')
        for material_id in material_ids
    )
    animate_emissive = switch_track is not None and (
        len(switch_track[1].times) > 1 or float(switch_track[1](switch_track[1].times[0])) != 1.0
    )
    if not animate_paints and not animate_emissive:
        return

    track = binding.add_track(unreal.MovieSceneComponentMaterialTrack)
    track.set_material_info(unreal.ComponentMaterialInfo(
        material_slot_index=material_index,
        material_type=unreal.ComponentMaterialType.INDEXED_MATERIAL,
    ))
    section = _add_unbounded_section(track)

    if animate_emissive:
        _add_parameter_keys(
            section, materials.EMISSIVE_SCALE_PARAMETER, switch_track[1], capture_start)

    if animate_paints:
        for paint_index, field, paint_track in paint_tracks:
            _add_parameter_keys(
                section,
                materials.paint_parameter_name(paint_index, field),
                paint_track,
                capture_start,
                color=field == 'baseColor',
            )


def _relative_transform(component):
    location = component.get_editor_property('relative_location')
    rotation = component.get_editor_property('relative_rotation')
    scale = component.get_editor_property('relative_scale3d')
    return (
        location.x, location.y, location.z,
        rotation.roll, rotation.pitch, rotation.yaw,
        scale.x, scale.y, scale.z,
    )


def _add_transform_track(binding, frames, transforms):
    track = binding.add_track(unreal.MovieScene3DTransformTrack)
    section = _add_unbounded_section(track)
    section.set_editor_property('use_quaternion_interpolation', True)
    channels = section.get_all_channels()
    for frame, transform in zip(frames, transforms):
        for channel, value in zip(channels, transform):
            _add_key(channel, frame, value)


def _prepare_segment(segment, state, take, decoder, reference_origin):
    if not segment.sample_times:
        raise RuntimeError(f'Vehicle {segment.vehicle_id} has no deformation samples')

    body = state['body']
    sample_frames = [round(sample - take.capture_start) for sample in segment.sample_times]
    sample_seconds = (
        np.asarray(segment.sample_times, dtype=np.float64) - take.capture_start
    ) / take.capture_fps
    sample_count = len(segment.sample_times)
    node_samples = np.empty((sample_count, len(segment.init['node_ids']), 3), dtype=np.float32)
    rotation_samples = np.empty((sample_count, 4), dtype=np.float32)
    translation_samples = np.empty((sample_count, 3), dtype=np.float32)
    transform_components = [state['root'].root_component]
    transform_components.extend(prop['actor'].root_component for prop in state['props'])
    transforms_by_component = [[] for _ in transform_components]
    prop_visibility = [[] for _ in state['props']]
    damage_times = []
    damage_indices = []

    for sample_index, (sample, seconds) in enumerate(zip(segment.sample_times, sample_seconds)):
        sync = segment.sync(sample)
        damage_changed = vehicle.apply_damage(state, sync, update_component=False)
        if damage_changed or not damage_times:
            damage_times.append(seconds)
            damage_indices.append(body['indices'].copy())
        vehicle.apply_body(state, sync, decoder, update_component=False)
        vehicle.apply_transforms(state, sync, reference_origin)
        node_samples[sample_index] = body['vehicle_nodes']
        q = body['vehicle_q']
        rotation_samples[sample_index] = (q[1], q[2], q[3], q[0])
        translation_samples[sample_index] = body['vehicle_t']
        for component, transforms in zip(transform_components, transforms_by_component):
            transforms.append(_relative_transform(component))
        hidden_props = {int(pid) for pid in sync['hidden_props']}
        for prop, visibility in zip(state['props'], prop_visibility):
            visibility.append(prop['pid'] not in hidden_props)

    initialized = body['component'].set_take(
        (take.capture_end - take.capture_start) / take.capture_fps,
        sample_count,
        node_samples.shape[1],
        sample_seconds.ctypes.data,
        node_samples.ctypes.data,
        rotation_samples.ctypes.data,
        translation_samples.ctypes.data,
    )
    if not initialized:
        raise RuntimeError('BngVehicleMeshComponent.set_take failed')
    damage_times = np.ascontiguousarray(damage_times, dtype=np.float64)
    damage_indices = np.ascontiguousarray(damage_indices, dtype=np.uint32)
    initialized = body['component'].set_take_indices(
        len(damage_times),
        damage_indices.shape[1],
        damage_times.ctypes.data,
        damage_indices.ctypes.data,
    )
    if not initialized:
        raise RuntimeError('BngVehicleMeshComponent.set_take_indices failed')
    if state['prop_handle'] is not None:
        unreal.BngLinkBridge.unregister_prop_group(state['prop_handle'])
        state['prop_handle'] = None
    return sample_frames, transforms_by_component, prop_visibility


def _material_sample_times(segment):
    samples = {segment.sample_times[0]}
    for _, material_track, _ in segment.switch_tracks:
        samples.update(material_track.times)
    for versions in segment.texture_versions:
        samples.update(frame for frame, _ in versions)
    return sorted(samples)


def _prepare_materials(segment, state, take):
    material_keys = {
        component: [[] for _ in sections]
        for component, sections in state['material_sections'].items()
    }
    current_versions = (0,) * len(segment.texture_versions)
    texture_assets = state['texture_assets']
    for sample in _material_sample_times(segment):
        versions = tuple(
            usd_reader.version_at(texture_versions, sample)
            for texture_versions in segment.texture_versions
        )
        if versions != current_versions:
            current_versions = versions
            texture_defs = [
                texture_versions[version][1]
                for texture_versions, version in zip(segment.texture_versions, versions)
            ]
            texture_assets = textures.import_textures(
                segment.vehicle_id, texture_defs, segment.init['materials'], TEX_DIR, save=True)
        switches = {
            switch_id: int(material_track(sample))
            for switch_id, material_track, _ in segment.switch_tracks
        }
        frame = round(sample - take.capture_start)

        for component, sections in state['material_sections'].items():
            for material_index, (base_material_id, switch_id) in enumerate(sections):
                material_id = switches.get(switch_id, base_material_id)
                if material_id < 0:
                    material_id = base_material_id
                material = materials.create_material(
                    segment.init['materials'][material_id],
                    segment.init['paints'],
                    texture_assets,
                    persistent=True,
                )[0]
                keys = material_keys[component][material_index]
                if not keys or keys[-1][1] != material:
                    keys.append((frame, material))
    return material_keys


def import_take(path, fps=None):
    path = str(path)
    take = usd_reader.Take(path, fps)
    if not take.segments:
        raise RuntimeError('Take has no vehicle segments')
    sequence, duration = _create_sequence(path, take)
    decoder = bnglink.BngDecoder()
    states = []
    take_end_frame = take.capture_end - take.capture_start
    first_segment = min(take.segments, key=lambda segment: segment.sample_times[0])
    reference_origin = first_segment.sync(first_segment.sample_times[0])['origin']

    for segment_index, segment in enumerate(take.segments):
        vehicle_states = {}
        vehicle.build_meshes(segment.init, vehicle_states, TEX_DIR, persistent=True)
        state = vehicle_states[segment.vehicle_id]
        states.append(state)
        frames, transforms, prop_visibility = _prepare_segment(
            segment, state, take, decoder, reference_origin)
        material_keys = _prepare_materials(segment, state, take)
        paint_tracks = [
            (paint_index, field, track)
            for paint_index, fields in enumerate(segment.paint_tracks)
            for field, track in fields.items()
            if len(track.times) > 1
        ]
        switch_tracks = {
            switch_id: (material_track, emissive_track)
            for switch_id, material_track, emissive_track in segment.switch_tracks
        }
        if segment.end_frame is not None:
            end_frame = segment.end_frame - take.capture_start
        else:
            end_frame = take_end_frame + 1
            for later in take.segments[segment_index + 1:]:
                if later.vehicle_id == segment.vehicle_id:
                    end_frame = round(later.sample_times[0] - take.capture_start)
                    break

        root_binding = sequence.add_possessable(state['root'])
        _add_transform_track(root_binding, frames, transforms[0])
        _add_visibility_track(root_binding, frames[0], end_frame, take_end_frame)
        body_actor = state['body']['component'].get_owner()
        body_binding = sequence.add_possessable(body_actor)
        _add_visibility_track(body_binding, frames[0], end_frame, take_end_frame)
        body_component = state['body']['component']
        body_component_binding = sequence.add_possessable(body_component)
        body_component_binding.set_parent(body_binding)
        _add_time_track(body_component_binding, duration, take.capture_fps)
        _add_material_tracks(body_component_binding, body_component, material_keys[body_component])
        for material_index, section_key in enumerate(state['material_sections'][body_component]):
            _add_material_parameter_track(
                body_component_binding, material_index, segment, section_key,
                paint_tracks, switch_tracks, take.capture_start)
        for prop, values, visibility in zip(state['props'], transforms[1:], prop_visibility):
            prop_binding = sequence.add_possessable(prop['actor'])
            visibility_keys = []
            previous = None
            for frame, visible in zip(frames, visibility):
                if visible != previous:
                    visibility_keys.append((frame, visible))
                    previous = visible
            _add_visibility_track(
                prop_binding, frames[0], end_frame, take_end_frame, visibility_keys)
            _add_transform_track(prop_binding, frames, values)
            component = prop['actor'].root_component
            component_binding = sequence.add_possessable(component)
            component_binding.set_parent(prop_binding)
            _add_material_tracks(component_binding, component, material_keys[component])
            for material_index, section_key in enumerate(state['material_sections'][component]):
                _add_material_parameter_track(
                    component_binding, material_index, segment, section_key,
                    paint_tracks, switch_tracks, take.capture_start)

    if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():
        raise RuntimeError('Failed to save the current level')
    if not unreal.EditorAssetLibrary.save_loaded_asset(sequence, only_if_is_dirty=False):
        raise RuntimeError(f'Failed to save {sequence.get_path_name()}')
    print(f'BNGLink Unreal USD import: imported {path} to {sequence.get_path_name()}')
    return {'sequence': sequence, 'states': states}


if __name__ == '__main__':
    import_take(sys.argv[1] if len(sys.argv) > 1 else usd_reader.latest_take(TAKES_DIR),
                float(sys.argv[2]) if len(sys.argv) > 2 else None)