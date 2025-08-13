# async_modbusclient.py full code
import asyncio
import logging
import socket
import time

# Set up logging
logger = logging.getLogger(__name__)

class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocol for UDP discovery of the inverter."""
    def __init__(self, inverter_ip, message):
        self.transport = None
        self.inverter_ip = inverter_ip
        self.message = message
        self.response_received = asyncio.get_event_loop().create_future()

    def connection_made(self, transport):
        self.transport = transport
        logger.debug(f"Sending UDP discovery message to {self.inverter_ip}:58899")
        self.transport.sendto(self.message)

    def datagram_received(self, data, addr):
        logger.info(f"Received response from {addr}")
        if not self.response_received.done():
            self.response_received.set_result(True)

    def error_received(self, exc):
        logger.error(f"Error received: {exc}")
        if not self.response_received.done():
            self.response_received.set_result(False)

class AsyncModbusClient:
    def __init__(self, inverter_ip: str, local_ip: str, port: int = 8899, *, require_udp: bool = True):
        """
        require_udp=True  -> normal Modbus-over-WiFi workflow (8899), UDP is needed to point the module.
        require_udp=False -> Voltronic ASCII workflow (502), start listener regardless of UDP.
        """
        self.inverter_ip = inverter_ip
        self.local_ip = local_ip
        self.port = port
        self.require_udp = require_udp

        self._lock = asyncio.Lock()
        self._server = None
        self._consecutive_udp_failures = 0
        self._base_timeout = 5
        self._active_connections = set()
        self._reader = None
        self._writer = None
        self._connection_established = False
        self._last_activity = 0
        self._connection_timeout = 30  # seconds before considering connection stale
        self._connection_future = None

    async def _cleanup_server(self):
        """Cleanup server and all active connections."""
        try:
            for writer in self._active_connections.copy():
                try:
                    if not writer.is_closing():
                        writer.close()
                        await writer.wait_closed()
                except Exception as e:
                    logger.debug(f"Error closing connection: {e}")
                finally:
                    self._active_connections.remove(writer)

            if self._server:
                try:
                    self._server.close()
                    await self._server.wait_closed()
                    logger.debug("Server cleaned up successfully")
                except Exception as e:
                    logger.debug(f"Error closing server: {e}")
                finally:
                    self._server = None

            await asyncio.sleep(0.5)
        finally:
            self._server = None
            self._active_connections.clear()
            self._connection_established = False
            self._reader = None
            self._writer = None
            if self._connection_future and not self._connection_future.done():
                self._connection_future.set_result(False)

    async def _find_available_port(self, start_port: int = 8899, max_attempts: int = 20) -> int:
        """Find an available port starting from the given port."""
        port = start_port
        for _ in range(max_attempts):
            try:
                server = await asyncio.get_event_loop().create_server(lambda: None, self.local_ip, port)
                server.close()
                await server.wait_closed()
                return port
            except OSError:
                port += 1
        raise RuntimeError(f"No available port found after {max_attempts} attempts")

    async def _send_udp_discovery(self) -> bool:
        """Send UDP discovery message and wait for response with timeout."""
        message = f"set>server={self.local_ip}:{self.port};".encode()

        try:
            transport, protocol = await asyncio.get_event_loop().create_datagram_endpoint(
                lambda: DiscoveryProtocol(self.inverter_ip, message),
                remote_addr=(self.inverter_ip, 58899)
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
        """Ensure TCP connection is established (listener + accepted client)."""
        if self._connection_established:
            if time.time() - self._last_activity > self._connection_timeout:
                logger.debug("Connection stale, cleaning up")
                await self._cleanup_server()
            else:
                return True

        # For Modbus/8899 we try to notify the Wi‑Fi module; for ASCII we just listen.
        if self.require_udp:
            udp_ok = await self._send_udp_discovery()
            if not udp_ok:
                self._consecutive_udp_failures += 1
                logger.warning("UDP discovery failed; continuing to listen anyway (device may already be pointed).")
                # Keep same port for a while; only rotate if we keep failing repeatedly.
                if self._consecutive_udp_failures >= 3:
                    try:
                        new_port = await self._find_available_port(self.port + 1)
                        logger.error(f"Multiple UDP failures; switching listening port {self.port} -> {new_port}")
                        self.port = new_port
                    except Exception as e:
                        logger.error(f"Unable to change port after UDP failures: {e}")
                    finally:
                        self._consecutive_udp_failures = 0

        # Start TCP server unconditionally so devices already configured can connect.
        try:
            self._connection_future = asyncio.Future()
            self._server = await asyncio.start_server(self._handle_connection, self.local_ip, self.port)
            logger.debug(f"TCP server listening on {self.local_ip}:{self.port}")
            try:
                await asyncio.wait_for(self._connection_future, timeout=10)
                return True
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for client connection")
                await self._cleanup_server()
                return False
        except PermissionError as e:
            # Helpful hint for privileged ports like 502
            logger.error(f"Failed to start TCP server on {self.local_ip}:{self.port} (permission denied). "
                         f"Try running with permissions that allow binding to low ports or use a higher port. Error: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to start TCP server: {e}")
            return False

    async def _handle_connection(self, reader, writer):
        """Handle incoming client connection."""
        addr = writer.get_extra_info('peername')
        logger.info(f"Client connected from {addr}")

        # If there's an existing connection, close it
        if self._writer and not self._writer.is_closing():
            logger.warning("Existing connection found, closing it")
            self._writer.close()
            await self._writer.wait_closed()

        self._reader = reader
        self._writer = writer
        self._connection_established = True
        self._last_activity = time.time()
        self._active_connections.add(writer)
        if self._connection_future and not self._connection_future.done():
            self._connection_future.set_result(True)

    async def send_bulk(self, hex_commands: list[str], retry_count: int = 5) -> list[str]:
        """Send multiple Modbus/ASCII commands using persistent connection."""
        async with self._lock:
            responses: list[str] = []

            for attempt in range(retry_count):
                try:
                    if not await self._ensure_connection():
                        if attempt == retry_count - 1:
                            logger.error("Failed to establish connection after all attempts")
                            return []
                        await asyncio.sleep(1)
                        continue

                    for command in hex_commands:
                        try:
                            if self._writer.is_closing():
                                logger.warning("Connection closed while processing commands")
                                self._connection_established = False
                                break

                            logger.debug(f"Sending command: {command}")
                            command_bytes = bytes.fromhex(command)
                            self._writer.write(command_bytes)
                            await self._writer.drain()

                            response = await asyncio.wait_for(self._reader.read(1024), timeout=5)
                            if len(response) >= 6:
                                expected_length = int.from_bytes(response[4:6], 'big') + 6
                                while len(response) < expected_length:
                                    chunk = await asyncio.wait_for(self._reader.read(1024), timeout=5)
                                    if not chunk:
                                        break
                                    response += chunk

                            logger.debug(f"Response: {response.hex()}")
                            responses.append(response.hex())
                            self._last_activity = time.time()
                            await asyncio.sleep(0.1)

                        except asyncio.TimeoutError:
                            logger.error(f"Timeout reading response for command: {command}")
                            self._connection_established = False
                            break
                        except Exception as e:
                            logger.error(f"Error processing command {command}: {e}")
                            self._connection_established = False
                            break

                    if len(responses) == len(hex_commands):
                        return responses

                except Exception as e:
                    logger.error(f"Bulk send error: {e}")
                    self._connection_established = False
                    await self._cleanup_server()

                await asyncio.sleep(1)

            return []
