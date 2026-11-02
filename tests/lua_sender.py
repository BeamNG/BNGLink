# Runs the real Lua sender in LuaJIT (pip install lupa) against engine_mock.lua and feeds its datagrams to the real bnglink receiver code, which acks bulk chunks and confirms inits.

import hashlib
import sys
import types
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
LUA_DIR = ROOT / 'beamng' / 'gelua'
sys.path.insert(0, str(ROOT))

if 'numba' not in sys.modules:
    numba = types.ModuleType('numba')
    numba.njit = lambda *args, **kwargs: lambda function: function
    numba.prange = range
    sys.modules['numba'] = numba

import bnglink

try:
    from lupa.luajit21 import LuaRuntime
except ImportError:
    LuaRuntime = None

FRAME_DT = 1 / 60


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, address):
        self.sent.append(data)


class Receiver:
    # Drops the first bulk datagram of chunk 1 so the sender has to resend it
    def __init__(self):
        self.comm = object.__new__(bnglink.UdpComm)
        self.comm.sock = FakeSocket()
        self.comm.peer_addr = None
        self.comm.chunk_states = {}
        self.comm.buffered_bytes = 0
        self.comm.last_completed_transfer_ids = {}
        self.comm.highest_transfer_ids = {}
        self.decoder = bnglink.BngDecoder()
        self.messages = []
        self.replies = []
        self.failed_inits = set()
        self.dropped = False

    def handle(self, datagram):
        _, kind, _, chunk, _ = bnglink.HEADER.unpack_from(datagram)
        if kind == bnglink.KIND_BULK and chunk == 1 and not self.dropped:
            self.dropped = True
            return
        self.comm.handle(datagram, ('127.0.0.1', 6001), self.on_transfer)
        self.replies.extend(self.comm.sock.sent)
        self.comm.sock.sent.clear()

    def on_transfer(self, kind, payload):
        try:
            name, data = self.decoder.decode(kind, bytes(payload))
        except bnglink.MissingAsset as exc:
            self.replies.append(f'init vehicle {exc.vehicle_id} missing'.encode())
            return
        self.messages.append((name, data))
        if name == 'control' and data == 'ping':
            self.replies.append(b'pong')
        elif name == 'control' and data == 'session_reset':
            self.decoder.reset()
        elif name == 'control' and data.startswith('vehicle_removed '):
            self.decoder.remove_vehicle(int(data[len('vehicle_removed '):]))
        elif name == 'init':
            vehicle_id = data['vehicle_id']
            if vehicle_id in self.failed_inits:
                self.failed_inits.remove(vehicle_id)
                self.replies.append(f'init vehicle {vehicle_id} failed'.encode())
            else:
                self.replies.append(f'init vehicle {vehicle_id} ok'.encode())


class LuaSender:
    def __init__(self, lua_dir=LUA_DIR):
        self.lua = LuaRuntime(encoding='latin-1')
        self.lua.globals().hashStringSHA256 = lambda s: hashlib.sha256(s.encode('latin-1')).hexdigest()
        self.lua.globals().dofile(str(TESTS / 'engine_mock.lua'))
        self.harness = self.lua.globals().harness
        self.harness.setup(str(lua_dir).replace('\\', '/'))
        self.receiver = Receiver()
        self.datagrams = []
        self.frame = 0

    def ext(self, name):
        return self.lua.globals().extensions[name]

    def execute(self, code):
        self.lua.execute(code)

    def run(self, frames):
        for _ in range(frames):
            self.frame += 1
            self.harness.frame(FRAME_DT)
            for datagram in self.harness.takeSent().values():
                datagram = datagram.encode('latin-1')
                self.datagrams.append((self.frame, datagram))
                self.receiver.handle(datagram)
            for reply in self.receiver.replies:
                self.harness.push(reply.decode('latin-1'))
            self.receiver.replies.clear()

    def messages(self, name):
        return [data for message, data in self.receiver.messages if message == name]

    def errors(self):
        return [entry for entry in self.harness.logs.values() if entry.startswith('E ')]


def run_scenario(sender):
    # Every sender feature in a fixed frame sequence, so two sender versions can be compared byte for byte
    s = sender
    s.lua.globals().extensions.load('BNGLink_BNGLink')
    bng = s.ext('BNGLink_BNGLink')
    bng.initContent()
    s.run(60)
    bng.startRealtime()
    s.run(20)
    for code in ('harness.electrics.lowbeam = 1', 'harness.electrics.highbeam = 1',
                 'harness.electrics.signal_l_filament = 1; harness.electrics.signal_L = true',
                 'harness.electrics.signal_L = false', 'harness.electrics.lowbeam = 0; harness.electrics.highbeam = 0'):
        s.execute(code)
        s.run(10)
    s.execute('harness.vehicles[10].color = {x = 0.9, y = 0.1, z = 0.1, w = 1}')
    s.run(30)
    s.execute('harness.vehicles[11].paint = {metallic = 1, roughness = 0, clearcoat = 0, clearcoatRoughness = 0}')
    bng.onVehicleColorChanged(11)
    s.run(30)
    s.execute('harness.versions.cef = 2')
    s.run(30)
    s.execute('harness.vehicles[10].licenseText = "XYZ"')
    s.ext('BNGLink_DynamicTextures').onLicensePlateChanged(None, 10)
    s.run(30)
    s.execute('harness.versions.decal = 2')
    bng.dynamicDecals_onLayerUpdated()
    s.run(120)
    s.execute('harness.vehicles[10].skin = "blue"')
    s.run(60)
    s.execute('harness.vehicles[10].layoutChanged = true')
    s.run(150)
    bng.onVehicleReplaced(11)
    s.run(60)
    s.receiver.decoder = bnglink.BngDecoder()
    bng.onPartsTreeConfigChanged()
    s.run(60)
    bng.stopRealtime()
    s.run(5)
    s.lua.globals().extensions.unload('BNGLink_BNGLink')
