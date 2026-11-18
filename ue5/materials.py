import hashlib
import json
import re

import unreal

EMISSIVE_NITS_SCALE = 1.0 / 1000.0
EMISSIVE_SCALE_PARAMETER = 'BNGLinkEmissiveScale'
MATERIALS_PATH = '/Game/BNGLink/Generated/Shared/Materials'
COLOR = unreal.MaterialSamplerType.SAMPLERTYPE_COLOR
MASK = unreal.MaterialSamplerType.SAMPLERTYPE_MASKS
NORMAL = unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL
PAINT_SCALAR_DEFAULTS = {'metallic': 0.0, 'roughness': 0.5, 'clearcoat': 0.0, 'clearcoatRoughness': 1.0}
BLEND_MODES = {
    'Add': unreal.BlendMode.BLEND_ADDITIVE,
    'AddAlpha': unreal.BlendMode.BLEND_ADDITIVE,
    'Mul': unreal.BlendMode.BLEND_MODULATE,
    'MulAlpha': unreal.BlendMode.BLEND_MODULATE,
    'PreMulAlpha': unreal.BlendMode.BLEND_ALPHA_COMPOSITE,
}
FLAT_NORMAL = (0.0, 0.0, 1.0)

mel = unreal.MaterialEditingLibrary
MP = unreal.MaterialProperty
_material_cache = {}


def paint_parameter_name(slot, field):
    suffix = 'Color' if field == 'baseColor' else field[0].upper() + field[1:]
    return f'Paint{slot}{suffix}'


class Graph:
    # Values plugged into expression inputs are expressions, (expression, output name) tuples or numbers
    def __init__(self, material, textures):
        self.material = material
        self.textures = textures
        self.paint_params = []
        self.uvs = [self.node('TextureCoordinate', coordinate_index=index) for index in (0, 1)]

    def node(self, name, **props):
        expr = mel.create_material_expression(self.material, getattr(unreal, 'MaterialExpression' + name))
        for key, value in props.items():
            expr.set_editor_property(key, value)
        return expr

    def op(self, name, **inputs):
        expr = self.node(name)
        for input_name, value in inputs.items():
            if isinstance(value, (int, float)):
                expr.set_editor_property(f'const_{input_name.lower()}', float(value))
                continue
            source, output = value if isinstance(value, tuple) else (value, '')
            pin = '' if input_name == 'Input' else input_name
            if not mel.connect_material_expressions(source, output, expr, pin):
                raise RuntimeError(f'Could not connect {name}.{input_name}')
        return expr

    def mul(self, a, b):
        return self.op('Multiply', A=a, B=b)

    def add(self, a, b):
        return self.op('Add', A=a, B=b)

    def sub(self, a, b):
        return self.op('Subtract', A=a, B=b)

    def lerp(self, a, b, alpha):
        return self.op('LinearInterpolate', A=a, B=b, Alpha=alpha)

    def scale(self, value, factor):
        return value if factor is None else self.mul(value, factor)

    def sum(self, values):
        result = values[0]
        for value in values[1:]:
            result = self.add(result, value)
        return result

    def const(self, value):
        return self.node('Constant', r=float(value))

    def rgb(self, value=(1.0, 1.0, 1.0)):
        rgb = list(value) + [1.0] * (3 - len(value))
        return self.node('Constant3Vector', constant=unreal.LinearColor(rgb[0], rgb[1], rgb[2], 1.0))

    def sample(self, index, sampler, use_uv1=False, uv=None):
        expr = self.node('TextureSample', texture=self.textures[index], sampler_type=sampler)
        mel.connect_material_expressions(uv or self.uvs[1 if use_uv1 else 0], '', expr, 'UVs')
        return expr

    def detail_uv(self, layer, use_uv1):
        scale = layer.get('detailScale', (2.0, 2.0))
        return self.mul(self.uvs[1 if use_uv1 else 0], self.node('Constant2Vector', r=float(scale[0]), g=float(scale[1])))

    def blend_detail(self, base, detail, strength):
        centered = self.sub(self.mul(detail, 2.0), 1.0)
        return self.op('Saturate', Input=self.add(base, self.mul(centered, max(0.0, strength))))

    def paint_param(self, paint, slot, field):
        name = paint_parameter_name(slot, field)
        if field == 'baseColor':
            value = paint.get('baseColor', (1.0, 1.0, 1.0, 1.0))
            expr = self.node('VectorParameter', parameter_name=name,
                             default_value=unreal.LinearColor(value[0], value[1], value[2], 1.0))
        else:
            expr = self.node('ScalarParameter', parameter_name=name,
                             default_value=float(paint.get(field, PAINT_SCALAR_DEFAULTS[field])))
        self.paint_params.append({'slot': slot, 'field': field, 'name': name})
        return expr

    def connect(self, value, prop):
        if isinstance(value, tuple):
            mel.connect_material_property(value[0], value[1], prop)
        else:
            mel.connect_material_property(value, '', prop)

    def connect_clearcoat(self, clearcoat, roughness):
        if hasattr(MP, 'MP_CUSTOM_DATA0'):
            self.connect(clearcoat, MP.MP_CUSTOM_DATA0)
            self.connect(roughness, MP.MP_CUSTOM_DATA1)


