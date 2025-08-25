"""
easunpy.async_modbusclient
--------------------------
TCP "cloud-server" shim for Voltronic/PI18 ASCII via the FF 04 wrapper.

This version keeps the listener PERSISTENT:
- We no longer stop() the server on connection timeouts during updates.
- The server stays up so the inverter can connect whenever it's ready.
- Default connect timeout increased to 60s.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from typing import List, Optional

_LOGGER = logging.getLogger("easunpy.async_modbusclient")


class AsyncModbusClient:
    """Listens on TCP and speaks the FF 04 tunnel with the inverter (server mode)."""

    def __init__(self, inverter_ip: str, local_ip: str, port: int = 502, connect_timeout: float = 60.0):
        # inverter_ip is not used in server mode but kept for compatibility/diagnostics
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

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Accept newest client; close old writer if needed
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

    async def start(self) -> None:
        """Start listening (idempotent)."""
        if self._server is not None:
            return
        try:
            self._server = await asyncio.start_server(self._on_client, host=self._local_ip, port=self._port)
            _LOGGER.debug("TCP server listening on %s:%s", self._local_ip, self._port)
        except OSError as exc:
            _LOGGER.error("Failed to start TCP server: %s", exc, exc_info=False)
            raise

    async def _ensure_client(self) -> None:
        """
        Ensure the listener is up and (if we don't already have a client) wait a bit
        for the inverter to connect. We DO NOT stop() the server on timeout anymore.
        """
        if self._server is None:
            await self.start()

        if self._reader and self._writer and not self._writer.is_closing():
            _LOGGER.debug("Reusing existing TCP connection")
            return

        try:
            await asyncio.wait_for(self._client_ready.wait(), timeout=self._connect_timeout)
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout waiting for client connection")
            # Important: do NOT stop the server here; keep listening persistently.
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

    # Backwards-compat API used on unload in some versions
    async def _cleanup_server(self) -> None:
        await self.stop()

    def _next_tid(self) -> int:
        self._trans_id = (self._trans_id + 1) & 0xFFFF
        return self._trans_id

    async def send_bulk(self, requests: List[bytes], timeout: float = 5.0) -> List[Optional[bytes]]:
        """
        Send multiple FF 04 requests and collect replies.
        Each request already includes header+payload (built by modbusclient helpers).
        """
        if not requests:
            return []
        async with self._lock:
            await self._ensure_client()
            results: List[Optional[bytes]] = []
            assert self._reader and self._writer
            for req in requests:
                try:
                    _LOGGER.debug("Sending command: %s", req.hex())
                    self._writer.write(req)
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
                    # Do NOT stop the server here; keep it up for the next cycle.
                    results.append(None)
            return results

    async def send_ascii_command(self, ascii_command_packet: bytes, timeout: float = 5.0) -> Optional[bytes]:
        """
        Send a *single* prebuilt ASCII packet (full FF 04 wrapper already built)
        and return the full raw response bytes (header+payload), or None on error.
        Used by the settings API for one-off commands.
        """
        async with self._lock:
            await self._ensure_client()
            try:
                assert self._reader and self._writer
                _LOGGER.debug("Sending command: %s", ascii_command_packet.hex())
                self._writer.write(ascii_command_packet)
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
                # Keep server up for the next attempt.
                return None
