# easunpy/async_modbusclient.py

"""
easunpy.async_modbusclient
--------------------------
TCP "cloud-server" shim for Voltronic/PI18 ASCII via the FF 04 wrapper.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from typing import List, Optional, Tuple, Union

# Optional spacing between commands to accommodate slower Wi-Fi / device processing
INTER_COMMAND_DELAY: float = 0.300  # seconds

_LOGGER = logging.getLogger("easunpy.async_modbusclient")


def _to_bytes(req: Union[bytes, bytearray, memoryview, str, List[int], Tuple[int, ...]]) -> bytes:
    """Coerce various request types into raw bytes."""
    if isinstance(req, (bytes, bytearray, memoryview)):
        return bytes(req)
    if isinstance(req, str):
        s = req.strip()
        # Heuristic: hex string (even length, hex chars only)
        if len(s) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in s):
            try:
                return bytes.fromhex(s)
            except ValueError:
                # Fall back to raw encoding
                pass
        try:
            return s.encode("ascii")
        except Exception:
            return s.encode("latin-1", "ignore")
    # Maybe an iterable of ints (0..255)
    try:
        return bytes(req)  # type: ignore[arg-type]
    except Exception as exc:
        raise TypeError(f"Unsupported request type for transport: {type(req)!r}") from exc


class AsyncModbusClient:
    """Listens on TCP and speaks the FF 04 tunnel with the inverter (server mode)."""

    def __init__(
        self,
        inverter_ip: str,
        local_ip: str,
        port: int = 502,
        connect_timeout: float = 60.0,
    ):
        self._inverter_ip = inverter_ip
        self._local_ip = local_ip
        self._port = port
        self._connect_timeout = connect_timeout

        self._server: Optional[asyncio.AbstractServer] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

        self._client_ready = asyncio.Event()
        self._lock = asyncio.Lock()
        self._trans_id = int(time.time()) & 0xFFFF

        self._udp_task: Optional[asyncio.Task] = None
        self._udp_interval = 5.0

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.start())
        except RuntimeError:
            pass

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = reader
        self._writer = writer
        peer = writer.get_extra_info("peername")
        if peer:
            _LOGGER.info("Inverter connected from %s:%s", peer[0], peer[1])
        else:
            _LOGGER.info("Inverter connected")
        self._client_ready.set()

    async def _try_start_at(self, host: Optional[str]) -> Optional[asyncio.AbstractServer]:
        try:
            server = await asyncio.start_server(self._on_client, host=host, port=self._port)
            bound = None
            if server.sockets:
                sock = server.sockets[0].getsockname()
                bound = f"{sock[0]}:{sock[1]}"
            _LOGGER.debug("TCP server listening on %s", bound or f"{host}:{self._port}")
            return server
        except OSError as exc:
            _LOGGER.warning("Failed binding listener on %s:%s -> %s", host, self._port, exc, exc_info=False)
            return None

    async def start(self) -> None:
        if self._server is not None:
            return

        server = None
        if self._local_ip and self._local_ip != "0.0.0.0":
            server = await self._try_start_at(self._local_ip)

        if server is None:
            server = await self._try_start_at("0.0.0.0")

        if server is None:
            server = await self._try_start_at(None)

        if server is None:
            raise OSError(f"Could not start TCP listener on {self._local_ip}:{self._port} (and fallbacks)")

        self._server = server
        self._ensure_udp_task()

    def _ensure_udp_task(self) -> None:
        if not self._inverter_ip:
            _LOGGER.debug("UDP discovery disabled (no inverter_ip configured)")
            return
        if self._udp_task is None or self._udp_task.done():
            loop = asyncio.get_running_loop()
            self._udp_task = loop.create_task(self._udp_discovery_loop())

    async def ensure_listening(self) -> None:
        if self._server is None:
            await self.start()
        self._ensure_udp_task()

    def is_connected(self) -> bool:
        return bool(self._reader and self._writer and not self._writer.is_closing())

    async def stop(self) -> None:
        if self._udp_task is not None:
            self._udp_task.cancel()
            try:
                await self._udp_task
            except Exception:
                pass
            self._udp_task = None

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

    def _discovery_payload(self) -> bytes:
        return f"set>server={self._local_ip}:{self._port};".encode("ascii")

    async def _udp_discovery_loop(self) -> None:
        payload = self._discovery_payload()
        target = (self._inverter_ip, 58899)

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            try:
                if self._local_ip and self._local_ip != "0.0.0.0":
                    sock.bind((self._local_ip, 0))
            except OSError:
                pass
            sock.settimeout(0.2)
        except Exception as exc:
            _LOGGER.warning("UDP discovery disabled (socket error): %s", exc, exc_info=False)
            return

        _LOGGER.debug("UDP discovery loop started (interval=%ss, target=%s:%s)", self._udp_interval, target[0], target[1])

        try:
            while True:
                if not self.is_connected():
                    try:
                        sock.sendto(payload, target)
                        _LOGGER.debug("UDP discovery sent (inverter not connected) to %s:%s payload=%r", target[0], target[1], payload)
                    except Exception as exc:
                        _LOGGER.debug("UDP send error to %s:%s -> %s", target[0], target[1], exc, exc_info=False)
                else:
                    _LOGGER.debug("Skipping UDP discovery (inverter is connected)")

                await asyncio.sleep(self._udp_interval)
        except asyncio.CancelledError:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
            _LOGGER.debug("UDP discovery loop stopped")

    async def send_bulk(self, requests: List[Union[bytes, str]], timeout: float = 5.0) -> List[Optional[bytes]]:
        if not requests:
            return []
        async with self._lock:
            await self.ensure_listening()
            if not self.is_connected():
                _LOGGER.debug("No inverter connection yet; skipping this cycle")
                return [None for _ in requests]

            results: List[Optional[bytes]] = []
            assert self._reader and self._writer
            for req in requests:
                try:
                    req_bytes = _to_bytes(req)
                    _LOGGER.debug("Sending command: %s", req_bytes.hex())
                    self._writer.write(req_bytes)
                    await self._writer.drain()

                    header = await asyncio.wait_for(self._reader.readexactly(6), timeout=timeout)
                    length = struct.unpack(">H", header[4:6])[0]
                    rest = await asyncio.wait_for(self._reader.readexactly(length), timeout=timeout)
                    resp = header + rest
                    _LOGGER.debug("Response: %s", resp.hex())
                    results.append(resp)
                except asyncio.TimeoutError:
                    _LOGGER.warning("No response for a command (read timeout)")
                    results.append(None)
                except Exception as exc:
                    _LOGGER.error("Transport error: %s", exc, exc_info=False)
                    results.append(None)
                
                # ** THE FIX IS HERE **
                # Add a small delay to give the inverter time to process
                await asyncio.sleep(0.3)
            return results

    async def send_ascii_command(self, ascii_command_packet: Union[bytes, str], timeout: float = 5.0) -> Optional[bytes]:
        async with self._lock:
            await self.ensure_listening()
            if not self.is_connected():
                _LOGGER.debug("No inverter connection yet; skipping settings command")
                return None
            try:
                req_bytes = _to_bytes(ascii_command_packet)
                assert self._reader and self._writer
                _LOGGER.debug("Sending command: %s", req_bytes.hex())
                self._writer.write(req_bytes)
                await self._writer.drain()
                header = await asyncio.wait_for(self._reader.readexactly(6), timeout=timeout)
                length = struct.unpack(">H", header[4:6])[0]
                rest = await asyncio.wait_for(self._reader.readexactly(length), timeout=timeout)
                resp = header + rest
                _LOGGER.debug("Response: %s", resp.hex())
                return resp
            except asyncio.TimeoutError:
                _LOGGER.warning("No response for settings command (read timeout)")
                return None
            except Exception as exc:
                _LOGGER.error("Transport error on settings command: %s", exc, exc_info=False)
                return None