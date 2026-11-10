"""Userspace WireGuard handshake.

This is the published Noise_IKpsk2 construction. X25519 and ChaCha20-Poly1305
come from the cryptography library. BLAKE2s comes from hashlib. There is no
TUN device and no system route. A finished handshake is not application protection.
"""

from __future__ import annotations

import hashlib
import hmac
import socket
import struct
import threading

CONSTRUCTION = b"Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s"
IDENTIFIER = b"WireGuard v1 zx2c4 Jason@zx2c4.com"
_ZERO = b"\x00" * 12
_TIMEOUT = 5.0


class HandshakeError(Exception):
    pass


class Tunnel:
    def __init__(self, sock: socket.socket, send_key: bytes, remote_index: int):
        self._sock = sock
        self._send_key = send_key
        self._remote_index = remote_index
        self._counter = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._keepalive, name="fraghost-ghost-keepalive", daemon=True)
        self._thread.start()

    def send(self, payload: bytes) -> None:
        with self._lock:
            counter = self._counter
            self._counter += 1
            packet = _transport(self._send_key, self._remote_index, counter, payload)
        self._sock.send(packet)

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass

    def _keepalive(self) -> None:
        while not self._stop.wait(25):
            try:
                self.send(b"")
            except OSError:
                return


def connect_endpoint(endpoint: dict) -> Tunnel:
    from .ghost_identity import IdentityError, decode_key, private_key
    from .secrets import read_secret

    try:
        local = private_key()
        peer = decode_key(endpoint.get("public_key"))
    except IdentityError:
        raise
    except ValueError as exc:
        raise HandshakeError("The saved endpoint is missing a pinned public key.") from exc
    psk = read_secret(f"ghost_psk_{endpoint.get('id') or ''}")
    if len(psk) != 32:
        psk = b"\x00" * 32
    return connect(str(endpoint.get("host") or ""), int(endpoint.get("port") or 0), local, peer, psk)


def connect(host: str, port: int, local_private: bytes, remote_public: bytes, psk: bytes = b"\x00" * 32) -> Tunnel:
    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host or port < 1 or port > 65535:
        raise HandshakeError("The pinned endpoint did not answer a WireGuard handshake. Transfers stay paused.")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except OSError as exc:
        raise HandshakeError("The endpoint address could not be resolved. Transfers stay paused.") from exc
    if not infos:
        raise HandshakeError("The endpoint address could not be resolved. Transfers stay paused.")
    family, _, _, _, sockaddr = infos[0]
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(_TIMEOUT)
    try:
        sock.connect(sockaddr)
        noise = _Noise(remote_public)
        packet, ephemeral = noise.initiation(local_private)
        sock.send(packet)
        reply = _recv(sock)
        send_key, remote_index = noise.consume_response(reply, local_private, psk, ephemeral)
        tunnel = Tunnel(sock, send_key, remote_index)
        tunnel.send(b"")
        return tunnel
    except HandshakeError:
        sock.close()
        raise
    except OSError as exc:
        sock.close()
        raise HandshakeError("The pinned endpoint did not answer a WireGuard handshake. Transfers stay paused.") from exc


def serve_once(sock: socket.socket, local_private: bytes, expected_peer: bytes, psk: bytes = b"\x00" * 32) -> bytes:
    """Test helper. One handshake and one transport packet, then the payload."""
    from .ghost_identity import _public_bytes

    sock.settimeout(_TIMEOUT)
    packet, address = sock.recvfrom(4096)
    noise = _Noise(_public_bytes(local_private))
    peer, remote_index, ephemeral = noise.consume_initiation(packet, local_private)
    if peer != expected_peer:
        raise HandshakeError("The pinned endpoint did not accept the handshake.")
    reply, local_index, responder_ephemeral = noise.response(local_private, peer, psk, ephemeral, remote_index)
    sock.sendto(reply, address)
    transport, _ = sock.recvfrom(4096)
    recv_key = noise.transport_keys(initiator=False)[1]
    return _open_transport(recv_key, local_index, transport)