def _uv_key(texture_key):
    return f'{texture_key[:-len("Texture")]}UseUV1'


def _layer_scalar(g, layer, texture_key, factor_key, default, detail_key=None, strength_key=None, opacity_detail=False):
    index = layer.get(texture_key)
    value = g.const(1.0) if index is None else (g.sample(index, MASK, layer.get(_uv_key(texture_key))), 'R')
    detail_index = layer.get(detail_key) if detail_key else None
    if detail_index is not None:
        detail = (g.sample(detail_index, MASK, uv=g.detail_uv(layer, layer.get(_uv_key(detail_key)))), 'R')
        blended = g.blend_detail(1.0 if opacity_detail else value, detail, layer.get(strength_key, 1.0))
        value = g.mul(value, blended) if opacity_detail else blended
    return g.mul(value, layer.get(factor_key, default))


def _layer_color(g, layer):
    index = layer.get('baseColorTexture')
    value = g.rgb() if index is None else (g.sample(index, COLOR, layer.get('baseColorUseUV1')), '')
    if layer.get('detailBaseColorTexture') is not None:
        detail = g.sample(layer['detailBaseColorTexture'], COLOR,
                          uv=g.detail_uv(layer, layer.get('detailBaseColorUseUV1')))
        value = g.blend_detail(value, (detail, ''), layer.get('detailBaseColorStrength', 1.0))
    value = g.mul(value, g.rgb(layer.get('baseColorFactor', (1.0, 1.0, 1.0))))
    if layer.get('overlayTexture') is not None:
        overlay = g.sample(layer['overlayTexture'], COLOR, layer.get('overlayUseUV1'))
        value = g.lerp(value, (overlay, ''), (overlay, 'A'))
    return value


def _layer_specular(g, layer):
    factor = layer.get('specularFactor')
    factor = sum(factor[:3]) / len(factor[:3]) if factor else 1.0
    index = layer.get('specularTexture')
    result = g.const(factor) if index is None else g.mul((g.sample(index, COLOR, layer.get('specularUseUV1')), 'R'), factor)
    if layer.get('reflectivityTexture') is not None:
        reflectivity = (g.sample(layer['reflectivityTexture'], MASK, layer.get('reflectivityUseUV1')), 'A')
        result = g.op('Max', A=result, B=g.mul(reflectivity, layer.get('reflectivityFactor', 1.0)))
    return result


def _layer_emissive(g, layer):
    result = g.rgb(layer.get('emissiveFactor', (0.0, 0.0, 0.0)))
    if layer.get('emissiveTexture') is not None:
        result = g.mul((g.sample(layer['emissiveTexture'], COLOR, layer.get('emissiveUseUV1')), ''), result)
    intensity = layer.get('emissiveIntensity')
    if intensity is not None and intensity >= 0:
        result = g.mul(result, intensity * EMISSIVE_NITS_SCALE)
    return result


def _layer_normal(g, layer):
    index = layer.get('normalTexture')
    if index is None:
        normal = g.rgb(FLAT_NORMAL)
    else:
        normal = (g.sample(index, NORMAL, layer.get('normalUseUV1')), '')
        strength = layer.get('normalStrength', 1.0)
        if strength != 1.0:
            normal = g.lerp(g.rgb(FLAT_NORMAL), normal, strength)
    if layer.get('detailNormalTexture') is None:
        return normal
    detail = g.sample(layer['detailNormalTexture'], NORMAL, uv=g.detail_uv(layer, layer.get('detailNormalUseUV1')))
    detail = g.mul(g.sub((detail, ''), g.rgb(FLAT_NORMAL)), layer.get('detailNormalStrength', 1.0))
    return g.op('Normalize', VectorInput=g.add(normal, detail))


