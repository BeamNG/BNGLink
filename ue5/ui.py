import os
import sys
import time
import traceback
from pathlib import Path

import unreal

pkg_dir = os.path.dirname(__file__)
root_dir = os.path.dirname(pkg_dir)
for path in (root_dir, pkg_dir):
    if path not in sys.path:
        sys.path.insert(0, path)

import main as receiver
from bnglink import settings

try:
    import import_take as take_import
except ImportError:
    take_import = None

ASSET_DIR = '/Game/BNGLink/Generated/Editor'
ASSET_NAME = 'BNGLink'
ASSET_PATH = f'{ASSET_DIR}/{ASSET_NAME}'
FONT = unreal.load_asset('/Engine/EngineFonts/Roboto.Roboto')
if FONT is None:
    raise RuntimeError('Could not load the Unreal default font')
BODY_FONT = unreal.SlateFontInfo(font_object=FONT, size=9.0)
HEADING_FONT = unreal.SlateFontInfo(font_object=FONT, size=12.0)
STATUS_FONT = unreal.SlateFontInfo(font_object=FONT, size=10.0)
PANEL_COLOR = unreal.LinearColor(0.06, 0.06, 0.06, 0.65)
PRIMARY_COLOR = unreal.LinearColor(0.12, 0.32, 0.52, 1.0)
LABEL_WIDTH = 60.0


def _add(
        blueprint, widget_class, name, parent='', slot_padding=None,
        slot_fill=False, slot_center=False, **properties):
    widget = unreal.EditorUtilityLibrary.add_source_widget(
        blueprint, widget_class, name, parent)
    for key, value in properties.items():
        widget.set_editor_property(key, value)
    if slot_padding:
        widget.slot.set_padding(unreal.Margin(*slot_padding))
    if slot_fill:
        widget.slot.set_size(unreal.SlateChildSize(1.0, unreal.SlateSizeRule.FILL))
    if slot_center:
        widget.slot.set_vertical_alignment(unreal.VerticalAlignment.V_ALIGN_CENTER)
    return widget


def _select_take_file(default_path):
    from tkinter import Tk, filedialog

    path = Path(default_path)
    directory = path if path.is_dir() else path.parent
    filename = '' if path.is_dir() else path.name
    root = Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        return filedialog.askopenfilename(
            parent=root,
            title='Load BNGLink Take',
            initialdir=str(directory),
            initialfile=filename,
            filetypes=(('USD Zip Archive', '*.usdz'),),
        )
    finally:
        root.destroy()


def _text(blueprint, name, parent, text, font=BODY_FONT, baseline=False, **layout):
    widget = _add(
        blueprint, unreal.TextBlock, name, parent, text=text, font=font, **layout)
    if baseline:
        widget.set_render_translation(unreal.Vector2D(0.0, 1.0))
    return widget


def _button(blueprint, name, parent, text, primary=False, tooltip='', **layout):
    button = _add(
        blueprint, unreal.EditorUtilityButton, name, parent,
        background_color=(
            PRIMARY_COLOR if primary else unreal.LinearColor(1.0, 1.0, 1.0, 1.0)),
        tool_tip_text=tooltip,
        **layout,
    )
    _text(blueprint, f'{name}Text', name, text)
    return button


def _labeled_row(blueprint, name, parent, label):
    row = f'{name}Row'
    _add(
        blueprint, unreal.HorizontalBox, row, parent,
        slot_padding=(0.0, 3.0, 0.0, 0.0))
    label_box = f'{name}LabelBox'
    _add(
        blueprint, unreal.SizeBox, label_box, row, slot_center=True,
        width_override=LABEL_WIDTH, override_width_override=True)
    _text(blueprint, f'{name}Label', label_box, label, baseline=True)
    return row


def _address_row(blueprint, name, parent, port_value):
    row = _labeled_row(blueprint, name, parent, name)
    _add(
        blueprint, unreal.EditorUtilityEditableTextBox, f'{name}Host', row,
        slot_padding=(0.0, 0.0, 6.0, 0.0), slot_fill=True, slot_center=True,
        text=settings.HOST, minimum_desired_width=160.0)
    _add(
        blueprint, unreal.EditorUtilitySpinBox, f'{name}Port', row,
        slot_center=True, value=float(port_value),
        min_value=1.0, max_value=65535.0, override_min_value=True,
        override_max_value=True, min_fractional_digits=0, max_fractional_digits=0,
        min_desired_width=80.0)


