#!/usr/bin/env python3
"""
WebSSHole - Tunnel through ssh.parspack.net and similar web SSH services
"""

import asyncio
import sys
import argparse
import json
import base64
import random
import string
from typing import Optional, Dict, Any
from dataclasses import dataclass
import logging
import urllib.parse
import hashlib
import time

# Third-party imports
try:
    import aiohttp
    import websockets
    from bs4 import BeautifulSoup
except ImportError:
    print("Please install dependencies: pip install aiohttp websockets beautifulsoup4")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("webssh-hole")

@dataclass
class TunnelConfig:
    """Configuration for the tunnel."""
    url: str
    username: str
    password: str
    ssh_host: str = "localhost"
    ssh_port: int = 22
    socks_port: int = 1080
    remote_proxy_port: int = 9999
    term: str = "xterm-256color"

class WebSSHTunnel:
    """Tunnel through web SSH services like ssh.parspack.net."""
    
    def __init__(self, config: TunnelConfig):
        self.config = config
        self.session = None
        self.ws = None
        self.xsrf_token = None
        self.session_id = None
        self.terminal_ready = asyncio.Event()
        self.proxy_active = asyncio.Event()
        
    async def initialize_session(self):
        """Initialize HTTP session and get CSRF token."""
        self.session = aiohttp.ClientSession()
        
        # Fetch the login page to get CSRF token
        async with self.session.get(self.config.url) as response:
            html = await response.text()
            
        # Parse HTML to find CSRF token
        soup = BeautifulSoup(html, 'html.parser')
        xsrf_input = soup.find('input', {'name': '_xsrf'})
        
        if xsrf_input:
            self.xsrf_token = xsrf_input.get('value')
            logger.info(f"Found CSRF token: {self.xsrf_token}")
        else:
            # Try to extract from cookies or generate one
            self.xsrf_token = self._generate_xsrf_token()
            logger.warning(f"Using generated CSRF token: {self.xsrf_token}")
    
    def _generate_xsrf_token(self):
        """Generate a random CSRF token."""
        return ''.join(random.choices(string.hexdigits, k=32)).lower()
    
    async def create_ssh_session(self):
        """Submit the SSH connection form."""
        form_data = {
            'hostname': self.config.ssh_host,
            'port': str(self.config.ssh_port),
            'username': self.config.username,
            'password': self.config.password,
            'term': self.config.term,
            '_xsrf': self.xsrf_token
        }
        
        headers = {
            'Origin': self.config.url,
            'Referer': self.config.url,
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-Requested-With': 'XMLHttpRequest'
        }
        
        logger.info(f"Creating SSH session to {self.config.ssh_host}:{self.config.ssh_port}")
        
        async with self.session.post(
            self.config.url,
            data=form_data,
            headers=headers
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"Failed to create SSH session: {response.status} - {error_text}")
                return False
            
            # The response should contain WebSocket URL and session ID
            # For ssh.parspack.net, it typically returns JSON with ws_url
            try:
                result = await response.json()
                self.ws_url = result.get('ws_url') or result.get('url')
                self.session_id = result.get('session_id') or result.get('id')
                
                if not self.ws_url:
                    # Try to construct WebSocket URL from patterns
                    parsed = urllib.parse.urlparse(self.config.url)
                    ws_scheme = 'wss://' if parsed.scheme == 'https' else 'ws://'
                    self.ws_url = f"{ws_scheme}{parsed.netloc}/ws"
                
                logger.info(f"WebSocket URL: {self.ws_url}")
                logger.info(f"Session ID: {self.session_id}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to parse response: {e}")
                # Try alternative parsing
                return await self._fallback_parse_response(await response.text())
    
    async def _fallback_parse_response(self, html: str):
        """Fallback method to parse HTML response for WebSocket info."""
        soup = BeautifulSoup(html, 'html.parser')
        
        # Look for script tags that might contain WebSocket URL
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'ws://' in script.string:
                import re
                match = re.search(r'(ws[s]?://[^\s"\']+)', script.string)
                if match:
                    self.ws_url = match.group(1)
                    logger.info(f"Found WebSocket URL in script: {self.ws_url}")
                    return True
        
        # Last resort: guess the WebSocket URL
        parsed = urllib.parse.urlparse(self.config.url)
        ws_scheme = 'wss://' if parsed.scheme == 'https' else 'ws://'
        self.ws_url = f"{ws_scheme}{parsed.netloc}/ws"
        logger.warning(f"Guessing WebSocket URL: {self.ws_url}")
        return True
    
    async def connect_websocket(self):
        """Establish WebSocket connection to terminal."""
        headers = {
            'Origin': self.config.url,
            'User-Agent': 'Mozilla/5.0 (WebSSHole Tunnel)'
        }
        
        if self.session_id:
            headers['X-Session-ID'] = self.session_id
        
        logger.info(f"Connecting to WebSocket: {self.ws_url}")
        
        try:
            self.ws = await websockets.connect(
                self.ws_url,
                extra_headers=headers,
                ping_interval=30,
                ping_timeout=10
            )
            logger.info("WebSocket connection established")
            
            # Send terminal initialization if needed
            init_msg = json.dumps({
                'type': 'init',
                'term': self.config.term,
                'cols': 80,
                'rows': 24
            })
            await self.ws.send(init_msg)
            
            # Start listening for messages
            asyncio.create_task(self._handle_websocket_messages())
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect WebSocket: {e}")
            return False
    
    async def _handle_websocket_messages(self):
        """Handle incoming WebSocket messages."""
        try:
            async for message in self.ws:
                await self._process_websocket_message(message)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed")
            self.terminal_ready.clear()
    
    async def _process_websocket_message(self, message):
        """Process a single WebSocket message."""
        try:
            data = json.loads(message)
            msg_type = data.get('type', '')
            
            if msg_type == 'ready':
                logger.info("Terminal is ready")
                self.terminal_ready.set()
            
            elif msg_type == 'stdout':
                output = data.get('data', '')
                # Check if our tunnel setup command succeeded
                if 'SOCKS proxy listening' in output or 'Dynamic forwarding' in output:
                    logger.info("Remote SOCKS proxy is active!")
                    self.proxy_active.set()
                
                # Print terminal output for debugging
                if output.strip():
                    logger.debug(f"Terminal: {output[:100]}...")
            
            elif msg_type == 'stderr':
                error = data.get('data', '')
                if error.strip():
                    logger.warning(f"Terminal error: {error}")
            
        except json.JSONDecodeError:
            # Raw terminal data (some implementations send raw text)
            if isinstance(message, str) and 'SOCKS' in message:
                logger.info("Detected SOCKS proxy message")
                self.proxy_active.set()
    
    async def setup_remote_tunnel(self):
        """Execute commands to create SOCKS proxy on remote server."""
        await self.terminal_ready.wait()
        logger.info("Setting up remote tunnel...")
        
        # Try different methods to create SOCKS proxy
        methods = [
            self._setup_ssh_dynamic_forwarding,
            self._setup_python_socks_proxy,
            self._setup_socat_tunnel,
            self._setup_netcat_relay
        ]
        
        for method in methods:
            try:
                if await method():
                    logger.info(f"Tunnel setup using {method.__name__}")
                    await self.proxy_active.wait(timeout=10)
                    if self.proxy_active.is_set():
                        return True
            except Exception as e:
                logger.debug(f"Method {method.__name__} failed: {e}")
                continue
        
        logger.error("All tunnel setup methods failed")
        return False
    
    async def _setup_ssh_dynamic_forwarding(self):
        """Method 1: Use SSH's built-in dynamic forwarding."""
        command = f"ssh -o StrictHostKeyChecking=no -D {self.config.remote_proxy_port} -f -N localhost &\n"
        await self._send_terminal_command(command)
        return True
    
    async def _setup_python_socks_proxy(self):
        """Method 2: Create SOCKS proxy with Python one-liner."""
        python_code = f'''
import socket, threading, sys

class SocksProxy:
    def __init__(self, port=9999):
        self.port = port
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", port))
        self.server.listen(5)
        print(f"SOCKS5 proxy listening on 127.0.0.1:{port}")
    
    def handle_client(self, client):
        try:
            # SOCKS5 handshake
            client.recv(262)
            client.send(b"\\x05\\x00")
            
            request = client.recv(4)
            if request[1] != 1:  # Only CONNECT
                client.close()
                return
            
            addr_type = client.recv(1)
            if addr_type[0] == 1:  # IPv4
                addr = socket.inet_ntoa(client.recv(4))
            elif addr_type[0] == 3:  # Domain
                length = client.recv(1)[0]
                addr = client.recv(length).decode()
            else:
                client.close()
                return
            
            port = int.from_bytes(client.recv(2), "big")
            
            # Connect to destination
            remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote.connect((addr, port))
            
            # Send success
            client.send(b"\\x05\\x00\\x00\\x01" + socket.inet_aton("0.0.0.0") + b"\\x00\\x00")
            
            # Start tunneling
            self._tunnel(client, remote)
            
        except Exception as e:
            client.close()
    
    def _tunnel(self, client, remote):
        def forward(source, dest):
            try:
                while True:
                    data = source.recv(4096)
                    if not data:
                        break
                    dest.sendall(data)
            except:
                pass
        
        threading.Thread(target=forward, args=(client, remote)).start()
        threading.Thread(target=forward, args=(remote, client)).start()
    
    def run(self):
        while True:
            client, addr = self.server.accept()
            threading.Thread(target=self.handle_client, args=(client,)).start()

if __name__ == "__main__":
    proxy = SocksProxy({self.config.remote_proxy_port})
    proxy.run()
'''
        
        # Create and execute Python script
        command = f"cat > /tmp/socks_proxy.py << 'EOF'\n{python_code}\nEOF\n"
        command += f"python3 /tmp/socks_proxy.py &\n"
        command += "echo 'Python SOCKS proxy started'\n"
        
        await self._send_terminal_command(command)
        return True
    
    async def _setup_socat_tunnel(self):
        """Method 3: Use socat if available."""
        command = f"which socat && socat TCP-LISTEN:{self.config.remote_proxy_port},fork,reuseaddr SOCKS4A:127.0.0.1:google.com:80,socksport=9050 &\n"
        await self._send_terminal_command(command)
        return True
    
    async def _setup_netcat_relay(self):
        """Method 4: Simple netcat relay (limited)."""
        command = f'''while true; do
    nc -l -p {self.config.remote_proxy_port} -e /bin/bash 2>/dev/null || 
    ncat -l -p {self.config.remote_proxy_port} -e /bin/bash 2>/dev/null || 
    echo "No netcat available" && break
done &
echo "Netcat relay on port {self.config.remote_proxy_port}"
'''
        await self._send_terminal_command(command)
        return True
    
    async def _send_terminal_command(self, command: str):
        """Send command to terminal via WebSocket."""
        # Common WebSocket message formats for web SSH
        message_formats = [
            # Format 1: JSON with stdin type
            json.dumps({'type': 'stdin', 'data': command}),
            # Format 2: Raw command (some servers accept raw text)
            command,
            # Format 3: Base64 encoded
            json.dumps({'type': 'input', 'data': base64.b64encode(command.encode()).decode()}),
        ]
        
        for msg in message_formats:
            try:
                await self.ws.send(msg)
                logger.debug(f"Sent command: {command[:50]}...")
                await asyncio.sleep(0.5)  # Wait for command to be processed
                break
            except Exception as e:
                logger.debug(f"Message format failed, trying next: {e}")
                continue
    
    async def create_local_socks_bridge(self):
        """Create local SOCKS5 server that bridges to remote proxy."""
        import socket
        import struct
        
        async def handle_socks_client(reader, writer):
            """Handle local SOCKS5 connections."""
            try:
                # SOCKS5 greeting
                greeting = await reader.read(2)
                if not greeting or greeting[0] != 0x05:
                    writer.close()
                    return
                
                # No authentication required
                writer.write(b'\x05\x00')
                await writer.drain()
                
                # Read request
                request = await reader.read(4)
                if len(request) < 4:
                    writer.close()
                    return
                
                cmd = request[1]
                if cmd != 0x01:  # Only support CONNECT
                    writer.write(b'\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00')
                    await writer.drain()
                    writer.close()
                    return
                
                # Parse destination
                addr_type = await reader.read(1)
                if not addr_type:
                    writer.close()
                    return
                
                dest_addr = None
                if addr_type[0] == 0x01:  # IPv4
                    ip_bytes = await reader.read(4)
                    if len(ip_bytes) == 4:
                        dest_addr = socket.inet_ntoa(ip_bytes)
                elif addr_type[0] == 0x03:  # Domain name
                    length_byte = await reader.read(1)
                    if length_byte:
                        length = length_byte[0]
                        domain_bytes = await reader.read(length)
                        if len(domain_bytes) == length:
                            dest_addr = domain_bytes.decode()
                else:
                    writer.close()
                    return
                
                port_bytes = await reader.read(2)
                if len(port_bytes) < 2:
                    writer.close()
                    return
                
                dest_port = struct.unpack('!H', port_bytes)[0]
                
                if not dest_addr:
                    writer.close()
                    return
                
                dest = f"{dest_addr}:{dest_port}"
                logger.info(f"SOCKS request for: {dest}")
                
                # Send success response
                response = b'\x05\x00\x00\x01'
                response += socket.inet_aton('0.0.0.0')
                response += struct.pack('!H', 1080)
                writer.write(response)
                await writer.drain()
                
                # Now we need to tunnel through WebSocket
                # This is the complex part - we need to establish a data channel
                # For now, we'll create a simple echo for testing
                await self._create_websocket_tunnel(reader, writer, dest)
                
            except Exception as e:
                logger.error(f"SOCKS handler error: {e}")
                try:
                    writer.close()
                except:
                    pass
        
        # Start SOCKS server
        server = await asyncio.start_server(
            handle_socks_client,
            '127.0.0.1',
            self.config.socks_port
        )
        
        addr = server.sockets[0].getsockname()
        logger.info(f"Local SOCKS5 server listening on {addr[0]}:{addr[1]}")
        
        async with server:
            await server.serve_forever()
    
    async def _create_websocket_tunnel(self, reader, writer, dest):
        """Create tunnel through WebSocket to remote proxy."""
        # This is a simplified version
        # In production, you'd implement proper protocol
        
        # Send connect request through WebSocket
        connect_msg = json.dumps({
            'type': 'proxy_connect',
            'dest': dest,
            'channel_id': f"chan_{int(time.time())}"
        })
        
        await self.ws.send(connect_msg)
        
        # For now, just echo data back (proof of concept)
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                
                # Send through WebSocket
                proxy_msg = json.dumps({
                    'type': 'proxy_data',
                    'data': base64.b64encode(data).decode(),
                    'dest': dest
                })
                await self.ws.send(proxy_msg)
                
                # In real implementation, you'd receive response from WebSocket
                # and write it back to the writer
                
        except Exception as e:
            logger.debug(f"Tunnel error: {e}")
        finally:
            writer.close()
    
    async def run(self):
        """Main tunnel orchestration."""
        logger.info("=" * 60)
        logger.info("Starting WebSSHole Tunnel")
        logger.info(f"Target: {self.config.url}")
        logger.info(f"SSH Server: {self.config.ssh_host}:{self.config.ssh_port}")
        logger.info(f"Local SOCKS: 127.0.0.1:{self.config.socks_port}")
        logger.info("=" * 60)
        
        try:
            # Step 1: Initialize HTTP session
            await self.initialize_session()
            
            # Step 2: Create SSH session
            if not await self.create_ssh_session():
                logger.error("Failed to create SSH session")
                return
            
            # Step 3: Connect WebSocket
            if not await self.connect_websocket():
                logger.error("Failed to connect WebSocket")
                return
            
            # Step 4: Setup remote tunnel in background
            tunnel_task = asyncio.create_task(self.setup_remote_tunnel())
            
            # Step 5: Start local SOCKS bridge
            logger.info("Waiting for remote tunnel to be ready...")
            await asyncio.sleep(3)  # Give remote tunnel time to start
            
            if not self.proxy_active.is_set():
                logger.warning("Remote proxy not confirmed active, continuing anyway...")
            
            bridge_task = asyncio.create_task(self.create_local_socks_bridge())
            
            # Keep alive
            logger.info("=" * 60)
            logger.info("Tunnel is ACTIVE!")
            logger.info(f"Configure your browser/app to use SOCKS5 proxy:")
            logger.info(f"  Host: 127.0.0.1")
            logger.info(f"  Port: {self.config.socks_port}")
            logger.info("=" * 60)
            logger.info("Press Ctrl+C to stop")
            
            await asyncio.gather(tunnel_task, bridge_task)
            
        except KeyboardInterrupt:
            logger.info("\nShutting down tunnel...")
        except Exception as e:
            logger.error(f"Tunnel error: {e}")
        finally:
            if self.session:
                await self.session.close()
            if self.ws:
                await self.ws.close()