class _Noise:
    def __init__(self, responder_public: bytes):
        self.chain = _hash(CONSTRUCTION)
        self.hash = _hash(self.chain + IDENTIFIER)
        self.mix_hash(responder_public)
        self.responder_public = responder_public

    def mix_hash(self, data: bytes) -> None:
        self.hash = _hash(self.hash + data)

    def mix_key(self, data: bytes) -> None:
        self.chain = _hmac(self.chain, data)

    def split_key(self, data: bytes) -> bytes:
        chain, key = _kdf2(self.chain, data)
        self.chain = chain
        return key

    def initiation(self, local_private: bytes) -> tuple[bytes, object]:
        ephemeral = _ephemeral()
        ephemeral_public = _public_of(ephemeral)
        self.local_index = _index()
        self.mix_hash(ephemeral_public)
        self.mix_key(ephemeral_public)
        key = self.split_key(_exchange(ephemeral, self.responder_public))
        static_box = _seal(key, _public_of(_private(local_private)), self.hash)
        self.mix_hash(static_box)
        key = self.split_key(_exchange(_private(local_private), self.responder_public))
        timestamp = _seal(key, _tai64n(), self.hash)
        self.mix_hash(timestamp)
        body = struct.pack("<II", 1, self.local_index) + ephemeral_public + static_box + timestamp
        return _macs(self.responder_public, body), ephemeral

    def consume_initiation(self, packet: bytes, local_private: bytes) -> tuple[bytes, int, bytes]:
        _check_macs(self.responder_public, packet)
        if len(packet) != 148 or struct.unpack_from("<I", packet)[0] != 1:
            raise HandshakeError("The pinned endpoint did not accept the handshake.")
        sender = struct.unpack_from("<I", packet, 4)[0]
        ephemeral_public = packet[8:40]
        static_box = packet[40:88]
        timestamp = packet[88:116]
        self.mix_hash(ephemeral_public)
        self.mix_key(ephemeral_public)
        key = self.split_key(_exchange(_private(local_private), ephemeral_public))
        try:
            peer = _open(key, static_box, self.hash)
        except HandshakeError:
            raise HandshakeError("The pinned endpoint did not accept the handshake.") from None
        self.mix_hash(static_box)
        key = self.split_key(_exchange(_private(local_private), peer))
        try:
            _open(key, timestamp, self.hash)
        except HandshakeError:
            raise HandshakeError("The pinned endpoint did not accept the handshake.") from None
        self.mix_hash(timestamp)
        self.peer_public = peer
        self.peer_ephemeral = ephemeral_public
        return peer, sender, ephemeral_public

    def response(self, local_private: bytes, peer_public: bytes, psk: bytes, peer_ephemeral: bytes, remote_index: int):
        ephemeral = _ephemeral()
        ephemeral_public = _public_of(ephemeral)
        self.local_index = _index()
        self.mix_hash(ephemeral_public)
        self.mix_key(ephemeral_public)
        self.mix_key(_exchange(ephemeral, peer_ephemeral))
        self.mix_key(_exchange(ephemeral, peer_public))
        self._mix_psk(psk)
        empty = _seal(self.message_key, b"", self.hash)
        self.mix_hash(empty)
        body = struct.pack("<III", 2, self.local_index, remote_index) + ephemeral_public + empty
        return _macs(peer_public, body), self.local_index, ephemeral

    def consume_response(self, packet: bytes, local_private: bytes, psk: bytes, ephemeral) -> tuple[bytes, int]:
        from .ghost_identity import _public_bytes

        _check_macs(_public_bytes(local_private), packet)
        if len(packet) != 92 or struct.unpack_from("<I", packet)[0] != 2:
            raise HandshakeError("The pinned endpoint did not answer a WireGuard handshake. Transfers stay paused.")
        sender, receiver = struct.unpack_from("<II", packet, 4)
        if receiver != self.local_index:
            raise HandshakeError("The pinned endpoint did not answer a WireGuard handshake. Transfers stay paused.")
        ephemeral_public = packet[12:44]
        empty = packet[44:60]
        self.mix_hash(ephemeral_public)
        self.mix_key(ephemeral_public)
        self.mix_key(_exchange(ephemeral, ephemeral_public))
        self.mix_key(_exchange(_private(local_private), ephemeral_public))
        self._mix_psk(psk)
        try:
            _open(self.message_key, empty, self.hash)
        except HandshakeError:
            raise HandshakeError("The pinned endpoint did not accept the handshake. Transfers stay paused.") from None
        self.mix_hash(empty)
        send_key, _recv = self.transport_keys(initiator=True)
        return send_key, sender

    def _mix_psk(self, psk: bytes) -> None:
        if len(psk) != 32:
            psk = b"\x00" * 32
        chain, tau, key = _kdf3(self.chain, psk)
        self.chain = chain
        self.mix_hash(tau)
        self.message_key = key

    def transport_keys(self, *, initiator: bool) -> tuple[bytes, bytes]:
        send, recv = _kdf2(self.chain, b"")
        if initiator:
            return send, recv
        return recv, send


def _transport(key: bytes, receiver: int, counter: int, payload: bytes) -> bytes:
    nonce = b"\x00\x00\x00\x00" + counter.to_bytes(8, "little")
    return struct.pack("<IIQ", 4, receiver, counter) + _cipher(key).encrypt(nonce, payload, b"")


