import bpy

PAINT_SCALAR_DEFAULTS = {'metallic': 0.0, 'roughness': 0.5, 'clearcoat': 0.0, 'clearcoatRoughness': 1.0}
WHITE = (1.0, 1.0, 1.0, 1.0)
FLAT_NORMAL = (0.0, 0.0, 1.0)
CHANNELS = {'R': 0, 'G': 1, 'B': 2}
LAYER_MIX_TYPES = {
    'base': 'RGBA', 'metallic': 'FLOAT', 'roughness': 'FLOAT', 'ao': 'FLOAT', 'emissive': 'VECTOR', 'clearcoat': 'FLOAT',
    'clearcoat_roughness': 'FLOAT', 'specular': 'FLOAT', 'anisotropy': 'FLOAT', 'normal': 'VECTOR',
}

_material_cache = {}


def _rgba(values):
    return (list(values) + [1.0] * (4 - len(values)))[:4]


class Graph:
    # Values plugged into sockets are sockets or constants
    def __init__(self, tree, images):
        self.nodes = tree.nodes
        self.links = tree.links
        self.images = images
        self.paint_nodes = []

    def node(self, type_name, **attrs):
        node = self.nodes.new(type_name)
        for key, value in attrs.items():
            setattr(node, key, value)
        return node

    def plug(self, socket, value):
        if isinstance(value, (int, float, tuple, list)):
            socket.default_value = value
        else:
            self.links.new(value, socket)

    def math(self, operation, a, b, c=None, clamp=False):
        node = self.node('ShaderNodeMath', operation=operation)
        if clamp:
            node.use_clamp = True
        for index, value in enumerate((a, b, c)):
            if value is not None:
                self.plug(node.inputs[index], float(value) if isinstance(value, (int, float)) else value)
        return node.outputs['Value']

    def vmath(self, operation, a, b=None, c=None, scale=None, output='Vector'):
        node = self.node('ShaderNodeVectorMath', operation=operation)
        for index, value in enumerate((a, b, c)):
            if value is not None:
                self.plug(node.inputs[index], value)
        if scale is not None:
            self.plug(node.inputs['Scale'], scale)
        return node.outputs[output]

    def mix(self, data_type, a, b, factor):
        node = self.node('ShaderNodeMix', data_type=data_type)
        self.links.new(factor, node.inputs['Factor'])
        self.links.new(a, node.inputs['A'])
        self.links.new(b, node.inputs['B'])
        return node.outputs['Result']

    def mul_color(self, a, b):
        node = self.node('ShaderNodeMixRGB', blend_type='MULTIPLY')
        node.inputs['Fac'].default_value = 1.0
        self.links.new(a, node.inputs['Color1'])
        self.links.new(b, node.inputs['Color2'])
        return node.outputs['Color']

    def sum(self, sockets, add):
        result = sockets[0]
        for socket in sockets[1:]:
            result = add('ADD', result, socket)
        return result

    def rgb(self, rgba=WHITE):
        node = self.node('ShaderNodeRGB')
        node.outputs[0].default_value = rgba
        return node.outputs['Color']

    def value(self, value):
        node = self.node('ShaderNodeValue')
        node.outputs[0].default_value = value
        return node.outputs[0]

    def separate(self, color):
        node = self.node('ShaderNodeSeparateColor', mode='RGB')
        self.links.new(color, node.inputs['Color'])
        return node.outputs['Red'], node.outputs['Green'], node.outputs['Blue']

    def texture(self, index, non_color, use_uv1=False, uv_scale=None):
        node = self.node('ShaderNodeTexImage', image=self.images[index])
        if non_color:
            node.image.colorspace_settings.name = 'Non-Color'
        uv = self.node('ShaderNodeUVMap', uv_map='uv2' if use_uv1 else 'uv1').outputs['UV']
        if uv_scale:
            uv = self.vmath('MULTIPLY', uv, (uv_scale[0], uv_scale[1], 1.0))
        self.links.new(uv, node.inputs['Vector'])
        return node

    def channel(self, index, use_uv1=False, channel='R', uv_scale=None):
        return self.separate(self.texture(index, True, use_uv1, uv_scale).outputs['Color'])[CHANNELS[channel]]

    def _normal_z(self, color):
        # BC5 drops the blue channel, so the game rebuilds Z from XY (decodeNormalRG) and never
        # reads it. Some maps leave blue empty, which would shade them inside out here.
        # Zeroing blue before the dot product keeps the unpack to a single vector node.
        xy = self.vmath('MULTIPLY_ADD', color, (2.0, 2.0, 0.0), (-1.0, -1.0, 0.0))
        length2 = self.vmath('DOT_PRODUCT', xy, xy, output='Value')
        z = self.math('SQRT', self.math('SUBTRACT', 1.0, length2, clamp=True), 0.0)
        return self.math('MULTIPLY_ADD', z, 0.5, 0.5)

    def normal_map(self, index, use_uv1, strength, uv_scale=None):
        # BeamNG normal maps are DirectX style, Blender expects the green channel flipped
        color = self.texture(index, True, use_uv1, uv_scale).outputs['Color']
        red, green, _ = self.separate(color)
        combine = self.node('ShaderNodeCombineColor', mode='RGB')
        self.links.new(red, combine.inputs['Red'])
        self.links.new(self.math('SUBTRACT', 1.0, green), combine.inputs['Green'])
        self.links.new(self._normal_z(color), combine.inputs['Blue'])
        node = self.node('ShaderNodeNormalMap')
        node.inputs['Strength'].default_value = strength
        self.links.new(combine.outputs['Color'], node.inputs['Color'])
        return node.outputs['Normal']

    def paint_value(self, paint, slot, field):
        if field == 'baseColor':
            rgb = paint.get('baseColor', WHITE)[:3]
            node = self.node('ShaderNodeRGB')
            node.outputs[0].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
            socket = node.outputs['Color']
        else:
            node = self.node('ShaderNodeValue')
            node.outputs[0].default_value = paint.get(field, PAINT_SCALAR_DEFAULTS[field])
            socket = node.outputs[0]
        self.paint_nodes.append({'node': node, 'slot': slot, 'field': field})
        return socket