def _panel(blueprint, name, title):
    panel = f'{name}Panel'
    content = f'{name}Content'
    _add(
        blueprint, unreal.Border, panel, 'Root',
        slot_padding=(8.0, 6.0, 8.0, 2.0),
        padding=unreal.Margin(10.0, 8.0, 10.0, 10.0), brush_color=PANEL_COLOR)
    _add(blueprint, unreal.VerticalBox, content, panel)
    _text(blueprint, f'{name}Heading', content, title, HEADING_FONT)
    return content


def _build_blueprint():
    unreal.EditorAssetLibrary.make_directory(ASSET_DIR)
    factory = unreal.EditorUtilityWidgetBlueprintFactory()
    factory.set_editor_property('parent_class', unreal.EditorUtilityWidget)
    blueprint = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        ASSET_NAME, ASSET_DIR, unreal.EditorUtilityWidgetBlueprint, factory)
    if blueprint is None:
        raise RuntimeError(f'Could not create {ASSET_PATH}')

    _add(blueprint, unreal.VerticalBox, 'Root')

    _add(
        blueprint, unreal.Border, 'StatusPanel', 'Root',
        slot_padding=(8.0, 8.0, 8.0, 2.0),
        padding=unreal.Margin(10.0, 7.0, 10.0, 7.0), brush_color=PANEL_COLOR)
    _text(blueprint, 'Status', 'StatusPanel', '●  Disconnected', STATUS_FONT)

    connection = _panel(blueprint, 'Connection', 'Connection')
    _address_row(blueprint, 'BeamNG', connection, settings.BEAMNG_PORT)
    _address_row(blueprint, 'Receiver', connection, settings.RECEIVER_PORT)
    _button(
        blueprint, 'ToggleReceiver', connection, 'Start receiver',
        primary=True, tooltip='Start or stop the live BeamNG connection',
        slot_padding=(0.0, 8.0, 0.0, 0.0))

    record = _panel(blueprint, 'Record', 'Record and replay')
    path_row = _labeled_row(blueprint, 'RecordPath', record, 'Take path')
    _add(
        blueprint, unreal.EditorUtilityEditableTextBox, 'RecordPath', path_row,
        slot_fill=True, slot_center=True,
        text=settings.default_takes_dir(), minimum_desired_width=240.0,
        tool_tip_text='A .usdz file or a folder for recorded takes')

    _add(
        blueprint, unreal.HorizontalBox, 'OptionsRow', record,
        slot_padding=(0.0, 6.0, 0.0, 0.0))
    _add(
        blueprint, unreal.EditorUtilityCheckBox, 'LivePreview', 'OptionsRow',
        slot_center=True, checked_state=unreal.CheckBoxState.CHECKED)
    _text(
        blueprint, 'LivePreviewLabel', 'OptionsRow', 'Live preview',
        baseline=True, slot_padding=(4.0, 0.0, 0.0, 0.0),
        slot_fill=True, slot_center=True)
    _text(
        blueprint, 'FramerateLabel', 'OptionsRow', 'Playback FPS',
        baseline=True, slot_center=True)
    _add(
        blueprint, unreal.EditorUtilitySpinBox, 'Framerate', 'OptionsRow',
        slot_padding=(6.0, 0.0, 0.0, 0.0), slot_center=True,
        value=24.0, min_value=1.0, override_min_value=True,
        min_desired_width=80.0)

    _add(
        blueprint, unreal.HorizontalBox, 'ActionsRow', record,
        slot_padding=(0.0, 8.0, 0.0, 0.0))
    _button(
        blueprint, 'ToggleRecording', 'ActionsRow', 'Start recording',
        primary=True, tooltip='Record the live link to a USD take',
        slot_padding=(0.0, 0.0, 3.0, 0.0), slot_fill=True)
    _button(
        blueprint, 'LoadRecord', 'ActionsRow', 'Load take',
        tooltip='Choose and import a USD take',
        slot_padding=(3.0, 0.0, 0.0, 0.0), slot_fill=True)
    _button(
        blueprint, 'ClearTextures', record, 'Clear texture cache',
        tooltip='Delete unused cached source textures',
        slot_padding=(0.0, 6.0, 0.0, 0.0))

    if not unreal.BlueprintEditorLibrary.compile_blueprint(blueprint):
        raise RuntimeError(f'Could not compile {ASSET_PATH}')
    if not unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False):
        raise RuntimeError(f'Could not save {ASSET_PATH}')
    return blueprint


def _ui_action(action):
    def wrapped(self):
        try:
            action(self)
            self.error = None
        except Exception as exc:
            self.error = str(exc)
            unreal.log_error(f'BNGLink Unreal UI: {exc}')
            traceback.print_exc()
        self.refresh()
    return wrapped