def _blend_layer(g, result, layer, keys):
    for key in keys:
        result[key] = g.lerp(result[key], layer[key], layer['opacity'])


def layer_values(g, material_def):
    # Every layer is blended over the ones below it by its opacity. Unblended layers are returned too
    layers = []
    for layer in material_def['layers']:
        values = {
            'base': _layer_color(g, layer),
            'metallic': _layer_scalar(g, layer, 'metallicTexture', 'metallicFactor', 0.0,
                                      'detailMetallicTexture', 'detailMetallicStrength'),
            'roughness': _layer_scalar(g, layer, 'roughnessTexture', 'roughnessFactor', 1.0,
                                       'detailRoughnessTexture', 'detailRoughnessStrength'),
            'ao': _layer_scalar(g, layer, 'aoTexture', 'aoFactor', 1.0, 'detailAoTexture', 'detailAoStrength'),
            'opacity': _layer_scalar(g, layer, 'opacityTexture', 'opacityFactor', 1.0,
                                     'detailOpacityTexture', 'detailOpacityStrength', True),
            'emissive': _layer_emissive(g, layer),
            'clearcoat': _layer_scalar(g, layer, 'clearCoatTexture', 'clearCoatFactor', 0.0),
            'clearcoat_roughness': _layer_scalar(g, layer, 'clearCoatRoughnessTexture', 'clearCoatRoughnessFactor', 1.0),
            'specular': _layer_specular(g, layer),
            'anisotropy': g.const(layer.get('anisotropy', 0.0)),
            'normal': _layer_normal(g, layer),
        }
        adds_coverage = layer.get('opacityTexture') is not None or layer.get('detailOpacityTexture') is not None
        values['coverage'] = values['opacity'] if adds_coverage else None
        if layer.get('legacyEmissive'):
            values['emissive'] = values['base']
            values['base'] = g.rgb((0.0, 0.0, 0.0))
        layers.append(values)

    result = dict(layers[0])
    blended_keys = [key for key in result if key not in ('opacity', 'coverage')]
    for layer in layers[1:]:
        _blend_layer(g, result, layer, blended_keys)
        if layer['coverage'] is not None:
            result['opacity'] = g.add(layer['coverage'], g.mul(result['opacity'], g.sub(1.0, layer['coverage'])))
    result['normal'] = g.op('Normalize', VectorInput=result['normal'])
    return result, layers


def _substrate_channel(g, material_def, name, default):
    index = material_def.get(f'{name}Texture')
    value = None if index is None else (g.sample(index, MASK, material_def.get(f'{name}UseUV1')), 'R')
    factor = material_def.get(f'{name}Factor')
    if factor is not None:
        value = g.mul(1.0 if value is None else value, factor)
    return g.const(default) if value is None else value


def _palette_weights(g, palette):
    # Per-channel paint weights from the palette mask; alpha is the paint coverage
    red, green, blue, alpha = ((palette, channel) for channel in 'RGBA')
    total = g.add(g.add(g.add(red, green), blue), 1e-6)
    return [g.mul(g.op('Divide', A=channel, B=total), alpha) for channel in (red, green, blue)] + [g.sub(1.0, alpha)]


def _blend_paint_scalar(g, weights, paints, field, enabled, base_value=1.0):
    if not enabled:
        return g.const(base_value)
    terms = [g.mul(weight, g.paint_param(paint, slot, field)) for slot, (weight, paint) in enumerate(zip(weights[:3], paints))]
    return g.sum(terms + [g.mul(weights[3], base_value)])


def _blend_paint_color(g, weights, paints, enabled):
    if not enabled:
        return g.rgb()
    terms = [g.mul(g.paint_param(paint, slot, 'baseColor'), weight) for slot, (weight, paint) in enumerate(zip(weights[:3], paints))]
    return g.sum(terms + [g.mul(g.rgb(), weights[3])])


def _blend_palette(g, substrate, paint, mask, palette_on_top):
    return g.lerp(substrate, paint, mask) if palette_on_top else g.lerp(paint, substrate, mask)


def _final_base_color(g, base, ao, opacity, material_def):
    base = g.mul(base, ao)
    if material_def.get('transparent') and material_def.get('blendOp') == 'PreMulAlpha':
        base = g.mul(base, opacity)
    return base