def _uv_key(texture_key):
    return f'{texture_key[:-len("Texture")]}UseUV1'


def _blend_detail_scalar(g, base, detail, strength):
    centered = g.math('SUBTRACT', g.math('MULTIPLY', detail, 2.0), 1.0)
    return g.math('ADD', base, g.math('MULTIPLY', centered, max(0.0, strength)), clamp=True)


def _blend_detail_color(g, base, detail, strength):
    centered = g.vmath('SUBTRACT', g.vmath('MULTIPLY', detail, (2.0, 2.0, 2.0)), (1.0, 1.0, 1.0))
    added = g.vmath('ADD', base, g.vmath('SCALE', centered, scale=max(0.0, strength)))
    return g.vmath('MINIMUM', g.vmath('MAXIMUM', added, (0.0, 0.0, 0.0)), (1.0, 1.0, 1.0))


def _layer_scalar(g, layer, texture_key, factor_key, default, detail_key=None, strength_key=None, opacity_detail=False):
    value = g.value(1.0)
    if layer.get(texture_key) is not None:
        value = g.channel(layer[texture_key], layer.get(_uv_key(texture_key)))
    detail_index = layer.get(detail_key) if detail_key else None
    if detail_index is not None:
        detail = g.channel(detail_index, layer.get(_uv_key(detail_key)), 'R', layer.get('detailScale', (2.0, 2.0)))
        blended = _blend_detail_scalar(g, g.value(1.0) if opacity_detail else value, detail, layer.get(strength_key, 1.0))
        value = g.math('MULTIPLY', value, blended) if opacity_detail else blended
    return g.math('MULTIPLY', value, layer.get(factor_key, default))


def _layer_color(g, layer):
    factor = g.rgb(_rgba(layer.get('baseColorFactor', WHITE)))
    if layer.get('baseColorTexture') is None:
        value = g.rgb()
    else:
        value = g.texture(layer['baseColorTexture'], False, layer.get('baseColorUseUV1')).outputs['Color']
    if layer.get('detailBaseColorTexture') is not None:
        detail = g.texture(layer['detailBaseColorTexture'], False, layer.get('detailBaseColorUseUV1'),
                           layer.get('detailScale', (2.0, 2.0)))
        value = _blend_detail_color(g, value, detail.outputs['Color'], layer.get('detailBaseColorStrength', 1.0))
    value = g.mul_color(value, factor)
    if layer.get('overlayTexture') is not None:
        overlay = g.texture(layer['overlayTexture'], False, layer.get('overlayUseUV1'))
        value = g.mix('RGBA', value, overlay.outputs['Color'], overlay.outputs['Alpha'])
    return value


