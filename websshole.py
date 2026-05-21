#!/usr/bin/env python3
"""
WebSSHole – V2Ray Super‑Tunnel
Through any web‑based SSH terminal.
"""

import asyncio
import aiohttp
import base64
import json
import os
import re
import signal
import socket
import sys
import textwrap
import uuid
from bs4 import BeautifulSoup
from colorama import init, Fore, Style

init(autoreset=True)

# ─── Color Print Helper ──────────────────────────────────────
class ColorPrint:
    @staticmethod
    def info(msg): print(f"{Fore.CYAN}[*] {msg}{Style.RESET_ALL}")
    @staticmethod
    def success(msg): print(f"{Fore.GREEN}[+] {msg}{Style.RESET_ALL}")
    @staticmethod
    def error(msg): print(f"{Fore.RED}[-] {msg}{Style.RESET_ALL}")
    @staticmethod
    def warn(msg): print(f"{Fore.YELLOW}[!] {msg}{Style.RESET_ALL}")
    @staticmethod
    def banner(msg): print(f"{Fore.MAGENTA}{Style.BRIGHT}{msg}{Style.RESET_ALL}")

# ─── Remote relay script (will be injected) ──────────────────
REMOTE_RELAY_SCRIPT = r'''
import sys, socket, base64, select
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(('127.0.0.1', PORT))
s.setblocking(0)
rbuf = b''
while True:
    r, _, _ = select.select([sys.stdin.buffer, s], [], [])
    if sys.stdin.buffer in r:
        line = sys.stdin.buffer.readline()
        if not line: break
        try:
            data = base64.b64decode(line.strip())
            s.sendall(data)
        except: pass
    if s in r:
        try:
            chunk = s.recv(4096)
            if not chunk: break
            sys.stdout.buffer.write(base64.b64encode(chunk) + b'\n')
            sys.stdout.buffer.flush()
        except: pass
s.close()
'''

# ─── Default config ──────────────────────────────────────────
class TunnelConfig:
    def __init__(self):
        self.webssh_url = ''
        self.host = ''
        self.port = 22
        self.username = ''
        self.password = ''
        self.local_v2ray_port = 10808
        self.v2ray_uuid = str(uuid.uuid4())

# ─── Interactive wizard ─────────────────────────────────────
def wizard():
    ColorPrint.banner("🕳️  WebSSHole – V2Ray Super‑Tunnel Setup")
    print("═══════════════════════════════════════════\n")
    cfg = TunnelConfig()
    cfg.webssh_url = input("Web SSH URL (e.g. https://ssh.parspack.net/): ").strip()
    cfg.host = input("SSH Host / IP : ").strip()
    cfg.port = int(input("SSH Port [22]: ") or "22")
    cfg.username = input("SSH Username: ").strip()
    cfg.password = input("SSH Password: ").strip()
    # V2Ray options
    print("\nV2Ray settings (press Enter for defaults):")
    cfg.local_v2ray_port = int(input(f"Local listen port for V2Ray [{cfg.local_v2ray_port}]: ") or cfg.local_v2ray_port)
    cfg.v2ray_uuid = input(f"VMess UUID [{cfg.v2ray_uuid}]: ") or cfg.v2ray_uuid
    return cfg

