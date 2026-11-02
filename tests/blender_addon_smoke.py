# Smoke test of the Blender add-on, run from the source tree:
# blender -b --factory-startup --python-exit-code 1 --python tests/blender_addon_smoke.py

import os
import socket
import sys
import tempfile
import unittest

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blender as addon
from bnglink.transport import HEADER, HEADER_MAGIC, KIND_CONTROL
import materials
import receiver
import textures


class BlenderAddonTests(unittest.TestCase):
    def setUp(self):
        addon.register()
        self.props = bpy.context.scene.bnglink

    def tearDown(self):
        addon.unregister()

    def test_unregister_removes_settings(self):
        addon.unregister()
        self.assertFalse(hasattr(bpy.types.Scene, 'bnglink'))
        addon.register()

    def test_live_preview_drives_receiver(self):
        self.props.live_preview = False
        self.assertFalse(receiver.live_preview)
        self.props.live_preview = True
        self.assertTrue(receiver.live_preview)

    def test_receiver_connects_and_gates_buttons(self):
        self.assertFalse(bpy.ops.bnglink.toggle_recording.poll())
        self.assertTrue(bpy.ops.bnglink.clear_textures_cache.poll())

        beamng = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        beamng.bind(('127.0.0.1', 0))
        with bpy.context.temp_override(screen=bpy.data.screens[0]):
            receiver.start('127.0.0.1', 0, beamng.getsockname())
        session = receiver.link()['session']
        self.assertFalse(session.connected)
        self.assertTrue(bpy.ops.bnglink.toggle_recording.poll())
        self.assertFalse(bpy.ops.bnglink.clear_textures_cache.poll())

        beamng.sendto(HEADER.pack(HEADER_MAGIC, KIND_CONTROL, 1, 0, 1) + b'init_completed', session.comm.sock.getsockname())
        session.poll()
        self.assertTrue(session.connected)

        receiver.stop()
        beamng.close()
        self.assertIsNone(receiver.link())

    def test_clear_cache_keeps_textures_in_use(self):
        with tempfile.TemporaryDirectory() as tex_dir:
            textures.TEX_DIR = tex_dir
            files = ['0_paint.png', '1_glass.png', 'V1_0.0001.png', 'V1_0.0002.png', 'V2_0.0001.png']
            for name in files:
                open(os.path.join(tex_dir, name), 'wb').close()
            images = [bpy.data.images.new(name, 1, 1) for name in files[:3]]
            for image in images:
                image.filepath = os.path.join(tex_dir, image.name)
            used_image, unused_image, sequence = images
            sequence.source = 'SEQUENCE'
            textures._image_cache.update({'used': used_image, 'unused': unused_image})

            used_material = self.material([used_image, sequence])
            materials._material_cache.update({'used': (used_material, []), 'unused': (self.material([unused_image]), [])})
            mesh = bpy.data.meshes.new('vehicle')
            mesh.materials.append(used_material)
            bpy.context.scene.collection.objects.link(bpy.data.objects.new('vehicle', mesh))

            receiver.clear_textures_cache()

            self.assertEqual(['0_paint.png', 'V1_0.0001.png', 'V1_0.0002.png'], sorted(os.listdir(tex_dir)))
            self.assertEqual(['used'], list(textures._image_cache))
            self.assertEqual(['used'], list(materials._material_cache))

    @staticmethod
    def material(images):
        material = bpy.data.materials.new('material')
        material.use_nodes = True
        for image in images:
            material.node_tree.nodes.new('ShaderNodeTexImage').image = image
        return material


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False).result
    if not result.wasSuccessful():
        sys.exit(1)
