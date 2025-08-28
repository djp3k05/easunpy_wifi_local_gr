# modbusclient.py full code
import socket
import struct
import time
import logging  # Import logging

from easunpy.crc import crc16_modbus, crc16_xmodem, adjust_crc_byte

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ModbusClient:
    def __init__(self, inverter_ip: str, local_ip: str, port: int = 8899):
        self.inverter_ip = inverter_ip
        self.local_ip = local_ip
        self.port = port
        self.request_id = 0  # Add request ID counter

    def send_udp_discovery(self) -> bool:
        """Perform UDP discovery to initialize the inverter communication."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_sock:
            udp_message = f"set>server={self.local_ip}:{self.port};"
            try:
                logger.debug(f"Sending UDP discovery message to {self.inverter_ip}:58899")
                udp_sock.sendto(udp_message.encode(), (self.inverter_ip, 58899))
                response, _ = udp_sock.recvfrom(1024)
                return True
            except socket.timeout:
                logger.error("UDP discovery timed out")
                return False
            except Exception as e:
                logger.error(f"Error sending UDP discovery message: {e}")
                return False

    def send(self, hex_command: str, retry_count: int = 2) -> str:
        """Send a Modbus TCP command."""
        command_bytes = bytes.fromhex(hex_command)
        logger.info(f"Sending command: {hex_command}")

        for attempt in range(retry_count):
            logger.debug(f"Attempt {attempt + 1} of {retry_count}")
            
            if not self.send_udp_discovery():
                logger.info("UDP discovery failed")
                time.sleep(1)
                continue

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_server:
                tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
                
                try:
                    # Attempt to bind to the local IP and port
                    logger.debug(f"Binding to {self.local_ip}:{self.port}")
                    tcp_server.bind((self.local_ip, self.port))
                    tcp_server.listen(1)

                    logger.debug("Waiting for client connection...")
                    client_sock, addr = tcp_server.accept()
                    logger.info(f"Client connected from {addr}")
                    
                    with client_sock:
                        logger.debug("Sending command bytes...")
                        client_sock.sendall(command_bytes)

                        logger.debug("Waiting for response...")
                        response = client_sock.recv(4096)
                        if response:
                            logger.debug(f"Received response: {response.hex()}")
                            return response.hex()
                        else:
                            logger.warning("No response received")
                except Exception as e:
                    logger.error(f"Error during TCP communication: {e}")
                
            time.sleep(1)
        
        return ""

def create_request(transaction_id: int, protocol_id: int, unit_id: int, function_code: int, start: int, count: int) -> str:
    """
    Creates a Modbus TCP request in hexadecimal format.
    :param transaction_id: Transaction ID.
    :param protocol_id: Protocol ID.
    :param unit_id: Unit ID.
    :param function_code: Function code.
    :param start: Starting register address.
    :param count: Number of registers to read.
    :return: Hexadecimal string of the Modbus request.
    """
    # Construir el paquete RTU (sin TCP header)
    rtu_packet = bytearray([
        unit_id, function_code,
        (start >> 8) & 0xFF, start & 0xFF,
        (count >> 8) & 0xFF, count & 0xFF
    ])
    
    # Calcular CRC sobre el paquete RTU
    crc = crc16_modbus(rtu_packet)
    crc_low = crc & 0xFF
    crc_high = (crc >> 8) & 0xFF
    rtu_packet.extend([crc_low, crc_high])
    
    # Campo adicional `FF04`
    rtu_packet = bytearray([0xFF, 0x04]) + rtu_packet
    
    # Calcular la longitud total (incluye solo RTU) + TCP
    length = len(rtu_packet)
    
    # Construir el comando completo
    command = bytearray([
        (transaction_id >> 8) & 0xFF, transaction_id & 0xFF,  # Transaction ID
        (protocol_id >> 8) & 0xFF, protocol_id & 0xFF,        # Protocol ID
        (length >> 8) & 0xFF, length & 0xFF                  # Longitud
    ]) + rtu_packet

    return command.hex()

def decode_modbus_response(response: str, register_count: int=1, data_format: str="Int"):
    """
    Decodes a Modbus TCP response using the provided format.
    :param request: Hexadecimal string of the Modbus request.
    :param response: Hexadecimal string of the Modbus response.
    :return: Dictionary with register addresses and their values.
    """
    # Extract common fields from response
    req_id = response[:8]
    length_hex = response[8:12]
    length = int(length_hex, 16)
    
    # Extract RTU payload
    rtu_payload = response[12:12 + length * 2]

    # Decode RTU Payload
    extra_field = rtu_payload[:2]
    device_address = rtu_payload[2:4]
    function_code = rtu_payload[4:6]
    num_data_bytes = int(rtu_payload[8:10], 16)
    data_bytes = rtu_payload[10:10 + num_data_bytes * 2]
    # Decode the register values and pair with addresses
    values = []
    for i, _ in enumerate(range(register_count)):
        if data_format == "Int":
            # Handle signed 16-bit integers
            value = int(data_bytes[i * 4:(i + 1) * 4], 16)
            # If the highest bit is set (value >= 32768), it's negative
            if value >= 32768:  # 0x8000
                value -= 65536  # 0x10000
        elif data_format == "UnsignedInt":
            # Handle unsigned 16-bit integers (0 to 65535)
            value = int(data_bytes[i * 4:(i + 1) * 4], 16)
        elif data_format == "Float":
            value = struct.unpack('f', bytes.fromhex(data_bytes[i * 4:(i + 1) * 4]))[0]
        else:
            raise ValueError(f"Unsupported data format: {data_format}")
        values.append(value)

    return values

def get_registers_from_request(request: str) -> list:
    """
    Extracts register addresses from a Modbus request
    :param request: Hexadecimal string of the Modbus request
    :return: List of register addresses
    """
    rtu_payload = request[12:]  # Skip TCP header
    register_address = int(rtu_payload[8:12], 16)  # Get register address from RTU payload
    register_count = int(rtu_payload[12:16], 16)  # Get number of registers
    
    registers = []
    for i in range(register_count):
        registers.append(register_address + i)
        
    return registers

def create_ascii_request(transaction_id: int, protocol_id: int, command: str) -> str:
    command_bytes = command.encode('ascii')
    crc = crc16_xmodem(command_bytes)
    crc_high = adjust_crc_byte((crc >> 8) & 0xFF)
    crc_low = adjust_crc_byte(crc & 0xFF)
    data = command_bytes + bytes([crc_high, crc_low, 0x0D])
    length = len(data) + 2  # + FF 04
    tcp_header = bytes([
        (transaction_id >> 8) & 0xFF, transaction_id & 0xFF,
        (protocol_id >> 8) & 0xFF, protocol_id & 0xFF,
        (length >> 8) & 0xFF, length & 0xFF
    ])
    rtu_prefix = bytes([0xFF, 0x04])
    full_command = tcp_header + rtu_prefix + data
    return full_command.hex()

def decode_ascii_response(response_hex) -> str:
    # Accept both hex string and raw bytes to avoid 'fromhex() argument must be str' crashes
    if not response_hex:
        return ""
    if isinstance(response_hex, (bytes, bytearray)):
        response = bytes(response_hex)
    else:
        response = bytes.fromhex(response_hex)

    if len(response) < 6:
        return ""
    length = (response[4] << 8) | response[5]
    if len(response) < 6 + length:
        return ""
    payload = response[6:6+length]
    if len(payload) < 3 or payload[0] != 0xFF or payload[1] != 0x04:
        return ""
    data = payload[2:-3]  # remove FF04, CRC (2), \r (1)
    return data.decode('ascii', errors='ignore')
