import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _AnyAttr:
    def __getattr__(self, name):
        return name


def _load_blender_materials():
    sys.modules.setdefault('bpy', types.ModuleType('bpy'))
    spec = importlib.util.spec_from_file_location(
        'bnglink_blender_materials', ROOT / 'blender' / 'materials.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_ue_materials():
    unreal = types.ModuleType('unreal')
    unreal.MaterialSamplerType = _AnyAttr()
    unreal.BlendMode = _AnyAttr()
    unreal.MaterialEditingLibrary = _AnyAttr()
    unreal.MaterialProperty = _AnyAttr()
    unreal.MaterialShadingModel = _AnyAttr()
    unreal.LinearColor = lambda r, g, b, a: (r, g, b, a)
    sys.modules['unreal'] = unreal
    spec = importlib.util.spec_from_file_location(
        'bnglink_ue_materials', ROOT / 'ue5' / 'materials.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BlenderGraph:
    def value(self, value=1.0):
        return ('value', value)

    def rgb(self, rgba=(1.0, 1.0, 1.0, 1.0)):
        return ('rgb', rgba)

    def channel(self, index, use_uv1=None, channel='R', scale=None):
        return ('channel', index, use_uv1, channel, scale)

    def texture(self, index, mask=False, use_uv1=None, scale=None):
        return types.SimpleNamespace(outputs={
            'Color': ('texture', index, mask, use_uv1, scale, 'Color'),
            'Alpha': ('texture', index, mask, use_uv1, scale, 'Alpha'),
        })

    def normal_map(self, index, use_uv1=None, strength=1.0, scale=None):
        return ('normal_map', index, use_uv1, strength, scale)

    def mul_color(self, a, b):
        return ('mul_color', a, b)

    def math(self, operation, a, b, c=None, clamp=False):
        return ('math', operation, a, b, c, clamp)

    def vmath(self, operation, a, b=None, c=None, scale=None, output='Vector'):
        return ('vmath', operation, a, b, c, scale, output)

    def mix(self, data_type, a, b, factor):
        return ('mix', data_type, a, b, factor)


class UeGraph:
    def const(self, value):
        return ('const', value)

    def rgb(self, rgb=(1.0, 1.0, 1.0)):
        return ('rgb', rgb)

    def sample(self, index, sampler, uv=None):
        return ('sample', index, sampler, uv)

    def detail_uv(self, layer, use_uv1):
        return ('detail_uv', use_uv1)

    def blend_detail(self, base, detail, strength):
        return ('blend_detail', base, detail, strength)

    def mul(self, a, b):
        return ('mul', a, b)

    def add(self, a, b):
        return ('add', a, b)

    def sub(self, a, b):
        return ('sub', a, b)

    def lerp(self, a, b, alpha):
        return ('lerp', a, b, alpha)

    def op(self, name, **inputs):
        return ('op', name, tuple(sorted(inputs.items())))


class MaterialLayerTests(unittest.TestCase):
    def test_sender_pbr_alpha_exports_layer_opacity(self):
        import lua_sender

        if lua_sender.LuaRuntime is None:
            self.skipTest('needs lupa (pip install lupa)')

        sender = lua_sender.LuaSender()
        sender.lua.globals().extensions.load('BNGLink_BNGLink')
        sender.ext('BNGLink_BNGLink').initContent()
        sender.run(60)

        materials = {
            material['name']: material
            for material in sender.messages('init')[0]['materials']
        }

        self.assertAlmostEqual(0.35, materials['mirror_mat']['layers'][1]['opacityFactor'])

    def test_blender_nits_are_scaled_to_receiver_strength(self):
        module = _load_blender_materials()
        result = module._layer_emissive(BlenderGraph(), {
            'emissiveTexture': 1,
            'emissiveFactor': (1.0, 1.0, 1.0),
            'emissiveIntensity': 100,
        })

        self.assertEqual((
            'vmath', 'SCALE',
            ('mul_color', ('texture', 1, False, None, None, 'Color'),
             ('rgb', [1.0, 1.0, 1.0, 1.0])),
            None, None, 100 * module.EMISSIVE_NITS_SCALE, 'Vector'), result)

    def test_blender_constant_upper_layer_does_not_increase_coverage(self):
        module = _load_blender_materials()
        result, layers = module.layer_values(BlenderGraph(), {'layers': [
            {'opacityTexture': 0},
            {'opacityFactor': 0.35},
        ]})

        self.assertIsNone(layers[1]['coverage'])
        self.assertEqual(layers[0]['opacity'], result['opacity'])

    def test_ue_nits_are_scaled_to_receiver_strength(self):
        module = _load_ue_materials()
        result = module._layer_emissive(UeGraph(), {
            'emissiveTexture': 1,
            'emissiveFactor': (1.0, 1.0, 1.0),
            'emissiveIntensity': 100,
        })

        self.assertEqual((
            'mul',
            ('mul', (('sample', 1, module.COLOR, None), ''), ('rgb', (1.0, 1.0, 1.0))),
            100 * module.EMISSIVE_NITS_SCALE), result)

    def test_ue_constant_upper_layer_does_not_increase_coverage(self):
        module = _load_ue_materials()
        result, layers = module.layer_values(UeGraph(), {'layers': [
            {'opacityTexture': 0},
            {'opacityFactor': 0.35},
        ]})

        self.assertIsNone(layers[1]['coverage'])
        self.assertEqual(layers[0]['opacity'], result['opacity'])

