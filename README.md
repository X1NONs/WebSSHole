# 🕳️ WebSSHole

> Turn any **web‑based SSH terminal** into a **V2Ray super‑tunnel**.

You’re stuck behind a firewall that only allows one domain: a web SSH client.  
With **WebSSHole**, you can:

- Establish a **full V2Ray (VMess) tunnel** through that single allowed entry point.
- Get a **local SOCKS5 proxy** (via V2Ray) that tunnels all your traffic.
- **Automatically deploy** the tunnel components on the remote server.
- **Zero configuration** – just answer a few questions.

---

## 🔥 How It Works

1. **Connects** to the web SSH as a normal user would.
2. **Downloads** a standalone V2Ray binary onto the remote machine.
3. **Starts** a V2Ray server (VMess) on `127.0.0.1:10000`.
4. **Injects** a tiny Python relay that bridges the remote V2Ray port to the existing terminal’s `stdin/stdout`.
5. **Takes over** the WebSocket and switches from terminal mode to tunnel mode.
6. **Spawns** a local TCP listener on your machine that connects to the WebSocket tunnel.
7. **Provides** a ready‑to‑use V2Ray client config for you.

The entire tunnel is wrapped in **base64‑encoded lines**, so it works with **any** web SSH backend (JSON‑based, raw‑text, even old‑school GateOne).

---

## ⚙️ Installation

### Requirements
- Python 3.7+
- pip

### Setup

git clone https://github.com/yourusername/WebSSHole.git
cd WebSSHole
pip install -r requirements.txt

---

## 🚀 Usage

Run the interactive wizard:

bash
python3 websshole.py

Answer the prompts:


🕳️  WebSSHole – V2Ray Super‑Tunnel Setup
═══════════════════════════════════════════

Web SSH URL (e.g. https://ssh.parspack.net/): https://ssh.parspack.net/
SSH Host: my-vps.example.com
SSH Port [22]: 22
SSH Username: root
SSH Password: ********

V2Ray settings (press Enter for defaults):
Local listen port for V2Ray [10808]: 10808
VMess UUID [auto-generated]: 

After a few seconds, you’ll see:


[+] WebSocket connected
[+] Shell ready
[*] Trying to deploy V2Ray...
[+] V2Ray binary uploaded
[*] Starting V2Ray server...
[+] V2AY_STARTED
[*] Injecting relay script...
[+] RELAY_RUNNING
[+] Switching WebSocket to tunnel mode...
[+] Local forwarder listening on 127.0.0.1:10808

---

## 🛸 Using the Tunnel

WebSSHole now acts as a transparent bridge.  
To use it, you need a **local V2Ray client** (the same binary you deployed remotely).  
Download V2Ray or Xray for your OS and run it with the following client config:

json
{
  "log": {"loglevel": "warning"},
  "inbounds": [{
"port": 1080,
"listen": "127.0.0.1",
"protocol": "socks",
"settings": {"udp": true}
  }],
  "outbounds": [{
"protocol": "vmess",
"settings": {
"vnext": [{
"address": "127.0.0.1",
"port": 10808,
"users": [{"id": "YOUR-UUID", "alterId": 0}]
}]
},
"streamSettings": {"network": "tcp"}
  }]
}

Replace `YOUR-UUID` with the UUID printed during the wizard (or the one you provided).

Then start your local V2Ray:

bash
./v2ray run -c client_config.json

Now you have a **SOCKS5 proxy** at `127.0.0.1:1080`.  
Point your browser, apps, or even use `redsocks` / `tun2socks` for full system VPN.

---

## 🧩 Fallback Mode

If the remote machine **cannot download V2Ray** (e.g., no `wget`/`curl` or `noexec` filesystem),  
WebSSHole automatically falls back to **SSH dynamic forwarding** (`ssh -D`) – a lightweight SOCKS5 proxy.  
(Coming in v1.1)

---

## 🧠 Advanced: Transparent System Proxy

Combine with:

- [**Tun2socks**](https://github.com/xjasonlyu/tun2socks) – create a virtual network interface that routes all traffic through the SOCKS proxy.
- [**redsocks**](https://github.com/darkk/redsocks) – redirect TCP connections to SOCKS transparently.

Example for Linux:

bash
sudo tun2socks -device tun0 -proxy socks5://127.0.0.1:1080
# then set routes / default gateway

---

## 📌 Supported Web SSH Servers

- **ssh.parspack.net**
- **Shellngn / GateOne**
- **ttyd** (with minor tweaks)
- **webssh2**
- Any service that provides a raw terminal in the browser.

---

## ⚠️ Disclaimer

This tool is intended for **legal penetration testing**, privacy protection, and network research.  
You are responsible for complying with all applicable laws and terms of service.

---

## 🌟 Star This Project

If this tool helps you escape restrictive networks, give it a ⭐ on GitHub!  
It’s the only way I know I’m not trapped alone in the tunnel.


---

**This is the complete, reborn project.**  
V2Ray‑first, automatic setup, base64‑tunnel proof, zero-config wizard.

Run `python3 websshole.py` and watch the magic happen.  
If any web SSH gets in the way, the base64 wrapper will slip through like liquid.
