"""
easunpy.async_modbusclient
--------------------------
TCP "cloud-server" shim for Voltronic/PI18 ASCII via the FF 04 wrapper.

This version:
- Starts the listener EARLY and keeps it persistent.
- Polls are NON-BLOCKING when no client is connected (we skip that cycle).
- Adds an AUTO UDP DISCOVERY loop that periodically sends well-known
  discovery "pokes" to make the Wi-Fi dongle connect back to our TCP
  server (port 502 by default).

Common dongles use a UDP discovery on port 48899 with payload:
  "WIFIKIT-214028-READ"
We send that (and a couple of no-op hello payloads) to both:
  - the configured inverter_ip (unicast), and
  - 255.255.255.255 (broadcast)
until a TCP client connects, then we pause the loop.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from typing import List, Optional, Tuple

_LOGGER = logging.getLogger("easunpy.async_modbusclient")


class AsyncModbusClient:
    """Listens on TCP and speaks the FF 04 tunnel with the inverter (server mode)."""

    def __init__(
        self,
        inverter_ip: str,
        local_ip: str,
        port: int = 502,
        connect_timeout: float = 60.0,  # retained for compatibility; we don't block polls on it
    ):
        # inverter_ip is not used by TCP server mode directly, but we use it as a UDP target hint
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

        # UDP discovery management
        self._udp_task: Optional[asyncio.Task] = None
        self._udp_interval = 5.0  # seconds between discovery bursts
        self._udp_enabled = True  # auto enabled; stops itself when connected

        # Start listener early (best effort). If no loop yet, we start on first use.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.start())
        except RuntimeError:
            # No running loop at construction time; it's fine—ensure_listening() will start it.
            pass

    # ---------------- Core server lifecycle ----------------

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

    async def _try_start_at(self, host: Optional[str]) -> Optional[asyncio.AbstractServer]:
        """Try to start the server bound to a given host; return server or None."""
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
        """Start listening (idempotent)."""
        if self._server is not None:
            return

        # 1) Try binding exactly to the configured local_ip (if provided)
        server = None
        if self._local_ip and self._local_ip != "0.0.0.0":
            server = await self._try_start_at(self._local_ip)

        # 2) Fallback to 0.0.0.0 (all interfaces) if needed
        if server is None:
            server = await self._try_start_at("0.0.0.0")

        # 3) As a last resort, let asyncio choose (None)
        if server is None:
            server = await self._try_start_at(None)

        if server is None:
            # Give up meaningfully
            raise OSError(f"Could not start TCP listener on {self._local_ip}:{self._port} (and fallbacks)")

        self._server = server

        # Kick off UDP discovery loop now that we're listening
        self._ensure_udp_task()

    def _ensure_udp_task(self) -> None:
        if not self._udp_enabled:
            return
        if self._udp_task is None or self._udp_task.done():
            loop = asyncio.get_running_loop()
            self._udp_task = loop.create_task(self._udp_discovery_loop())

    async def ensure_listening(self) -> None:
        """Ensure the listener is up; do not wait for a client connection here."""
        if self._server is None:
            await self.start()
        # Also ensure UDP discovery is running (if enabled)
        self._ensure_udp_task()

    def is_connected(self) -> bool:
        """Return True if we currently have an active client connection."""
        return bool(self._reader and self._writer and not self._writer.is_closing())

    async def stop(self) -> None:
        """Stop server and drop connection."""
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

    # Backwards-compat API used on unload in some versions
    async def _cleanup_server(self) -> None:
        await self.stop()

    def _next_tid(self) -> int:
        self._trans_id = (self._trans_id + 1) & 0xFFFF
        return self._trans_id

    # ---------------- UDP discovery ----------------

    async def _udp_discovery_loop(self) -> None:
        """
        Periodically send discovery "pokes" to nudge the Wi-Fi dongle to connect
        back to our TCP listener. Stops when connected; resumes if disconnected.
        """
        # Known-good probes (payload, port). We keep it minimal/noisy-safe.
        probes: List[Tuple[bytes, int]] = [
            (b"WIFIKIT-214028-READ", 48899),  # widely used by many Wi-Fi serial modules
        ]

        # Targets: unicast to configured inverter_ip (if set), and broadcast
        targets: List[Tuple[str, int, bytes]] = []
        for payload, port in probes:
            if self._inverter_ip:
                targets.append((self._inverter_ip, port, payload))
            targets.append(("255.255.255.255", port, payload))

        # Bind a UDP socket for broadcast
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(0.2)
        except Exception as exc:
            _LOGGER.warning("UDP discovery disabled (socket error): %s", exc, exc_info=False)
            return

        _LOGGER.debug("UDP discovery loop started (interval=%ss)", self._udp_interval)

        try:
            while True:
                # If connected, sleep longer and keep looping (so we can auto-recover)
                if self.is_connected():
                    await asyncio.sleep(self._udp_interval)
                    continue

                # Fire a short burst to all targets
                for host, port, payload in targets:
                    try:
                        sock.sendto(payload, (host, port))
                        _LOGGER.debug("UDP discovery sent to %s:%s payload=%r", host, port, payload)
                    except Exception as exc:
                        _LOGGER.debug("UDP send error to %s:%s -> %s", host, port, exc, exc_info=False)

                await asyncio.sleep(self._udp_interval)
        except asyncio.CancelledError:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
            _LOGGER.debug("UDP discovery loop stopped")

    # ---------------- Request helpers (TCP) ----------------

    async def send_bulk(self, requests: List[bytes], timeout: float = 5.0) -> List[Optional[bytes]]:
        """
        Send multiple FF 04 requests and collect replies.
        Each request already includes header+payload (built by modbusclient helpers).

        If no inverter is connected yet, we return a list of None (non-blocking).
        """
        if not requests:
            return []
        async with self._lock:
            # Make sure we're listening, but DO NOT block waiting for a client
            await self.ensure_listening()
            if not self.is_connected():
                _LOGGER.debug("No inverter connection yet; skipping this cycle")
                return [None for _ in requests]

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
                    # Keep server up for the next attempt.
                    results.append(None)
            return results

    async def send_ascii_command(self, ascii_command_packet: bytes, timeout: float = 5.0) -> Optional[bytes]:
        """
        Send a *single* prebuilt ASCII packet (full FF 04 wrapper already built)
        and return the full raw response bytes (header+payload), or None on error.

        If no inverter is connected yet, return None immediately (non-blocking).
        """
        async with self._lock:
            await self.ensure_listening()
            if not self.is_connected():
                _LOGGER.debug("No inverter connection yet; skipping settings command")
                return None
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
                return None