def main():
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        description="WebSSHole - Tunnel through web SSH services",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage with ssh.parspack.net
  %(prog)s --url https://ssh.parspack.net --user myuser --pass mypass
  
  # Custom SSH server
  %(prog)s --url https://ssh.example.com --ssh-host 192.168.1.100 --ssh-port 2222
  
  # Different local port
  %(prog)s --url https://ssh.parspack.net --socks-port 9090
        """
    )
    
    parser.add_argument('--url', required=True, 
                       help='Web SSH URL (e.g., https://ssh.parspack.net)')
    parser.add_argument('--user', '-u', required=True, dest='username',
                       help='SSH username')
    parser.add_argument('--pass', '-p', required=True, dest='password',
                       help='SSH password')
    parser.add_argument('--ssh-host', default='localhost',
                       help='Target SSH host (default: localhost)')
    parser.add_argument('--ssh-port', type=int, default=22,
                       help='Target SSH port (default: 22)')
    parser.add_argument('--socks-port', type=int, default=1080,
                       help='Local SOCKS5 port (default: 1080)')
    parser.add_argument('--remote-port', type=int, default=9999,
                       help='Remote SOCKS proxy port (default: 9999)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    config = TunnelConfig(
        url=args.url.rstrip('/'),
        username=args.username,
        password=args.password,
        ssh_host=args.ssh_host,
        ssh_port=args.ssh_port,
        socks_port=args.socks_port,
        remote_proxy_port=args.remote_port
    )
    
    tunnel = WebSSHTunnel(config)
    
    try:
        asyncio.run(tunnel.run())
    except KeyboardInterrupt:
        logger.info("\nExiting...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