def _palette_paint(g, material_def, paint_def, paints, layers, ao, opacity):
    base_color_factor = material_def.get('baseColorFactor')
    if material_def.get('baseColorTexture') is None:
        substrate_base = g.rgb(base_color_factor if base_color_factor is not None else (1.0, 1.0, 1.0))
    else:
        substrate_base = (g.sample(material_def['baseColorTexture'], COLOR, material_def.get('baseColorUseUV1')), '')
        if base_color_factor is not None:
            substrate_base = g.mul(substrate_base, g.rgb(base_color_factor))
    substrate_metallic = _substrate_channel(g, material_def, 'metallic', 0.0)
    substrate_roughness = _substrate_channel(g, material_def, 'roughness', 0.5)

    weights = _palette_weights(g, g.sample(paint_def['paletteTexture'], MASK, paint_def.get('paletteUseUV1')))
    slots = [paints[index] if index < len(paints) else {} for index in range(3)]
    color = _blend_paint_color(g, weights, slots, paint_def.get('paletteBaseColor', True))
    if paint_def.get('colorMapTexture') is not None:
        color = g.mul((g.sample(paint_def['colorMapTexture'], COLOR, paint_def.get('diffuseUseUV1')), ''), color)
    if paint_def.get('baseColorFactor') is not None:
        color = g.mul(color, g.rgb(paint_def['baseColorFactor']))
    metallic = _blend_paint_scalar(g, weights, slots, 'metallic', paint_def.get('paletteMetallic', True))
    roughness = _blend_paint_scalar(g, weights, slots, 'roughness', paint_def.get('paletteRoughness', True))
    clearcoat = _blend_paint_scalar(g, weights, slots, 'clearcoat', paint_def.get('paletteClearCoat', True), 0.0)
    clearcoat_roughness = _blend_paint_scalar(g, weights, slots, 'clearcoatRoughness',
                                              paint_def.get('paletteClearCoatRoughness', True))
    metallic = g.scale(metallic, paint_def.get('paintMetallicFactor'))
    roughness = g.scale(roughness, paint_def.get('paintRoughnessFactor'))
    clearcoat = g.scale(clearcoat, paint_def.get('clearCoatFactor'))
    if paint_def.get('clearCoatTexture') is not None:
        clearcoat = g.mul(clearcoat, (g.sample(paint_def['clearCoatTexture'], MASK, paint_def.get('clearCoatUseUV1')), 'R'))
    clearcoat_roughness = g.scale(clearcoat_roughness, paint_def.get('clearCoatRoughnessFactor'))

    index = paint_def.get('opacityTexture')
    mask = g.const(1.0) if index is None else (g.sample(index, MASK, paint_def.get('opacityUseUV1')), 'R')
    mask = g.scale(mask, paint_def.get('opacityFactor'))

    palette_on_top = paint_def.get('paletteOnTop', True)
    result = {
        'base': _blend_palette(g, substrate_base, color, mask, palette_on_top),
        'metallic': _blend_palette(g, substrate_metallic, metallic, mask, palette_on_top),
        'roughness': _blend_palette(g, substrate_roughness, roughness, mask, palette_on_top),
        'clearcoat': _blend_palette(g, 0.0, clearcoat, mask, palette_on_top),
        'clearcoat_roughness': _blend_palette(g, 1.0, clearcoat_roughness, mask, palette_on_top),
    }
    # Layers above the paint (liveries, decals) are drawn over
    for layer in layers[paint_def.get('firstOverlayLayer', len(layers)):]:
        _blend_layer(g, result, layer, list(result))
    g.connect(_final_base_color(g, result['base'], ao, opacity, material_def), MP.MP_BASE_COLOR)
    g.connect(result['metallic'], MP.MP_METALLIC)
    g.connect(result['roughness'], MP.MP_ROUGHNESS)
    g.connect_clearcoat(result['clearcoat'], result['clearcoat_roughness'])


def _set_material_properties(material, material_def):
    material.set_editor_property('use_material_attributes', False)
    material.set_editor_property('two_sided', bool(material_def.get('doubleSided')))
    if material_def.get('alphaTest'):
        material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_MASKED)
        material.set_editor_property('opacity_mask_clip_value', material_def.get('alphaRef', 0) / 255.0)
    elif material_def.get('transparent'):
        material.set_editor_property('blend_mode', BLEND_MODES.get(material_def.get('blendOp'), unreal.BlendMode.BLEND_TRANSLUCENT))
    else:
        material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_OPAQUE)
    if material_def.get('paint') or any(
        layer.get('clearCoatFactor', 0.0) > 0 or layer.get('clearCoatTexture') is not None
        for layer in material_def['layers']
    ):
        material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_CLEAR_COAT)