def _layer_specular(g, layer):
    factor = layer.get('specularFactor')
    factor = sum(factor[:3]) / len(factor[:3]) if factor else 1.0
    if layer.get('specularTexture') is None:
        result = g.value(factor)
    else:
        result = g.math('MULTIPLY', g.channel(layer['specularTexture'], layer.get('specularUseUV1')), factor)
    if layer.get('reflectivityTexture') is not None:
        texture = g.texture(layer['reflectivityTexture'], True, layer.get('reflectivityUseUV1'))
        result = g.math('MAXIMUM', result, g.math('MULTIPLY', texture.outputs['Alpha'], layer.get('reflectivityFactor', 1.0)))
    return result


def _layer_emissive(g, layer):
    result = g.rgb(_rgba(layer.get('emissiveFactor', (0.0, 0.0, 0.0))))
    if layer.get('emissiveTexture') is not None:
        texture = g.texture(layer['emissiveTexture'], False, layer.get('emissiveUseUV1'))
        result = g.mul_color(texture.outputs['Color'], result)
    intensity = layer.get('emissiveIntensity')
    if intensity is not None and intensity >= 0:
        result = g.vmath('SCALE', result, scale=intensity)
    return result


def _layer_normal(g, layer):
    if layer.get('normalTexture') is None:
        normal = g.rgb(FLAT_NORMAL + (1.0,))
    else:
        normal = g.normal_map(layer['normalTexture'], layer.get('normalUseUV1'), layer.get('normalStrength', 1.0))
    if layer.get('detailNormalTexture') is None:
        return normal
    detail = g.normal_map(layer['detailNormalTexture'], layer.get('detailNormalUseUV1'),
                          layer.get('detailNormalStrength', 1.0), layer.get('detailScale', (2.0, 2.0)))
    return g.vmath('NORMALIZE', g.vmath('ADD', normal, g.vmath('SUBTRACT', detail, FLAT_NORMAL)))


def _blend_layer(g, result, layer, keys):
    for key in keys:
        result[key] = g.mix(LAYER_MIX_TYPES[key], result[key], layer[key], layer['opacity'])


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
            'anisotropy': g.value(layer.get('anisotropy', 0.0)),
            'normal': _layer_normal(g, layer),
        }
        if layer.get('legacyEmissive'):
            values['emissive'] = values['base']
            values['base'] = g.rgb((0.0, 0.0, 0.0, 1.0))
        layers.append(values)

    result = dict(layers[0])
    for layer in layers[1:]:
        _blend_layer(g, result, layer, LAYER_MIX_TYPES)
        under = g.math('MULTIPLY', result['opacity'], g.math('SUBTRACT', 1.0, layer['opacity']))
        result['opacity'] = g.math('ADD', layer['opacity'], under)
    result['normal'] = g.vmath('NORMALIZE', result['normal'])
    return result, layers


def _substrate_channel(g, material_def, name, default):
    index = material_def.get(f'{name}Texture')
    value = None if index is None else g.channel(index, material_def.get(f'{name}UseUV1'))
    factor = material_def.get(f'{name}Factor')
    if factor is not None:
        value = g.math('MULTIPLY', 1.0 if value is None else value, factor)
    return g.value(default) if value is None else value


def _palette_weights(g, palette):
    # Per-channel paint weights from the palette mask; alpha is the paint coverage
    red, green, blue = g.separate(palette.outputs['Color'])
    alpha = palette.outputs['Alpha']
    total = g.math('ADD', 1e-6, g.math('ADD', g.math('ADD', red, green), blue))
    weights = [g.math('MULTIPLY', g.math('DIVIDE', channel, total), alpha) for channel in (red, green, blue)]
    return weights + [g.math('SUBTRACT', 1.0, alpha)]


def _blend_paint_scalar(g, weights, paints, field, enabled, base_value=1.0):
    if not enabled:
        return g.value(base_value)
    terms = [g.math('MULTIPLY', weight, g.paint_value(paint, slot, field))
             for slot, (weight, paint) in enumerate(zip(weights[:3], paints))]
    return g.sum(terms + [g.math('MULTIPLY', weights[3], base_value)], g.math)


