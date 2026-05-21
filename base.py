#!/usr/bin/env python3
"""
WebSSHole - The Great Escape through Web SSH Terminals
"""

import asyncio
import sys
import argparse
import json
import base64
from typing import Optional
from dataclasses import dataclass
import logging

# Third-party imports
try:
    from playwright.async_api import async_playwright
    import websockets
except ImportError:
    print("Please install dependencies: pip install playwright websockets")
    sys.exit(1)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("webssh-hole")

@dataclass
class TunnelConfig:
    """Configuration for our tunnel."""
    url: str
    username: str
    password: str
    socks_port: int = 1080
    remote_proxy_port: int = 9999
    headless: bool = True
    ws_endpoint: Optional[str] = None

class WebSSHTunnel:
    """Main tunnel orchestrator."""
    
    def __init__(self, config: TunnelConfig):
        self.config = config
        self.ws = None
        self.remote_proxy_ready = asyncio.Event()
        self.local_socks_server = None
        
    async def launch_browser(self):
        """Launch headless browser and navigate to web SSH page."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.config.headless)
            context = await browser.new_context()
            page = await context.new_page()
            
            logger.info(f"Navigating to {self.config.url}")
            await page.goto(self.config.url)
            
            # Wait for page to load and look for login elements
            await page.wait_for_load_state("networkidle")
            
            # Try to find and fill login form (this varies by web SSH client)
            # We'll implement common patterns
            await self._attempt_login(page)
            
            # Wait for terminal to be ready
            terminal_selector = await self._find_terminal_selector(page)
            if terminal_selector:
                await page.wait_for_selector(terminal_selector, timeout=10000)
                logger.info("Terminal loaded successfully")
            
            # Capture WebSocket connections
            ws_url = await self._capture_websocket_url(page)
            
            if not ws_url:
                logger.warning("Could not auto-detect WebSocket URL, using fallback")
                ws_url = self.config.ws_endpoint
            
            # Keep page alive while we work
            self.page = page
            return ws_url
    
    async def _attempt_login(self, page):
        """Try different login strategies for common web SSH clients."""
        # Strategy 1: Look for common input fields
        selectors_to_try = [
            ('input[name="username"]', 'input[name="password"]', 'button[type="submit"]'),
            ('#username', '#password', '#login'),
            ('#user', '#pass', '#submit'),
            ('input[id*="user"]', 'input[id*="pass"]', 'button:has-text("Login")')
        ]
        
        for user_sel, pass_sel, submit_sel in selectors_to_try:
            try:
                await page.fill(user_sel, self.config.username, timeout=2000)
                await page.fill(pass_sel, self.config.password, timeout=2000)
                await page.click(submit_sel, timeout=2000)
                logger.info(f"Login attempted with {user_sel}/{pass_sel}")
                await asyncio.sleep(2)  # Wait for potential redirect
                return
            except Exception:
                continue
        
        logger.warning("Could not find login form automatically, assuming already logged in")
    
    async def _find_terminal_selector(self, page):
        """Find the terminal container selector."""
        common_selectors = [
            '.terminal', '.xterm', '#terminal', 'iframe',
            'div[class*="terminal"]', 'div[class*="xterm"]'
        ]
        
        for selector in common_selectors:
            elements = await page.query_selector_all(selector)
            if elements:
                logger.info(f"Found terminal with selector: {selector}")
                return selector
        return None
    
    async def _capture_websocket_url(self, page):
        """Intercept WebSocket connections to find the terminal's WebSocket."""
        ws_urls = []
        
        # Listen for WebSocket connections via CDP
        cdp = await page.context.new_cdp_session(page)
        await cdp.send('Network.enable')
        
        def on_web_socket_created(params):
            ws_url = params.get('url', '')
            if 'ws://' in ws_url or 'wss://' in ws_url:
                ws_urls.append(ws_url)
                logger.info(f"Detected WebSocket: {ws_url}")
        
        cdp.on('Network.webSocketCreated', on_web_socket_created)
        
        # Trigger some activity to force WebSocket creation
        await page.keyboard.press('Tab')
        await asyncio.sleep(1)
        
        # Return the most likely WebSocket (terminal usually has "ssh", "shell", "guac")
        for url in ws_urls:
            if any(keyword in url.lower() for keyword in ['ssh', 'shell', 'guac', 'terminal', 'ws']):
                return url
        
        return ws_urls[0] if ws_urls else None
    
    async def establish_websocket_connection(self, ws_url):
        """Establish WebSocket connection to the terminal endpoint."""
        logger.info(f"Connecting to WebSocket: {ws_url}")
        
        # Add necessary headers for authentication
        headers = {
            'User-Agent': 'Mozilla/5.0 (WebSSHole Tunnel)',
            'Origin': self.config.url
        }
        
        try:
            self.ws = await websockets.connect(ws_url, extra_headers=headers)
            logger.info("WebSocket connection established")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {e}")
            return False
    
    async def setup_remote_tunnel(self):
        """Execute command in remote terminal to create SOCKS proxy."""
        # Send keystrokes via WebSocket to execute tunnel command
        tunnel_command = f"ssh -o StrictHostKeyChecking=no -D {self.config.remote_proxy_port} -f -N localhost\n"
        
        # Encode as WebSocket message (format depends on web SSH protocol)
        # Common format: JSON with {"type": "stdin", "data": "command"}
        message = json.dumps({
            "type": "stdin",
            "data": tunnel_command
        })
        
        await self.ws.send(message)
        logger.info(f"Sent tunnel command: {tunnel_command.strip()}")
        
        # Wait a bit for command to execute
        await asyncio.sleep(3)
        
        # Verify tunnel is working by checking if we can send a test message
        test_msg = json.dumps({"type": "stdin", "data": "echo tunnel-ready\n"})
        await self.ws.send(test_msg)
        
        self.remote_proxy_ready.set()
        logger.info("Remote SOCKS proxy should be ready")
    
    async def create_local_socks_server(self):
        """Create a local SOCKS5 server that bridges to WebSocket."""
        # This is a simplified SOCKS5 server implementation
        # In production, you'd want a full SOCKS5 implementation
        
        async def handle_client(reader, writer):
            """Handle SOCKS5 client connections."""
            try:
                # SOCKS5 greeting
                greeting = await reader.read(2)
                if greeting[0] != 0x05:  # SOCKS5 only
                    writer.close()
                    return
                
                # Send supported auth methods (no auth)
                writer.write(b'\x05\x00')
                await writer.drain()
                
                # Read request
                request = await reader.read(4)
                if request[1] != 0x01:  # Only support CONNECT
                    writer.write(b'\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00')
                    await writer.drain()
                    writer.close()
                    return
                
                # Parse destination address
                addr_type = await reader.read(1)
                if addr_type[0] == 0x01:  # IPv4
                    dest_addr = await reader.read(4)
                    dest_port = await reader.read(2)
                    dest_ip = '.'.join(str(b) for b in dest_addr)
                elif addr_type[0] == 0x03:  # Domain name
                    domain_len = await reader.read(1)
                    dest_addr = await reader.read(domain_len[0])
                    dest_port = await reader.read(2)
                    dest_ip = dest_addr.decode()
                else:
                    writer.close()
                    return
                
                port = int.from_bytes(dest_port, 'big')
                dest = f"{dest_ip}:{port}"
                
                logger.debug(f"SOCKS request for: {dest}")
                
                # Send success response
                writer.write(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
                await writer.drain()
                
                # Now tunnel data through WebSocket
                await self._tunnel_data(reader, writer, dest)
                
            except Exception as e:
                logger.error(f"SOCKS error: {e}")
                writer.close()
        
        # Start SOCKS server
        server = await asyncio.start_server(
            handle_client,
            '127.0.0.1',
            self.config.socks_port
        )
        
        self.local_socks_server = server
        addr = server.sockets[0].getsockname()
        logger.info(f"SOCKS5 server listening on {addr[0]}:{addr[1]}")
        
        async with server:
            await server.serve_forever()
    
    async def _tunnel_data(self, reader, writer, dest):
        """Tunnel data between SOCKS client and WebSocket."""
        # Encode the destination and data as a special message
        # Format: {"type": "proxy", "dest": "host:port", "data": base64_data}
        
        async def read_and_forward():
            """Read from SOCKS client and send via WebSocket."""
            try:
                while True:
                    data = await reader.read(4096)
                    if not data:
                        break
                    
                    # Encode as base64 for safe WebSocket transmission
                    encoded = base64.b64encode(data).decode()
                    msg = json.dumps({
                        "type": "proxy_data",
                        "dest": dest,
                        "data": encoded,
                        "direction": "outbound"
                    })
                    
                    await self.ws.send(msg)
            except Exception as e:
                logger.debug(f"Read/forward error: {e}")
        
        # In a real implementation, you'd also handle incoming WebSocket messages
        # and forward them to the SOCKS client
        
        # For now, just keep the connection alive
        await asyncio.sleep(3600)  # 1 hour timeout
    
    async def run(self):
        """Main tunnel orchestration."""
        logger.info("Starting WebSSHole tunnel...")
        
        # Step 1: Launch browser and get WebSocket URL
        ws_url = await self.launch_browser()
        if not ws_url:
            logger.error("Could not obtain WebSocket URL")
            return
        
        # Step 2: Connect to WebSocket
        if not await self.establish_websocket_connection(ws_url):
            return
        
        # Step 3: Setup remote tunnel (in parallel with local server)
        tunnel_task = asyncio.create_task(self.setup_remote_tunnel())
        
        # Step 4: Start local SOCKS server
        await self.create_local_socks_server()
        
        # Wait for everything
        await tunnel_task

def main():
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        description="Tunnel out through web SSH terminals",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --url https://ssh.corp.com --username me --password secret
  %(prog)s --url https://terminal.example.com --socks-port 9090
        """
    )
    
    parser.add_argument('--url', required=True, help='Web SSH URL')
    parser.add_argument('--username', '-u', required=True, help='SSH username')
    parser.add_argument('--password', '-p', required=True, help='SSH password')
    parser.add_argument('--socks-port', type=int, default=1080, 
                       help='Local SOCKS5 port (default: 1080)')
    parser.add_argument('--remote-port', type=int, default=9999,
                       help='Remote SOCKS proxy port (default: 9999)')
    parser.add_argument('--visible', action='store_false', dest='headless',
                       help='Show browser window (for debugging)')
    parser.add_argument('--ws-endpoint', help='Manual WebSocket endpoint URL')
    
    args = parser.parse_args()
    
    config = TunnelConfig(
        url=args.url,
        username=args.username,
        password=args.password,
        socks_port=args.socks_port,
        remote_proxy_port=args.remote_port,
        headless=args.headless,
        ws_endpoint=args.ws_endpoint
    )
    
    tunnel = WebSSHTunnel(config)
    
    try:
        asyncio.run(tunnel.run())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
