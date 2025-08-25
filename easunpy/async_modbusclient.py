"""
easunpy.async_modbusclient
--------------------------
Minimal TCP 'cloud-server' shim for Voltronic/PI18 ASCII over the FF 04 wrapper.

Adds: send_ascii_command() used by the Settings API.
Does not change read behaviour for sensors.

Logging mirrors the existing style seen in your logs.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from typing import Optional

_LOGGER = logging.getLogger("easunpy.async_modbusclient")


def _crc16_xmodem(data: bytes) -> int:
    """CRC-16/XMODEM as required by PI18 ASCII (poly=0x1021, init=0x0000)."""
    crc = 0x0000
    for b in data:
        crc ^= (b << 8) & 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _adjust_crc_byte(b: int) -> int:
    # Per vendor wrapper rule: avoid reserved 0x0A, 0x0D, 0x28 by +1 on those bytes.
    return b + 1 if b in (0x0A, 0x0D, 0x28) else b


def _build_ascii_payload(cmd: str) -> bytes:
    """
    Build the 'data' segment: ASCII + CRC (with reserved byte adjustment) + <CR>.
    Example: b'QPIGS' + crc_hi + crc_lo + 0x0D
    """
    raw = cmd.encode("ascii")
    crc = _crc16_xmodem(raw)
    crc_hi = _adjust_crc_byte((crc >> 8) & 0xFF)
    crc_lo = _adjust_crc_byte(crc & 0xFF)
    return raw + bytes([crc_hi, crc_lo, 0x0D])


def _wrap_cloud(trans_id: int, ascii_payload: bytes) -> bytes:
    """
    Wrap with Modbus/TCP-like header + FF 04 function (cloud tunnel):
        [TID (2)][PID=0x0001 (2)][LEN (2)][UID=0xFF (1)][FC=0x04 (1)] + data
    LEN counts bytes after LEN (i.e., unit+func+data).
    """
    length = len(ascii_payload) + 2  # + unit + function
    header = struct.pack(">HHHBB", trans_id & 0xFFFF, 0x0001, length, 0xFF, 0x04)
    return header + ascii_payload


def _parse_cloud_response(pdu: bytes) -> Optional[str]:
    """
    Parse response and extract raw ASCII between '(' and before CRC+<CR>.
    Returns a string that still starts with '(' (e.g. '(ACK', '(NAK', '(...').
    """
    if len(pdu) < 8:
        return None
    # Header: TID(2) PID(2) LEN(2) UID(1) FC(1)
    try:
        _, _, length, _, _ = struct.unpack(">HHHBB", pdu[:8])
    except struct.error:
        return None
    if len(pdu) < 6 + length:
        return None
    data = pdu[8 : 6 + length]
    if len(data) < 4:  # at least '(<CRC><CR)'
        return None
    # Strip CRC (2) + \r (1)
    body = data[:-3]
    try:
        return body.decode("ascii", errors="ignore")
    except Exception:
        return None


class AsyncModbusClient:
    """Listens on TCP and speaks the FF 04 tunnel with the inverter."""

    def __init__(self, local_ip: str, port: int = 502, connect_timeout: float = 10.0):
        self._local_ip = local_ip
        self._port = port
        self._connect_timeout = connect_timeout

        self._server: Optional[asyncio.AbstractServer] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

        self._trans_id = int(time.time()) & 0xFFFF
        self._lock = asyncio.Lock()
        self._client_ready = asyncio.Event()

    async def start(self) -> None:
        """Start listening (idempotent)."""
        if self._server is not None:
            return
        try:
            self._server = await asyncio.start_server(
                self._on_client, host=self._local_ip, port=self._port
            )
            _LOGGER.debug(
                "TCP server listening on %s:%s", self._local_ip, self._port
            )
        except OSError as exc:
            _LOGGER.error(
                "Failed to start TCP server: %s", exc, exc_info=False
            )
            raise

    async def stop(self) -> None:
        """Stop server and drop connection."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        self._client_ready.clear()
        _LOGGER.debug("Server cleaned up successfully")

    async def _ensure_client(self) -> None:
        """Wait for inverter client connection with timeout."""
        if self._reader and self._writer and not self._writer.is_closing():
            _LOGGER.debug("Reusing existing TCP connection")
            return
        if self._server is None:
            await self.start()
        try:
            await asyncio.wait_for(self._client_ready.wait(), timeout=self._connect_timeout)
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout waiting for client connection")
            await self.stop()
            raise

    async def _on_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Take first client and close any old writer
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = reader
        self._writer = writer
        self._client_ready.set()

    async def send_ascii_command(self, command: str, timeout: float = 5.0) -> str:
        """
        Send an ASCII command (e.g., 'POP00', 'PBCV54.0', 'QPIGS') and return the
        raw string reply (e.g., '(ACK', '(NAK', '(...)').

        This wraps cloud header and computes XMODEM CRC with reserved-byte fix.
        """
        async with self._lock:
            await self._ensure_client()

            # Build request
            self._trans_id = (self._trans_id + 1) & 0xFFFF
            payload = _build_ascii_payload(command)
            packet = _wrap_cloud(self._trans_id, payload)

            # Send
            _LOGGER.debug("Sending command: %s", packet.hex())
            try:
                assert self._writer
                self._writer.write(packet)
                await self._writer.drain()

                # Read header (6) first
                assert self._reader
                header = await asyncio.wait_for(self._reader.readexactly(6), timeout=timeout)
                # Determine LEN
                length = struct.unpack(">H", header[4:6])[0]
                rest = await asyncio.wait_for(self._reader.readexactly(length), timeout=timeout)
                response = header + rest
                _LOGGER.debug("Response: %s", response.hex())
            except asyncio.TimeoutError:
                _LOGGER.warning("No response for %s", command)
                raise
            except Exception as exc:
                _LOGGER.error("Transport error on %s: %s", command, exc, exc_info=False)
                await self.stop()
                raise

            parsed = _parse_cloud_response(response)
            return parsed or ""