def _texture_indices(material_def):
    sources = (material_def, *material_def.get('layers', []), material_def.get('paint') or {})
    return sorted({
        index
        for source in sources
        for key, index in source.items()
        if key.endswith('Texture') and isinstance(index, int)
    })


def _build_material(material_def, paints, texture_assets, variant_key=''):
    key_hash = hashlib.sha1((material_def['cache_key'] + '\0' + variant_key).encode('utf-8')).hexdigest()[:12]
    asset_name = re.sub(r'[^A-Za-z0-9_]+', '_', material_def['name']).strip('_') or 'Material'
    name = f'M_{material_def["id"]}_{asset_name}_{key_hash}'
    unreal.EditorAssetLibrary.make_directory(MATERIALS_PATH)
    asset_path = f'{MATERIALS_PATH}/{name}'
    if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        material = unreal.load_asset(asset_path)
    else:
        material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, MATERIALS_PATH, unreal.Material, unreal.MaterialFactoryNew())
    _set_material_properties(material, material_def)
    mel.delete_all_material_expressions(material)

    g = Graph(material, texture_assets)
    result, layers = layer_values(g, material_def)
    paint_def = material_def.get('paint')
    if paint_def and paint_def['mode'] == 'palette':
        _palette_paint(g, material_def, paint_def, paints, layers, result['ao'], result['opacity'])
    else:
        base = _final_base_color(g, result['base'], result['ao'], result['opacity'], material_def)
        if paint_def:
            base = g.mul(base, g.paint_param(paints[0] if paints else {}, 0, 'baseColor'))
        elif not material_def.get('pbr15'):
            g.connect(result['specular'], MP.MP_SPECULAR)
        g.connect(base, MP.MP_BASE_COLOR)
        g.connect(result['metallic'], MP.MP_METALLIC)
        g.connect(result['roughness'], MP.MP_ROUGHNESS)
        if hasattr(MP, 'MP_ANISOTROPY'):
            g.connect(result['anisotropy'], MP.MP_ANISOTROPY)
        g.connect_clearcoat(result['clearcoat'], result['clearcoat_roughness'])
    g.connect(result['ao'], MP.MP_AMBIENT_OCCLUSION)
    g.connect(result['normal'], MP.MP_NORMAL)
    g.connect(g.mul(result['emissive'], g.node(
        'ScalarParameter', parameter_name=EMISSIVE_SCALE_PARAMETER, default_value=1.0)),
              MP.MP_EMISSIVE_COLOR)
    if material_def.get('alphaTest'):
        g.connect(result['opacity'], MP.MP_OPACITY_MASK)
    elif material_def.get('transparent'):
        g.connect(result['opacity'], MP.MP_OPACITY)

    mel.layout_material_expressions(material)
    mel.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(asset_path, only_if_is_dirty=False)
    return material, g.paint_params


def apply_paints(material, paint_params, paints):
    for param in paint_params:
        if param['slot'] < len(paints):
            value = paints[param['slot']][param['field']]
            if param['field'] == 'baseColor':
                material.set_vector_parameter_value(param['name'], unreal.LinearColor(float(value[0]), float(value[1]), float(value[2]), 1.0))
            else:
                material.set_scalar_parameter_value(param['name'], float(value))


def create_dynamic_instance(parent):
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    return unreal.MaterialLibrary.create_dynamic_material_instance(world, parent)


def create_material(material_def, paints, texture_assets, persistent=False):
    # Returns the applied material, its paint parameters and the shared parent material
    variant_key = ''
    if persistent:
        variant_key = json.dumps({
            'paints': paints if material_def.get('paint') else None,
            'textures': [
                texture_assets[index].get_path_name()
                for index in _texture_indices(material_def)
            ],
        }, sort_keys=True, separators=(',', ':'))
    cache_key = (material_def['cache_key'], variant_key)
    cached = _material_cache.get(cache_key)
    if cached is None:
        cached = _material_cache[cache_key] = _build_material(
            material_def, paints, texture_assets, variant_key)
    parent, paint_params = cached
    if persistent:
        return parent, paint_params, parent
    instance = create_dynamic_instance(parent)
    apply_paints(instance, paint_params, paints)
    return instance, paint_params, parent