def _open_transport(key: bytes, local_index: int, packet: bytes) -> bytes:
    if len(packet) < 32 or struct.unpack_from("<I", packet)[0] != 4:
        raise HandshakeError("The pinned endpoint did not accept the handshake.")
    receiver, counter = struct.unpack_from("<IQ", packet, 4)
    if receiver != local_index:
        raise HandshakeError("The pinned endpoint did not accept the handshake.")
    nonce = b"\x00\x00\x00\x00" + counter.to_bytes(8, "little")
    try:
        return _cipher(key).decrypt(nonce, packet[16:], b"")
    except Exception as exc:
        raise HandshakeError("The pinned endpoint did not accept the handshake.") from exc


def _recv(sock: socket.socket) -> bytes:
    packet = sock.recv(4096)
    if len(packet) >= 4 and struct.unpack_from("<I", packet)[0] == 3:
        raise HandshakeError("The endpoint asked for a cookie. Ghost Mode will not guess one.")
    return packet


def _macs(public: bytes, body: bytes) -> bytes:
    mac1 = hashlib.blake2s(body, key=_hash(b"mac1----" + public), digest_size=16).digest()
    return body + mac1 + (b"\x00" * 16)


def _check_macs(public: bytes, packet: bytes) -> None:
    if len(packet) < 32:
        raise HandshakeError("The pinned endpoint did not accept the handshake.")
    body, mac1, mac2 = packet[:-32], packet[-32:-16], packet[-16:]
    expect = hashlib.blake2s(body, key=_hash(b"mac1----" + public), digest_size=16).digest()
    if not hmac.compare_digest(expect, mac1):
        raise HandshakeError("The pinned endpoint did not accept the handshake.")
    if mac2 != b"\x00" * 16:
        raise HandshakeError("The endpoint asked for a cookie. Ghost Mode will not guess one.")


def _hash(data: bytes) -> bytes:
    return hashlib.blake2s(data, digest_size=32).digest()


def _hmac(key: bytes, *parts: bytes) -> bytes:
    mac = hmac.new(key, digestmod=lambda: hashlib.blake2s(digest_size=32))
    for part in parts:
        mac.update(part)
    return mac.digest()


def _kdf2(key: bytes, data: bytes) -> tuple[bytes, bytes]:
    prk = _hmac(key, data)
    first = _hmac(prk, b"\x01")
    return first, _hmac(prk, first, b"\x02")


def _kdf3(key: bytes, data: bytes) -> tuple[bytes, bytes, bytes]:
    prk = _hmac(key, data)
    first = _hmac(prk, b"\x01")
    second = _hmac(prk, first, b"\x02")
    return first, second, _hmac(prk, second, b"\x03")


def _private(raw: bytes):
    private_cls, _, _serialization = _crypto()
    return private_cls.from_private_bytes(raw)


def _ephemeral():
    private_cls, _, _serialization = _crypto()
    return private_cls.generate()


def _public_of(private) -> bytes:
    _private_cls, _, serialization = _crypto()
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _exchange(private, peer: bytes) -> bytes:
    _private_cls, public_cls, _serialization = _crypto()
    return private.exchange(public_cls.from_public_bytes(peer))


def _seal(key: bytes, plaintext: bytes, ad: bytes) -> bytes:
    try:
        return _cipher(key).encrypt(_ZERO, plaintext, ad)
    except HandshakeError:
        raise
    except Exception as exc:
        raise HandshakeError("The pinned endpoint did not accept the handshake.") from exc


def _open(key: bytes, ciphertext: bytes, ad: bytes) -> bytes:
    try:
        return _cipher(key).decrypt(_ZERO, ciphertext, ad)
    except HandshakeError:
        raise
    except Exception as exc:
        raise HandshakeError("The pinned endpoint did not accept the handshake.") from exc


def _cipher(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    except ImportError as exc:
        raise HandshakeError("WireGuard support is not installed. Transfers stay paused.") from exc
    return ChaCha20Poly1305(key)


def _tai64n() -> bytes:
    import time

    now = time.time()
    seconds = int(now)
    nanos = int((now - seconds) * 1_000_000_000) % 1_000_000_000
    return (seconds + 0x400000000000000A).to_bytes(8, "big") + nanos.to_bytes(4, "big")


def _index() -> int:
    import os

    return int.from_bytes(os.urandom(4), "little") or 1


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    except ImportError as exc:
        raise HandshakeError("WireGuard support is not installed. Transfers stay paused.") from exc
    return X25519PrivateKey, X25519PublicKey, serialization
