# Bytes on the wire: packet header, message kinds and the reassembling receiver.

import socket
import struct
import time

HEADER = struct.Struct('<4sBIII')
HEADER_MAGIC = b'BNG1'
ACK_MAGIC = b'BNGA'
MAX_CHUNK_SIZE = 42000
MAX_TRANSFER_SIZE = 256 * 1024 * 1024
MAX_CHUNKS = (MAX_TRANSFER_SIZE + MAX_CHUNK_SIZE - 1) // MAX_CHUNK_SIZE
MAX_PARTIAL_TRANSFERS = 8
PARTIAL_TRANSFER_TIMEOUT = 5.0
MAX_DATAGRAMS_PER_POLL = 512
MAX_BYTES_PER_POLL = 8 * 1024 * 1024

KIND_STREAM = 0
KIND_BULK = 1
KIND_CONTROL = 2
KIND_UPDATE = 3
KINDS = {KIND_STREAM, KIND_BULK, KIND_CONTROL, KIND_UPDATE}

CONTROL_MESSAGES = {
    'session_reset': 'Resetting BNGLink scene...',
    'init_start': 'Preparing BeamNG scene, please wait...',
    'init_completed': 'BeamNG scene ready',
    'vehicle_change_detected': 'Vehicle changed, updating BNGLink scene...',
    'realtime_started': 'Live sync started',
    'realtime_stopped': 'Live sync stopped',
}


class UdpComm:
    # UDP receiver with chunked-transfer reassembly.
    # Each completed transfer is delivered to on_transfer(kind, payload).
    # Single-datagram transfers skip reassembly entirely.

    def __init__(self, host, port, recv_buf=64 * 1024 * 1024, peer_addr=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((host, port))
        except OSError as exc:
            self.sock.close()
            raise OSError(
                exc.errno,
                f'Could not bind BNGLink receiver to {host}:{port}: {exc.strerror}. '
                'Is another BNGLink endpoint already running?'
            ) from exc
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, recv_buf)
        self.sock.setblocking(False)

        self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.peer_addr = peer_addr

        self.chunk_states = {}
        self.buffered_bytes = 0
        self.last_completed_transfer_ids = {}
        self.highest_transfer_ids = {}

    def close(self):
        self.sock.close()
        self.send_sock.close()

    def send(self, host, port, data):
        self.send_sock.sendto(data, (host, port))

    def poll(self, on_transfer):
        self._expire_stale(time.monotonic())
        datagrams = 0
        received_bytes = 0
        while datagrams < MAX_DATAGRAMS_PER_POLL and received_bytes < MAX_BYTES_PER_POLL:
            try:
                data, addr = self.sock.recvfrom(65535)
                self.handle(data, addr, on_transfer)
                datagrams += 1
                received_bytes += len(data)
            except BlockingIOError:
                break

    def handle(self, data, addr, on_transfer):
        if self.peer_addr is not None and addr != self.peer_addr:
            return
        if len(data) < HEADER.size:
            return

        magic, kind, transfer_id, chunk, count = HEADER.unpack_from(data)
        payload_size = len(data) - HEADER.size
        if (
            magic != HEADER_MAGIC
            or kind not in KINDS
            or count == 0
            or count > MAX_CHUNKS
            or chunk >= count
            or payload_size > MAX_CHUNK_SIZE
        ):
            return

        # Transfer id 1 after higher ids means the sender restarted: forget its old transfers
        highest = self.highest_transfer_ids.get(addr, 0)
        if transfer_id == 1 and highest > 1:
            for channel in [key for key in self.chunk_states if key[0] == addr]:
                self._drop_state(channel)
            self.last_completed_transfer_ids = {
                key: value for key, value in self.last_completed_transfer_ids.items() if key[0] != addr
            }
            highest = 0
        self.highest_transfer_ids[addr] = max(highest, transfer_id)

        channel = (addr, kind)
        state = self.chunk_states.get(channel)
        in_progress = state is not None and state['transfer_id'] == transfer_id and state['count'] == count
        last_completed = self.last_completed_transfer_ids.get(channel, 0)
        if not in_progress and (
            transfer_id <= last_completed
            or state is not None and transfer_id < state['transfer_id']
        ):
            self._ack(kind, transfer_id, chunk, addr)
            return

        if count == 1:
            self._drop_state(channel)
            self.last_completed_transfer_ids[channel] = transfer_id
            self._ack(kind, transfer_id, chunk, addr)
            if kind == KIND_STREAM:
                # Stream payloads are only read through struct/numpy, so pass a zero-copy view of the datagram.
                on_transfer(kind, memoryview(data)[HEADER.size:])
            else:
                on_transfer(kind, data[HEADER.size:])
            return

        if not in_progress:
            self._drop_state(channel)
            if len(self.chunk_states) >= MAX_PARTIAL_TRANSFERS:
                return
            state = {
                'transfer_id': transfer_id,
                'count': count,
                'chunks': {},
                'size': 0,
                'updated_at': time.monotonic(),
            }
            self.chunk_states[channel] = state
        payload = data[HEADER.size:]
        previous = state['chunks'].get(chunk)
        new_size = state['size'] - (len(previous) if previous is not None else 0) + len(payload)
        if self.buffered_bytes - state['size'] + new_size > MAX_TRANSFER_SIZE:
            self._drop_state(channel)
            return
        state['chunks'][chunk] = payload
        self.buffered_bytes += new_size - state['size']
        state['size'] = new_size
        state['updated_at'] = time.monotonic()
        self._ack(kind, transfer_id, chunk, addr)
        if len(state['chunks']) == count:
            full = b''.join(state['chunks'][i] for i in range(count))
            self._drop_state(channel)
            self.last_completed_transfer_ids[channel] = max(last_completed, transfer_id)
            on_transfer(kind, full)

    def _ack(self, kind, transfer_id, chunk, addr):
        if kind == KIND_BULK:
            self.sock.sendto(ACK_MAGIC + struct.pack('<II', transfer_id, chunk), addr)

    def _drop_state(self, channel):
        state = self.chunk_states.pop(channel, None)
        if state is not None:
            self.buffered_bytes -= state['size']

    def _expire_stale(self, now):
        for channel, state in list(self.chunk_states.items()):
            if now - state['updated_at'] >= PARTIAL_TRANSFER_TIMEOUT:
                self._drop_state(channel)
