# BNGLink tab of the 3D view sidebar, driving receiver.py

import os

import bpy

from bnglink import settings
import receiver

try:
    import import_take
except ImportError:
    import_take = None


def set_live_preview(props, value):
    receiver.live_preview = value


class BNGLinkSettings(bpy.types.PropertyGroup):
    beamng_host: bpy.props.StringProperty(name='BeamNG IP', default=settings.HOST)
    beamng_port: bpy.props.IntProperty(name='Port', default=settings.BEAMNG_PORT, min=1, max=65535)
    receiver_host: bpy.props.StringProperty(name='Receiver IP', default=settings.HOST)
    receiver_port: bpy.props.IntProperty(name='Port', default=settings.RECEIVER_PORT, min=1, max=65535)
    live_preview: bpy.props.BoolProperty(name='Live preview', get=lambda props: receiver.live_preview, set=set_live_preview)
    record_path: bpy.props.StringProperty(name='Record file', default=settings.default_takes_dir(), subtype='FILE_PATH')
    # Takes are recorded on their own time base, the playback framerate is chosen when loading
    framerate: bpy.props.FloatProperty(name='Framerate', default=24.0, min=1.0, precision=3)


class BNGLINK_OT_toggle_receiver(bpy.types.Operator):
    bl_idname = 'bnglink.toggle_receiver'
    bl_label = 'Start or stop receiver'
    bl_description = 'Start or stop the live link: BeamNG vehicles are built and updated in the scene'

    def execute(self, context):
        if receiver.link():
            receiver.stop()
        else:
            props = context.scene.bnglink
            receiver.start(props.receiver_host, props.receiver_port, (props.beamng_host, props.beamng_port))
        return {'FINISHED'}


class BNGLINK_OT_toggle_recording(bpy.types.Operator):
    bl_idname = 'bnglink.toggle_recording'
    bl_label = 'Start or stop recording to file'
    bl_description = 'Start or stop recording the live link to a USD take, saved to the record file when stopped'

    @classmethod
    def poll(cls, context):
        # Blender only shows the message when poll fails
        cls.poll_message_set('Start the receiver first')
        return receiver.link() is not None

    def execute(self, context):
        usd = receiver.link()['usd']
        if usd.active:
            usd.stop()
        else:
            usd.start(bpy.path.abspath(context.scene.bnglink.record_path))
        return {'FINISHED'}


class BNGLINK_OT_load_record(bpy.types.Operator):
    bl_idname = 'bnglink.load_record'
    bl_label = 'Load record from file'
    bl_description = 'Import a USD take as keyframed vehicles at the chosen framerate'

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default='*.usdz', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        cls.poll_message_set('Loading records needs usd-core (pxr)')
        return import_take is not None

    def invoke(self, context, event):
        path = bpy.path.abspath(context.scene.bnglink.record_path)
        # The file browser reads a path without a trailing separator as a file name
        self.filepath = os.path.join(path, '') if os.path.isdir(path) else path
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        import_take.import_take(self.filepath, context.scene.bnglink.framerate)
        return {'FINISHED'}


class BNGLINK_OT_clear_textures_cache(bpy.types.Operator):
    bl_idname = 'bnglink.clear_textures_cache'
    bl_label = 'Clear textures cache'
    bl_description = 'Delete the cached vehicle textures, except those used by vehicles still in the scene'

    @classmethod
    def poll(cls, context):
        cls.poll_message_set('Stop the receiver first')
        return receiver.link() is None

    def execute(self, context):
        receiver.clear_textures_cache()
        return {'FINISHED'}


def big_button(layout, operator, text):
    row = layout.row()
    row.scale_y = 1.5
    row.operator(operator.bl_idname, text=text)


def address_rows(layout, label, props, host, port):
    layout.label(text=label)
    split = layout.split(factor=0.08)
    split.label(text='IP')
    split = split.split(factor=0.6)
    split.prop(props, host, text='')
    split.prop(props, port)


class BNGLINK_PT_panel(bpy.types.Panel):
    bl_label = 'BNGLink'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BNGLink'

    def draw(self, context):
        layout = self.layout
        props = context.scene.bnglink
        link = receiver.link()

        if link and link['session'].connected:
            host, port = link['session'].beamng_addr
            layout.label(text=f'Connected to {host}:{port}', icon='LINKED')
        else:
            layout.label(text='Disconnected', icon='UNLINKED')

        box = layout.box()
        box.label(text='Connection settings')
        # Read when the receiver starts
        box.enabled = link is None
        address_rows(box, 'BeamNG', props, 'beamng_host', 'beamng_port')
        address_rows(box, 'Receiver', props, 'receiver_host', 'receiver_port')

        big_button(layout, BNGLINK_OT_toggle_receiver, 'Stop receiver' if link else 'Start receiver')

        box = layout.box()
        box.label(text='Record and replay')
        recording = link and link['usd'].active
        big_button(box, BNGLINK_OT_toggle_recording, 'Stop recording to file' if recording else 'Start recording to file')
        row = box.row()
        row.prop(props, 'live_preview')
        row.prop(props, 'record_path', text='')
        big_button(box, BNGLINK_OT_load_record, 'Load record from file')
        box.prop(props, 'framerate')
        box.operator(BNGLINK_OT_clear_textures_cache.bl_idname)


register_classes, unregister_classes = bpy.utils.register_classes_factory((
    BNGLinkSettings,
    BNGLINK_OT_toggle_receiver,
    BNGLINK_OT_toggle_recording,
    BNGLINK_OT_load_record,
    BNGLINK_OT_clear_textures_cache,
    BNGLINK_PT_panel,
))


def register():
    register_classes()
    bpy.types.Scene.bnglink = bpy.props.PointerProperty(type=BNGLinkSettings)


def unregister():
    receiver.stop()
    del bpy.types.Scene.bnglink
    unregister_classes()
