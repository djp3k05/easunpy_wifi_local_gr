# async_modbusclient.py
import asyncio
import logging
import socket
import time
from typing import Set, Optional

logger = logging.getLogger(__name__)

class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocol for UDP discovery of the inverter."""
    def __init__(self, inverter_ip: str, message: bytes):
        self.transport: Optional[asyncio.transports.DatagramTransport] = None
        self.inverter_ip = inverter_ip
        self.message = message
        loop = asyncio.get_event_loop()
        self.response_received = loop.create_future()

    def connection_made(self, transport):
        self.transport = transport
        logger.debug(f"Sending UDP discovery message to {self.inverter_ip}:58899")
        self.transport.sendto(self.message, (self.inverter_ip, 58899))

    def datagram_received(self, data, addr):
        logger.info(f"Received discovery response from {addr}")
        if not self.response_received.done():
            self.response_received.set_result(True)

    def error_received(self, exc):
        logger.error(f"UDP discovery error received: {exc}")
        if not self.response_received.done():
            self.response_received.set_result(False)


class AsyncModbusClient:
    """
    Minimal server-side TCP endpoint used by Easun/Voltronic WiFi modules:
    - We send UDP: set>server=<local_ip>:<port>;
    - The inverter connects back to us on that TCP port and we exchange frames.
    """
    def __init__(self, inverter_ip: str, local_ip: str, port: int = 8899):
        self.inverter_ip = inverter_ip
        self.local_ip = local_ip
        self.port = port

        self._lock = asyncio.Lock()
        self._server: Optional[asyncio.AbstractServer] = None
        self._active_connections: Set[asyncio.StreamWriter] = set()
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

        self._connection_future: Optional[asyncio.Future] = None
        self._connection_established = False
        self._last_activity = 0.0
        self._connection_timeout = 30  # idle connection considered stale

    async def _cleanup_server(self):
        """Close all sockets and reset state to allow fresh binds."""
        try:
            # close client sockets
            for w in list(self._active_connections):
                try:
                    if not w.is_closing():
                        w.close()
                        await w.wait_closed()
                except Exception as e:
                    logger.debug(f"Error closing connection: {e}")
                finally:
                    self._active_connections.discard(w)

            # close server socket
            if self._server is not None:
                try:
                    self._server.close()
                    await self._server.wait_closed()
                    logger.debug("Server cleaned up successfully")
                except Exception as e:
                    logger.debug(f"Error closing server: {e}")
            await asyncio.sleep(0.2)  # give OS time to release the port
        finally:
            self._server = None
            self._reader = None
            self._writer = None
            self._connection_established = False
            if self._connection_future and not self._connection_future.done():
                self._connection_future.set_result(False)
            self._connection_future = None

    async def _send_udp_discovery(self) -> bool:
        """Send UDP discovery message and wait briefly for any reply."""
        message = f"set>server={self.local_ip}:{self.port};".encode()
        try:
            transport, protocol = await asyncio.get_event_loop().create_datagram_endpoint(
                lambda: DiscoveryProtocol(self.inverter_ip, message),
                remote_addr=(self.inverter_ip, 58899),
            )
            try:
                await asyncio.wait_for(protocol.response_received, timeout=2)
                return True
            except asyncio.TimeoutError:
                logger.warning("UDP discovery response timed out")
                return False
            finally:
                transport.close()
        except Exception as e:
            logger.error(f"UDP discovery error: {e}")
            return False

    async def _ensure_connection(self) -> bool:
        """
        Make sure we have a live connection:
        - If stale, clean up.
        - Else start a server and wait for inverter to connect back.
        """
        # active and not stale?
        if self._connection_established and (time.time() - self._last_activity) < self._connection_timeout:
            logger.debug("Reusing existing TCP connection")
            return True
        if self._server is not None:
            logger.debug("Cleaning up previous server before starting a new one")
            await self._cleanup_server()

        # tell inverter where to connect
        if not await self._send_udp_discovery():
            return False

        # create the TCP server and wait for accept
        try:
            self._connection_future = asyncio.get_event_loop().create_future()
            self._server = await asyncio.start_server(
                self._handle_connection,
                self.local_ip,
                self.port,
                reuse_address=True,  # allow quick rebinds if we had timeouts/cancels
                start_serving=True,
            )
            logger.debug(f"TCP server listening on {self.local_ip}:{self.port}")
            try:
                # give the inverter a bit more time to connect back
                await asyncio.wait_for(self._connection_future, timeout=10)
                return self._connection_established
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for client connection")
                await self._cleanup_server()
                return False
        except OSError as e:
            logger.error(f"Failed to start TCP server: {e}")
            # try to cleanup and give OS a moment, then signal failure
            await self._cleanup_server()
            return False
        except Exception as e:
            logger.error(f"Unexpected error starting TCP server: {e}")
            await self._cleanup_server()
            return False

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Accept callback from asyncio.start_server()."""
        addr = writer.get_extra_info("peername")
        logger.info(f"Client connected from {addr}")

        # Replace any prior client
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

        self._reader = reader
        self._writer = writer
        self._active_connections.add(writer)
        self._connection_established = True
        self._last_activity = time.time()

        if self._connection_future and not self._connection_future.done():
            self._connection_future.set_result(True)

    async def send_bulk(self, hex_commands: list[str], retry_count: int = 5) -> list[str]:
        """Send multiple frames on a single connection. Returns hex responses in order."""
        async with self._lock:
            for attempt in range(retry_count):
                responses: list[str] = []
                ok = await self._ensure_connection()
                if not ok:
                    await asyncio.sleep(1)
                    continue

                try:
                    for command in hex_commands:
                        if self._writer is None or self._writer.is_closing():
                            logger.warning("Connection closed while sending commands")
                            self._connection_established = False
                            break

                        logger.debug(f"Sending command: {command}")
                        self._writer.write(bytes.fromhex(command))
                        await self._writer.drain()

                        # Read MBAP header to know length, then read rest
                        resp = await asyncio.wait_for(self._reader.read(6), timeout=5)
                        if len(resp) < 6:
                            raise asyncio.TimeoutError("Short MBAP header")
                        expected = int.from_bytes(resp[4:6], "big")
                        body = await asyncio.wait_for(self._reader.read(expected), timeout=5)
                        response = (resp + body).hex()
                        logger.debug(f"Response: {response}")
                        responses.append(response)
                        self._last_activity = time.time()
                        await asyncio.sleep(0.05)

                    if len(responses) == len(hex_commands):
                        return responses

                except asyncio.TimeoutError as e:
                    logger.error(f"Timeout reading response: {e}")
                except Exception as e:
                    logger.error(f"Error during bulk send: {e}")

                # something went wrong, reset connection and retry
                await self._cleanup_server()
                await asyncio.sleep(1)

            logger.error("Failed to establish connection after all attempts")
            return []
