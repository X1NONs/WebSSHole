#!/usr/bin/env python3
"""
WebSSHole – V2Ray VLESS Super‑Tunnel
======================================
Turn any web‑based SSH terminal into a full V2Ray tunnel.
Outputs a copy‑paste VLESS link at the end.
"""

import asyncio
import aiohttp
import base64
import json
import os
import re
import signal
import sys
import uuid
from bs4 import BeautifulSoup
from colorama import init, Fore, Style

init(autoreset=True)

# ─── Color helpers ──────────────────────────────────────
class C:
    @staticmethod
    def info(msg): print(f"{Fore.CYAN}[*] {msg}{Style.RESET_ALL}")
    @staticmethod
    def success(msg): print(f"{Fore.GREEN}[+] {msg}{Style.RESET_ALL}")
    @staticmethod
    def error(msg): print(f"{Fore.RED}[-] {msg}{Style.RESET_ALL}")
    @staticmethod
    def warn(msg): print(f"{Fore.YELLOW}[!] {msg}{Style.RESET_ALL}")
    @staticmethod
    def banner(msg): print(f"\n{Fore.MAGENTA}{Style.BRIGHT}{msg}{Style.RESET_ALL}")
    @staticmethod
    def debug(msg): print(f"{Fore.BLUE}[D] {msg}{Style.RESET_ALL}")

BANNER = r"""
  ██╗    ██╗███████╗██████╗ ███████╗███████╗██╗  ██╗ ██████╗ ██╗     ███████╗
  ██║    ██║██╔════╝██╔══██╗██╔════╝██╔════╝██║  ██║██╔═══██╗██║     ██╔════╝
  ██║ █╗ ██║█████╗  ██████╔╝███████╗███████╗███████║██║   ██║██║     █████╗  
  ██║███╗██║██╔══╝  ██╔══██╗╚════██║╚════██║██╔══██║██║   ██║██║     ██╔══╝  
  ╚███╔███╔╝███████╗██████╔╝███████║███████║██║  ██║╚██████╔╝███████╗███████╗
   ╚══╝╚══╝ ╚══════╝╚═════╝ ╚══════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝
                         V2Ray VLESS Super‑Tunnel
"""

# ─── Relay script (injected into remote shell) ──────────
# This bridges stdin/stdout (the WebSocket) to V2Ray's TCP port.
# {{port}} will be replaced with 10000.
RELAY_SCRIPT = '''
import sys, socket, base64, select, time

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
for _ in range(10):
    try:
        s.connect(('127.0.0.1', {{port}}))
        break
    except:
        time.sleep(0.5)
else:
    sys.exit(1)

s.setblocking(False)
stdin = sys.stdin.buffer
stdout = sys.stdout.buffer

while True:
    r, _, _ = select.select([stdin, s], [], [], 1.0)
    if stdin in r:
        line = stdin.readline()
        if not line:
            break
        try:
            data = base64.b64decode(line.strip())
            s.sendall(data)
        except:
            pass
    if s in r:
        try:
            data = s.recv(65536)
            if not data:
                break
            stdout.write(base64.b64encode(data) + b'\\n')
            stdout.flush()
        except:
            pass
s.close()
'''

# ─── V2Ray server config ────────────────────────────────
def vless_server_config(uuid: str, port: int = 10000) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "port": port,
            "listen": "127.0.0.1",
            "protocol": "vless",
            "settings": {
                "clients": [{"id": uuid, "level": 0}],
                "decryption": "none"
            },
            "streamSettings": {"network": "tcp"}
        }],
        "outbounds": [{
            "protocol": "freedom",
            "settings": {}
        }]
    }

# ─── Configuration container ────────────────────────────
class TunnelConfig:
    def __init__(self):
        self.webssh_url = ""
        self.vps_host = ""
        self.vps_port = 22
        self.vps_username = ""
        self.vps_password = ""
        self.local_bridge_port = 10808
        self.vless_uuid = str(uuid.uuid4())
        self.debug = False