# ─── Core Engin ────────────────────────────────────────
class WebSSHTunnel:
    def __init__(self, config):
        self.config = config
        self.session = aiohttp.ClientSession()
        self.ws = None
        self.terminal_ready = asyncio.Event()

    async def initialize_session(self):
        """Fetch CSRF token & cookies"""
        ColorPrint.info("Initializing session...")
        async with self.session.get(self.config.webssh_url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to load page: {resp.status}")
            self.html = await resp.text()
            soup = BeautifulSoup(self.html, 'html.parser')
            token = soup.find('meta', {'name': 'csrf-token'})
            if token:
                self.csrf = token.get('content', '')
            else:
                # fallback: try hidden input
                inp = soup.find('input', {'name': '_token'})
                self.csrf = inp.get('value', '') if inp else ''
            print(f"CSRF token: {self.csrf[:20]}...")

    async def create_ssh_session(self):
        """Spawn a new SSH session on the web terminal"""
        ColorPrint.info("Creating SSH session...")
        form_data = {
            'hostname': self.config.host,
            'port': str(self.config.port),
            'username': self.config.username,
            'password': self.config.password,
        }
        # Add CSRF if required
        if self.csrf:
            form_data['_token'] = self.csrf

        async with self.session.post(self.config.webssh_url, data=form_data) as resp:
            self.url_terminals = str(resp.url)
            print(f"Terminal URL: {self.url_terminals}")
            # Some services redirect to a unique URL with the terminal ID

    async def connect_websocket(self):
        """Connect to the terminal's WebSocket"""
        # The WebSocket URL is usually derived from the terminal page
        ws_url = self.url_terminals.replace('http', 'ws')
        if not ws_url.endswith('/ws'):
            ws_url = ws_url.rstrip('/') + '/ws'
        ColorPrint.info(f"Connecting WebSocket: {ws_url}")
        self.ws = await self.session.ws_connect(ws_url)
        ColorPrint.success("WebSocket connected")

    async def send_command(self, cmd):
        """Send a command to the remote shell"""
        if not self.ws:
            return
        # Most terminals accept raw text frames
        await self.ws.send_str(cmd + '\n')

    async def wait_for_prompt(self, timeout=10):
        """Wait until we see a typical shell prompt"""
        self.terminal_ready.clear()
        start = asyncio.get_event_loop().time()
        buffer = ''
        while True:
            try:
                msg = await asyncio.wait_for(self.ws.receive(), timeout=1)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    buffer += msg.data
                    if re.search(r'[$#]\s*$', buffer.split('\n')[-1]):
                        self.terminal_ready.set()
                        return
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    raise Exception("WebSocket closed")
            except asyncio.TimeoutError:
                if asyncio.get_event_loop().time() - start > timeout:
                    raise TimeoutError("No shell prompt received")
                continue

    async def terminal_reader(self):
        """Constantly read terminal output to detect readiness"""
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                print(msg.data, end='', flush=True)   # show terminal to user
                if re.search(r'[$#]\s*$', msg.data):
                    self.terminal_ready.set()

    async def run_initial_commands(self):
        """Wait for prompt, then perform basic host checks"""
        await self.wait_for_prompt()
        ColorPrint.info("Shell ready")

    async def deploy_v2ray(self):
        """Download V2Ray and start it on the remote host"""
        ColorPrint.info("Trying to deploy V2Ray...")
        # Commands to download V2Ray (static binary) to /tmp
        cmds = [
            "mkdir -p /tmp/.v2ray",
            "cd /tmp/.v2ray",
            "if command -v wget >/dev/null; then wget -q https://github.com/v2fly/v2ray-core/releases/download/v5.10.0/v2ray-linux-64.zip; elif command -v curl >/dev/null; then curl -sLo v2ray-linux-64.zip https://github.com/v2fly/v2ray-core/releases/download/v5.10.0/v2ray-linux-64.zip; else echo 'NO_DOWNLOAD_TOOL'; fi",
            "if [ -f v2ray-linux-64.zip ]; then unzip -o v2ray-linux-64.zip && chmod +x v2ray v2ctl && echo 'V2RAY_OK'; else echo 'V2RAY_FAIL'; fi"
        ]
        for cmd in cmds:
            await self.send_command(cmd)
            await asyncio.sleep(2)   # let it run

        # Over time, we should parse output for V2RAY_OK / V2RAY_FAIL.
        # We'll check later (simplified here).
        return True  # assume OK for now

    async def start_v2ray_server(self):
        """Create V2Ray config and launch"""
        ColorPrint.info("Starting V2Ray server...")
        config = {
            "log": {"loglevel": "warning"},
            "inbounds": [{
                "port": 10000,
                "listen": "127.0.0.1",
                "protocol": "vmess",
                "settings": {
                    "clients": [{
                        "id": self.config.v2ray_uuid,
                        "alterId": 0
                    }]
                },
                "streamSettings": {
                    "network": "tcp"
                }
            }],
            "outbounds": [{
                "protocol": "freedom",
                "settings": {}
            }]
        }
        config_json = json.dumps(config)
        # Write config to remote
        write_cmd = f"cat > /tmp/.v2ray/config.json << 'EOF'\n{config_json}\nEOF"
        await self.send_command(write_cmd)
        await asyncio.sleep(1)
        # Start V2Ray in background
        await self.send_command("cd /tmp/.v2ray && nohup ./v2ray run -config config.json >/dev/null 2>&1 & disown")
        await self.send_command("echo 'V2RAY_STARTED'")
        await asyncio.sleep(2)

    async def start_relay_script(self):
        """Inject the base64 relay script to pipe stdin/stdout to V2Ray port"""
        ColorPrint.info("Injecting relay script...")
        script = REMOTE_RELAY_SCRIPT.replace('PORT', '10000')
        # We'll write it as a one-liner Python script command
        one_liner = base64.b64encode(script.encode()).decode()
        await self.send_command(f"python3 -c 'import base64, sys; exec(base64.b64decode(\"{one_liner}\"))' & disown")
        await self.send_command("echo 'RELAY_RUNNING'")
        await asyncio.sleep(1)

    async def switch_to_tunnel_mode(self):
        """Stop treating received data as terminal, start raw relay to local V2Ray client"""
        ColorPrint.success("Switching WebSocket to tunnel mode...")
        # Now the remote side expects base64-encoded binary over stdin/stdout.
        # We'll create a local TCP server that our local V2Ray client can connect to.
        # That local server will forward to the WebSocket (encode as base64 lines).
        local_port = self.config.local_v2ray_port
        ColorPrint.info(f"Starting local forwarder on 127.0.0.1:{local_port} ...")
        await self.start_local_forwarder(local_port)

        # Now keep the WebSocket alive, forwarding data
        # We need a reader that gets binary frames from WS, decodes, sends to local socket.
        # The writer takes data from local socket, encodes, sends to WS.
        # We'll implement a simple asyncio stream relay.
        # For simplicity, we'll just enter a loop that reads WS and forwards.
        forward_task = asyncio.create_task(self._ws_to_local(local_port))
        await forward_task  # run forever

    async def start_local_forwarder(self, local_port):
        """Start an asyncio TCP server that forwards connections to the WebSocket tunnel"""
        # We'll use a class to handle each client
        self.forwarder_server = await asyncio.start_server(
            self.handle_local_client, '127.0.0.1', local_port
        )
        ColorPrint.success(f"Local forwarder listening on 127.0.0.1:{local_port}")

    async def handle_local_client(self, reader, writer):
        """For each local V2Ray client connection, relay to WebSocket"""
        ws = self.ws
        # Read from local client and send to WS (encode base64 line)
        async def forward_to_ws():
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                b64 = base64.b64encode(data) + b'\n'
                await ws.send_bytes(b64)   # send as binary? We'll use text for safety
        # Read from WS and write to local client
        async def recv_from_ws():
            while True:
                msg = await ws.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        decoded = base64.b64decode(msg.data.strip())
                        writer.write(decoded)
                        await writer.drain()
                    except Exception:
                        pass
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    break
        # Run both directions
        tasks = [forward_to_ws(), recv_from_ws()]
        await asyncio.gather(*tasks)

    async def run(self):
        """Full sequence"""
        await self.initialize_session()
        await self.create_ssh_session()
        await self.connect_websocket()
        await self.run_initial_commands()

        # Try V2Ray deployment
        v2ray_ok = await self.deploy_v2ray()
        if not v2ray_ok:
            ColorPrint.warn("V2Ray deployment failed. Falling back to SSH -D (TODO)")
            # TODO: implement fallback (ssh -D) relay
            return

        await self.start_v2ray_server()
        # Ensure V2Ray is actually running (check with pid)

        await self.start_relay_script()
        # Now the remote shell is the relay; we can disconnect terminal mode
        await self.switch_to_tunnel_mode()

    async def shutdown(self):
        await self.session.close()

# ─── Main ─────────────────────────────────────────────────────
async def main():
    cfg = wizard()
    tunnel = WebSSHTunnel(cfg)
    try:
        await tunnel.run()
    except KeyboardInterrupt:
        ColorPrint.warn("Interrupted")
    except Exception as e:
        ColorPrint.error(f"Fatal: {e}")
    finally:
        await tunnel.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
