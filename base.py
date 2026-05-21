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
                self.session_id = result.get('sessi_id') or result.get('id')
                
                if not self.ws_url:
                    # Try to construct WebSocket URL from patterns
                    parsed = urllib.parse.urlparse(self.config.url)
                    ws_scheme = 'wss://' if parsed.scheme == 'https' else 'ws://'
                    self.ws_url = f"{ws_scheme}{parsed.netloc}/ws"
                
                logger.info(f"WebSocket URL: {self.ws_url}")
                logger.info(f"Session ID: {self.session_id}")
                return True
                
            except Exception
