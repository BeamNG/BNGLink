# Session shared by every platform endpoint: socket, decoder, vehicle registry and message
# dispatch. A platform only provides its handlers and drives poll() from its own timer.

import time
import traceback

from .assets import MissingAsset
from .decoder import BngDecoder
from .transport import CONTROL_MESSAGES, KIND_STREAM, UdpComm


class LinkSession:
    # handlers: {message name returned by BngDecoder.decode(): handler(session, data)}.
    # Control messages are logged here. stats is an optional RateStats.

    def __init__(self, host, port, beamng_addr, handlers, log, stats=None, on_payload=None):
        self.comm = UdpComm(host, port, peer_addr=beamng_addr)
        self.decoder = BngDecoder()
        self.beamng_addr = beamng_addr
        self.handlers = handlers
        self.log = log
        self.stats = stats
        self.on_payload = on_payload
        self.vehicle_states = {}
        self.latest_stream = None
        # Set by the first transfer from BeamNG. UDP has no disconnect, so it stays set until close
        self.connected = False

    def close(self):
        self.reset()
        self.comm.close()

    def reset(self):
        self.apply('session_reset', None)
        self.vehicle_states.clear()
        self.decoder.reset()
        self.latest_stream = None

    def remove_vehicle(self, vehicle_id):
        self.apply('vehicle_removed', {'vehicle_id': vehicle_id})
        self.vehicle_states.pop(vehicle_id, None)
        self.decoder.remove_vehicle(vehicle_id)

    def confirm_ready(self, vehicle_id, status='ok'):
        # BeamNG sends the next vehicle after 'ok'
        # 'missing' makes it resend this one with every asset
        self.comm.send(*self.beamng_addr, f'init vehicle {vehicle_id} {status}'.encode())

    def poll(self):
        # Returns True when a sync frame was applied
        self.comm.poll(self.on_transfer)
        applied = self.apply_latest_stream()
        if self.stats is not None:
            self.stats.tick()
        return applied

    def on_transfer(self, kind, payload):
        self.connected = True
        if self.on_payload is not None:
            self.on_payload(kind, payload)

        # Streams are latest-wins: keep the raw payload so a backlog costs a single decode
        if kind == KIND_STREAM:
            self.latest_stream = payload
            if self.stats is not None:
                self.stats.received += 1
            return

        name, data = self.decode(kind, payload, 'transfer')
        if name == 'control':
            if data == 'ping':
                self.comm.send(*self.beamng_addr, b'pong')
                return
            if data == 'session_reset':
                self.reset()
            elif data.startswith('vehicle_removed '):
                vehicle_id = data[len('vehicle_removed '):]
                if vehicle_id.isdigit():
                    self.remove_vehicle(int(vehicle_id))
                    self.log(f'Vehicle {vehicle_id} removed')
                    return
            self.log(CONTROL_MESSAGES.get(data, data))
            return
        if name == 'init':
            if not self.apply(name, data):
                vehicle_id = data['vehicle_id']
                self.remove_vehicle(vehicle_id)
                self.confirm_ready(vehicle_id, 'failed')
            return
        self.apply(name, data)

    # The endpoints drive poll() from a host timer
    # The host unregisters the timer when the callback raises, which would take the whole link down.
    # A failing handler stops here.
    def apply(self, name, data):
        handler = self.handlers.get(name)
        if handler is None:
            return False
        try:
            handler(self, data)
            return True
        except Exception as exc:
            self.log(f'Could not apply {name}: {exc}')
            traceback.print_exc()
            return False

    def apply_latest_stream(self):
        payload, self.latest_stream = self.latest_stream, None
        if payload is None:
            return False

        name, data = self.decode(KIND_STREAM, payload, 'stream')
        start = time.perf_counter()
        if not self.apply(name, data):
            return False
        if self.stats is not None:
            self.stats.note_applied(time.perf_counter() - start)
        return True

    def decode(self, kind, payload, context):
        try:
            return self.decoder.decode(kind, payload)
        except MissingAsset as exc:
            self.log(f'Missing cached BNGLink asset {exc.key}, requesting full init')
            self.confirm_ready(exc.vehicle_id, 'missing')
            return None, None
        except ValueError as exc:
            self.log(f'Could not read {context} data: {exc}')
            traceback.print_exc()
            return None, None