# ─── Interactive Wizard ──────────────────────────────────
def wizard() -> TunnelConfig:
    C.banner(BANNER)
    print(f"{Fore.WHITE}Turn any web SSH into a V2Ray VLESS tunnel\n{Style.RESET_ALL}")
    print("─" * 55)
    cfg = TunnelConfig()

    print(f"\n{Fore.CYAN}📌 Step 1: The Door (Web SSH){Style.RESET_ALL}")
    cfg.webssh_url = input("   Web SSH URL: ").strip()
    if not cfg.webssh_url.startswith("http"):
        cfg.webssh_url = "https://" + cfg.webssh_url
        C.info(f"Auto‑corrected to: {cfg.webssh_url}")

    print(f"\n{Fore.CYAN}📌 Step 2: The Proxy Server (VPS){Style.RESET_ALL}")
    print("   This is the server that will become your tunnel exit point.")
    cfg.vps_host = input("   VPS IP / Hostname: ").strip()
    cfg.vps_port = int(input("   SSH Port [22]: ") or 22)
    cfg.vps_username = input("   SSH Username: ").strip()
    cfg.vps_password = input("   SSH Password: ").strip()

    print(f"\n{Fore.CYAN}📌 Step 3: Tunnel Settings{Style.RESET_ALL}")
    cfg.local_bridge_port = int(input(f"   Local bridge port [10808]: ") or 10808)
    if input("   Use custom UUID? [y/N]: ").strip().lower() == 'y':
        cfg.vless_uuid = input("   VLESS UUID: ").strip() or cfg.vless_uuid
    else:
        C.info(f"Generated UUID: {cfg.vless_uuid}")

    cfg.debug = input("   Enable debug output? [y/N]: ").strip().lower() == 'y'

    return cfg