def _blend_paint_color(g, weights, paints, enabled):
    if not enabled:
        return g.rgb()
    terms = [g.vmath('SCALE', g.paint_value(paint, slot, 'baseColor'), scale=weight)
             for slot, (weight, paint) in enumerate(zip(weights[:3], paints))]
    return g.sum(terms + [g.vmath('SCALE', g.rgb(), scale=weights[3])], g.vmath)


def _scale_socket(g, value, factor):
    return value if factor is None else g.math('MULTIPLY', value, factor)


def _blend_palette(g, data_type, substrate, paint, mask, palette_on_top):
    a, b = (substrate, paint) if palette_on_top else (paint, substrate)
    return g.mix(data_type, a, b, mask)


def _palette_paint(g, bsdf, material_def, paint_def, paints, layers):
    # Connects metallic, roughness and coat, returns the base color
    base_color_factor = material_def.get('baseColorFactor')
    if material_def.get('baseColorTexture') is None:
        substrate_base = g.rgb(_rgba(base_color_factor)) if base_color_factor is not None else g.rgb()
    else:
        substrate_base = g.texture(material_def['baseColorTexture'], False, material_def.get('baseColorUseUV1')).outputs['Color']
        if base_color_factor is not None:
            substrate_base = g.mul_color(substrate_base, g.rgb(_rgba(base_color_factor)))
    substrate_metallic = _substrate_channel(g, material_def, 'metallic', 0.0)
    substrate_roughness = _substrate_channel(g, material_def, 'roughness', 0.5)

    weights = _palette_weights(g, g.texture(paint_def['paletteTexture'], True, paint_def['paletteUseUV1']))
    color = _blend_paint_color(g, weights, paints, paint_def['paletteBaseColor'])
    if paint_def.get('colorMapTexture') is not None:
        texture = g.texture(paint_def['colorMapTexture'], False, paint_def['diffuseUseUV1'])
        color = g.mul_color(texture.outputs['Color'], color)
    if paint_def.get('baseColorFactor') is not None:
        color = g.mul_color(color, g.rgb(_rgba(paint_def['baseColorFactor'])))
    metallic = _blend_paint_scalar(g, weights, paints, 'metallic', paint_def['paletteMetallic'])
    roughness = _blend_paint_scalar(g, weights, paints, 'roughness', paint_def['paletteRoughness'])
    clearcoat = _blend_paint_scalar(g, weights, paints, 'clearcoat', paint_def['paletteClearCoat'], 0.0)
    clearcoat_roughness = _blend_paint_scalar(g, weights, paints, 'clearcoatRoughness', paint_def['paletteClearCoatRoughness'])

    metallic = _scale_socket(g, metallic, paint_def.get('paintMetallicFactor'))
    roughness = _scale_socket(g, roughness, paint_def.get('paintRoughnessFactor'))
    clearcoat = _scale_socket(g, clearcoat, paint_def.get('clearCoatFactor'))
    if paint_def.get('clearCoatTexture') is not None:
        clearcoat = g.math('MULTIPLY', clearcoat, g.channel(paint_def['clearCoatTexture'], paint_def.get('clearCoatUseUV1')))
    clearcoat_roughness = _scale_socket(g, clearcoat_roughness, paint_def.get('clearCoatRoughnessFactor'))

    mask = g.value(1.0)
    if paint_def.get('opacityTexture') is not None:
        mask = g.channel(paint_def['opacityTexture'], paint_def['opacityUseUV1'])
    mask = _scale_socket(g, mask, paint_def.get('opacityFactor'))

    palette_on_top = paint_def['paletteOnTop']
    result = {
        'base': _blend_palette(g, 'RGBA', substrate_base, color, mask, palette_on_top),
        'metallic': _blend_palette(g, 'FLOAT', substrate_metallic, metallic, mask, palette_on_top),
        'roughness': _blend_palette(g, 'FLOAT', substrate_roughness, roughness, mask, palette_on_top),
        'clearcoat': _blend_palette(g, 'FLOAT', g.value(0.0), clearcoat, mask, palette_on_top),
        'clearcoat_roughness': _blend_palette(g, 'FLOAT', g.value(1.0), clearcoat_roughness, mask, palette_on_top),
    }
    # Layers above the paint (liveries, decals) are drawn over
    for layer in layers[paint_def.get('firstOverlayLayer', len(layers)):]:
        _blend_layer(g, result, layer, list(result))
    g.links.new(result['metallic'], bsdf.inputs['Metallic'])
    g.links.new(result['roughness'], bsdf.inputs['Roughness'])
    g.links.new(result['clearcoat'], bsdf.inputs['Coat Weight'])
    g.links.new(result['clearcoat_roughness'], bsdf.inputs['Coat Roughness'])
    return result['base']