class BNGLinkUI:
    def __init__(self, widget):
        self.widget = widget
        self.error = None
        self.last_refresh = 0.0
        self.controls = {
            name: widget.find_child_widget_by_name(name)
            for name in (
                'Status', 'BeamNGHost', 'BeamNGPort', 'ReceiverHost', 'ReceiverPort',
                'ToggleReceiver', 'ToggleReceiverText', 'ToggleRecording',
                'ToggleRecordingText', 'LivePreview', 'RecordPath', 'LoadRecord',
                'Framerate', 'ClearTextures',
            )
        }
        for name, callback in (
            ('ToggleReceiver', self.toggle_receiver),
            ('ToggleRecording', self.toggle_recording),
            ('LoadRecord', self.load_record),
            ('ClearTextures', self.clear_textures),
        ):
            self.controls[name].on_clicked.add_callable(callback)
        self.controls['LivePreview'].on_check_state_changed.add_callable(
            self.set_live_preview)
        self.tick_handle = unreal.register_slate_post_tick_callback(self.tick)
        self.refresh()

    def close(self):
        if self.tick_handle is not None:
            unreal.unregister_slate_post_tick_callback(self.tick_handle)
            self.tick_handle = None

    def tick(self, _delta_time):
        if not unreal.SystemLibrary.is_valid(self.widget):
            self.close()
            return
        now = time.monotonic()
        if now - self.last_refresh >= 0.2:
            self.refresh()
            self.last_refresh = now

    @_ui_action
    def toggle_receiver(self):
        if receiver.link() is not None:
            receiver.stop()
            return
        receiver.start(
            str(self.controls['ReceiverHost'].get_text()).strip(),
            int(self.controls['ReceiverPort'].get_value()),
            (
                str(self.controls['BeamNGHost'].get_text()).strip(),
                int(self.controls['BeamNGPort'].get_value()),
            ),
        )

    @_ui_action
    def toggle_recording(self):
        link = receiver.link()
        if link is None:
            raise RuntimeError('Start the receiver first')
        if link['usd'].active:
            link['usd'].stop()
        else:
            link['usd'].start(os.path.expandvars(os.path.expanduser(
                str(self.controls['RecordPath'].get_text()))))

    def set_live_preview(self, checked):
        receiver.live_preview = bool(checked)

    @_ui_action
    def load_record(self):
        if take_import is None:
            raise RuntimeError('Loading records needs usd-core (pxr)')
        default_path = os.path.expandvars(os.path.expanduser(
            str(self.controls['RecordPath'].get_text())))
        path = _select_take_file(default_path)
        if not path:
            return
        self.controls['RecordPath'].set_text(path)
        take_import.import_take(path, self.controls['Framerate'].get_value())

    @_ui_action
    def clear_textures(self):
        receiver.clear_textures_cache()

    def refresh(self):
        link = receiver.link()
        connected = bool(link and link['session'].connected)
        if self.error:
            status = f'●  Error: {self.error}'
        elif connected:
            host, port = link['session'].beamng_addr
            status = f'●  Connected to {host}:{port}'
        else:
            status = '●  Disconnected'
        self.controls['Status'].set_text(status)

        settings_enabled = link is None
        for name in ('BeamNGHost', 'BeamNGPort', 'ReceiverHost', 'ReceiverPort'):
            self.controls[name].set_is_enabled(settings_enabled)
        self.controls['ToggleReceiverText'].set_text(
            'Stop receiver' if link else 'Start receiver')

        recording = bool(link and link['usd'].active)
        self.controls['ToggleRecording'].set_is_enabled(link is not None)
        self.controls['ToggleRecordingText'].set_text(
            'Stop recording' if recording else 'Start recording')
        self.controls['LivePreview'].set_is_checked(receiver.live_preview)
        self.controls['LoadRecord'].set_is_enabled(take_import is not None)
        self.controls['ClearTextures'].set_is_enabled(link is None)


def open_ui():
    subsystem = unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem)
    previous = getattr(unreal, '_bng_link_ui', None)
    if previous is not None:
        previous.close()
    existing = unreal.load_asset(ASSET_PATH)
    if existing is not None:
        subsystem.unregister_tab_by_id(subsystem.get_tab_id_from_blueprint(existing))
        if not unreal.EditorAssetLibrary.delete_asset(ASSET_PATH):
            raise RuntimeError(f'Could not replace {ASSET_PATH}')

    widget = subsystem.spawn_and_register_tab(_build_blueprint())
    if widget is None:
        raise RuntimeError(f'Could not open {ASSET_PATH}')
    controller = BNGLinkUI(widget)
    unreal._bng_link_ui = controller
    return controller


if __name__ == '__main__':
    open_ui()