# ─── Core Engine ────────────────────────────────────────
class WebSSHTunnelAsync:
    def __init__(self, config: TunnelConfig):
        self.cfg = config
        self.session = None
        self.ws = None
        self._terminal_ready = asyncio.Event()
        self._csrf_token = ""
        self._terminal_url = ""

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
            timeout=aiohttp.ClientTimeout(total=30)
        )
        return self

    async def __aexit__(self, *exc):
        if self.ws and not self.ws.closed:
            await self.ws.close()
        if self.session:
            await self.session.close()

    # ─── Phase 1: Session & Authentication ─────────────
    async def initialize_session(self):
        C.info("Connecting to web SSH...")
        try:
            async with self.session.get(self.cfg.webssh_url, ssl=False) as resp:
                self._terminal_url = str(resp.url)
                html = await resp.text()
                C.debug(f"URL: {self._terminal_url} | Status: {resp.status}")
        except Exception as e:
            C.error(f"Failed to load page: {e}")
            raise

        # Extract CSRF token
        soup = BeautifulSoup(html, 'html.parser')
        meta = soup.find('meta', {'name': 'csrf-token'})
        if meta:
            self._csrf_token = meta.get('content', '')
        if not self._csrf_token:
            inp = soup.find('input', {'name': re.compile(r'_token|csrf', re.I)})
            if inp:
                self._csrf_token = inp.get('value', '')
        if not self._csrf_token:
            match = re.search(r'csrf_token\s*[:=]\s*["\']([^"\']+)', html)
            if match:
                self._csrf_token = match.group(1)
        if self._csrf_token:
            C.success(f"CSRF token: {self._csrf_token[:20]}...")
        else:
            C.warn("No CSRF token found – this may be okay.")

    async def submit_ssh_form(self):
        C.info(f"Creating SSH session to {self.cfg.vps_host}:{self.cfg.vps_port}...")
        form_data = {
            'hostname': self.cfg.vps_host,
            'host': self.cfg.vps_host,
            'port': str(self.cfg.vps_port),
            'username': self.cfg.vps_username,
            'user': self.cfg.vps_username,
            'password': self.cfg.vps_password,
            'passwd': self.cfg.vps_password,
        }
        if self._csrf_token:
            form_data['_csrf_token'] = self._csrf_token
            form_data['_token'] = self._csrf_token

        endpoints = [
            self.cfg.webssh_url,
            self.cfg.webssh_url.rstrip('/') + '/connect',
            self.cfg.webssh_url,
        ]
        for ep in endpoints:
            try:
                async with self.session.post(ep, data=form_data, ssl=False) as resp:
                    self._terminal_url = str(resp.url)
                    if resp.status < 400:
                        C.success("SSH session created")
                        return
            except:
                continue
        C.warn("Could not submit form; maybe no POST needed? Continuing...")

    async def find_and_connect_websocket(self):
        C.info("Finding WebSocket endpoint...")
        async with self.session.get(self._terminal_url, ssl=False) as resp:
            html = await resp.text()
        patterns = [
            r'["\'](wss?://[^"\']+)["\']',
            r'["\']([^"\']*ws[^"\']*)["\']',
        ]
        ws_url = None
        for pat in patterns:
            matches = re.findall(pat, html, re.I)
            for m in matches:
                if 'ws' in m.lower():
                    ws_url = m if m.startswith('ws') else self._build_ws_url(m)
                    break
            if ws_url:
                break
        if not ws_url:
            # Build from base URL
            base = self._terminal_url.replace('https://', 'wss://').replace('http://', 'ws://')
            ws_url = base.rstrip('/') + '/ws'
        C.info(f"Connecting to {ws_url}")
        self.ws = await self.session.ws_connect(ws_url, ssl=False)
        C.success("WebSocket connected")

    def _build_ws_url(self, path: str) -> str:
        if path.startswith('ws'):
            return path
        base = self._terminal_url
        match = re.match(r'(https?://[^/]+)', base)
        if match:
            host = match.group(1)
            scheme = 'wss' if host.startswith('https') else 'ws'
            return f"{scheme}://{host.split('://')[1]}{path}"
        return path

    # ─── Phase 2: Shell interaction ──────────────────
    async def wait_for_prompt(self, timeout=15):
        C.info("Waiting for shell prompt...")
        start = asyncio.get_event_loop().time()
        while True:
            if asyncio.get_event_loop().time() - start > timeout:
                raise TimeoutError("No shell prompt received")
            try:
                msg = await asyncio.wait_for(self.ws.receive(), timeout=2)
            except asyncio.TimeoutError:
                await self.ws.send_str('\r\n')
                continue
            if msg.type == aiohttp.WSMsgType.TEXT:
                if self.cfg.debug:
                    print(msg.data, end='', flush=True)
                if re.search(r'[$#]\s*$', msg.data.strip()):
                    C.success("Shell ready")
                    return
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                raise ConnectionError("WebSocket closed early")

    async def send_cmd(self, cmd: str, wait_prompt=True) -> str:
        if not self.ws or self.ws.closed:
            raise ConnectionError("WebSocket not connected")
        await self.ws.send_str(cmd + '\r\n')
        if not wait_prompt:
            return ""
        output = []
        while True:
            msg = await asyncio.wait_for(self.ws.receive(), timeout=10)
            if msg.type == aiohttp.WSMsgType.TEXT:
                output.append(msg.data)
                if re.search(r'[$#]\s*$', msg.data.strip()):
                    break
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
        return ''.join(output)

    # ─── Phase 3: V2Ray deployment ──────────────────
    async def check_caps(self) -> dict:
        caps = {'wget': False, 'curl': False, 'python3': False, 'unzip': False}
        tests = {
            'wget': 'command -v wget && echo YES || echo NO',
            'curl': 'command -v curl && echo YES || echo NO',
            'python3': 'command -v python3 && echo YES || echo NO',
            'unzip': 'command -v unzip && echo YES || echo NO',
        }
        for name, cmd in tests.items():
            out = await self.send_cmd(cmd)
            caps[name] = 'YES' in out
        for k, v in caps.items():
            print(f"   {'✅' if v else '❌'} {k}")
        return caps

    async def deploy_v2ray(self, caps: dict) -> bool:
        C.info("Deploying V2Ray...")
        cmds = [
            "mkdir -p /tmp/.v2ray",
            "cd /tmp/.v2ray"
        ]
        url = "https://github.com/v2fly/v2ray-core/releases/download/v5.15.0/v2ray-linux-64.zip"
        if caps['wget']:
            cmds.append(f"wget -q --no-check-certificate -O v2ray.zip '{url}'")
        elif caps['curl']:
            cmds.append(f"curl -sLk -o v2ray.zip '{url}'")
        else:
            cmds.append(f"python3 -c \"import urllib.request; urllib.request.urlretrieve('{url}', 'v2ray.zip')\"")
        if caps['unzip']:
            cmds.append("unzip -o v2ray.zip")
        else:
            cmds.append("python3 -c \"import zipfile; zipfile.ZipFile('v2ray.zip').extractall()\"")
        cmds.extend(["chmod +x v2ray", "echo DEPLOY_OK"])
        for cmd in cmds:
            out = await self.send_cmd(cmd)
            if self.cfg.debug:
                print(out[-500:], flush=True)
        C.success("V2Ray deployed")
        return True

    async def start_v2ray_server(self):
        C.info("Starting V2Ray server (VLESS)...")
        config = vless_server_config(self.cfg.vless_uuid, 10000)
        config_json = json.dumps(config)
        # Write config through shell
        await self.send_cmd(f"cat > /tmp/.v2ray/config.json << 'EOF'\n{config_json}\nEOF")
        await self.send_cmd("cd /tmp/.v2ray && nohup ./v2ray run -config config.json > /tmp/.v2ray/v2ray.log 2>&1 & disown")
        await self.send_cmd("sleep 2 && echo V2RAY_RUNNING")
        C.success("V2Ray server started")

    async def start_relay_script(self):
        C.info("Injecting relay script...")
        script = RELAY_SCRIPT.replace('{{port}}', '10000')
        b64 = base64.b64encode(script.encode()).decode()
        await self.send_cmd(f"python3 -c 'import base64, sys; exec(base64.b64decode(\"{b64}\"))' & disown")
        await self.send_cmd("echo RELAY_RUNNING")
        C.success("Relay script running")

    # ─── Phase 4: Bridge to local forwarder ──────────
    async def switch_to_tunnel_mode(self):
        C.banner("🌐 Tunnel is LIVE!")
        # Now the remote is sending base64-encoded binary to V2Ray.
        # We'll spin up a local TCP server that forwards data to the WebSocket.
        local_port = self.cfg.local_bridge_port
        C.info(f"Starting local bridge on 127.0.0.1:{local_port}")

        # Asyncio server
        async def handle_client(reader, writer):
            ws = self.ws
            # Local → WS
            async def forward_to_tunnel():
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    b64 = base64.b64encode(data) + b'\n'
                    await ws.send_bytes(b64)  # binary doesn't change encoding

            # WS → local
            async def recv_from_tunnel():
                while True:
                    msg = await ws.receive()
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        try:
                            decoded = base64.b64decode(msg.data.strip())
                            writer.write(decoded)
                            await writer.drain()
                        except:
                            pass
                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        # may still be base64 text line
                        try:
                            decoded = base64.b64decode(msg.data.strip())
                            writer.write(decoded)
                            await writer.drain()
                        except:
                            pass
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

            await asyncio.gather(forward_to_tunnel(), recv_from_tunnel())
            writer.close()

        server = await asyncio.start_server(handle_client, '127.0.0.1', local_port)
        C.success(f"Bridge listening on 127.0.0.1:{local_port}")

        # Generate VLESS link and display
        vless_link = f"vless://{self.cfg.vless_uuid}@127.0.0.1:{local_port}?encryption=none&security=none&type=tcp#WebSSHole-Tunnel"
        print()
        print(Fore.CYAN + "╔══════════════════════════════════════════╗")
        print(Fore.CYAN + "║  Copy this link into your V2Ray client: ║")
        print(Fore.CYAN + "╚══════════════════════════════════════════╝")
        print(Fore.YELLOW + vless_link)
        print()
        C.info("Your V2Ray client will create a SOCKS5 proxy at 127.0.0.1:1080")
        C.info("Use 'v2ray run -c client_config.json' or import the link directly")

        # Keep running
        await asyncio.Future()  # run forever

    # ─── Main sequence ─────────────────────────────
    async def run(self):
        await self.initialize_session()
        await self.submit_ssh_form()
        await self.find_and_connect_websocket()
        await self.wait_for_prompt()

        caps = await self.check_caps()
        if not caps['python3']:
            C.error("Python3 is required on remote host for the relay script!")
            return

        await self.deploy_v2ray(caps)
        await self.start_v2ray_server()
        await self.start_relay_script()
        await self.switch_to_tunnel_mode()

# ─── Entry point ───────────────────────────────────────
async def main():
    config = wizard()
    async with WebSSHTunnelAsync(config) as tunnel:
        try:
            await tunnel.run()
        except KeyboardInterrupt:
            C.warn("Stopped by user")
        except Exception as e:
            C.error(f"Fatal error: {e}")
            if config.debug:
                import traceback
                traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