def _build_material(material_def, images, paints):
    mat = bpy.data.materials.new(material_def['name'])
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    g = Graph(mat.node_tree, images)
    links = g.links

    result, layers = layer_values(g, material_def)
    paint_def = material_def.get('paint')
    if paint_def and paint_def['mode'] == 'palette':
        base = _palette_paint(g, bsdf, material_def, paint_def, paints, layers)
    else:
        base = result['base']
        if paint_def:
            base = g.mul_color(base, g.paint_value(paints[0] if paints else {}, 0, 'baseColor'))
        elif not material_def.get('pbr15'):
            links.new(result['specular'], bsdf.inputs['Specular IOR Level'])
        links.new(result['metallic'], bsdf.inputs['Metallic'])
        links.new(result['roughness'], bsdf.inputs['Roughness'])
        links.new(result['anisotropy'], bsdf.inputs['Anisotropic'])
        links.new(result['clearcoat'], bsdf.inputs['Coat Weight'])
        links.new(result['clearcoat_roughness'], bsdf.inputs['Coat Roughness'])
    # Blender has no ambient occlusion input, so it darkens the base color
    links.new(g.mul_color(base, result['ao']), bsdf.inputs['Base Color'])
    links.new(result['normal'], bsdf.inputs['Normal'])
    links.new(result['emissive'], bsdf.inputs['Emission Color'])
    bsdf.inputs['Emission Strength'].default_value = 1.0
    opacity = result['opacity']
    if material_def['alphaTest']:
        opacity = g.math('GREATER_THAN', opacity, material_def['alphaRef'] / 255.0)
    if material_def['alphaTest'] or material_def['transparent']:
        links.new(opacity, bsdf.inputs['Alpha'])

    if material_def['alphaTest'] or (material_def['transparent'] and material_def['zWrite']):
        mat.surface_render_method = 'DITHERED'
    elif material_def['transparent']:
        mat.surface_render_method = 'BLENDED'
    # The engine only draws a material's back faces when it is flagged double sided,
    # while thin sheets (license plates) wound on both sides would z-fight
    mat.use_backface_culling = not material_def.get('doubleSided')
    return mat, g.paint_nodes


def apply_paints(paint_nodes, paints):
    for entry in paint_nodes:
        if entry['slot'] < len(paints):
            value = paints[entry['slot']][entry['field']]
            entry['node'].outputs[0].default_value = (value[0], value[1], value[2], 1.0) if entry['field'] == 'baseColor' else value


def prune_cache():
    # Returns the images of the materials in use. Unused cached materials are dropped:
    # they are reused by key, so they must not outlive the texture files they point to
    for key, (mat, _) in list(_material_cache.items()):
        if not mat.users:
            del _material_cache[key]
    return {node.image for mat in bpy.data.materials if mat.users and mat.node_tree
            for node in mat.node_tree.nodes if node.type == 'TEX_IMAGE' and node.image}


def create_bpy_material(material_def, images, paints):
    # Returns the material and its paint nodes; materials with paint nodes are copied per vehicle
    cached = _material_cache.get(material_def['cache_key'])
    if cached is None:
        mat, paint_nodes = _build_material(material_def, images, paints)
        _material_cache[material_def['cache_key']] = (mat, [dict(entry, node=entry['node'].name) for entry in paint_nodes])
        return mat, paint_nodes
    mat, templates = cached
    if not templates:
        return mat, []
    mat = mat.copy()
    paint_nodes = [dict(entry, node=mat.node_tree.nodes[entry['node']]) for entry in templates]
    apply_paints(paint_nodes, paints)
    return mat, paint_nodes
