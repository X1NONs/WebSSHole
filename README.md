# 🕳️ WebSSHole – Tunnel Out Through a Web Terminal

<p align="center">
  <img src="assets/demo.gif" alt="Browsing free internet from a locked-down network" width="700"/>
</p>

**You're behind a corporate firewall that blocks *everything* except one single domain: a web-based SSH terminal.  
No HTTP, no NPM, no PyPI. How do you work?**  

`webssh-hole` turns that solitary allowed page into a full SOCKS5 proxy — without installing anything on the target server, opening extra ports, or tripping network alarms. Your traffic simply looks like someone typing commands in a browser window.

---

## 🔥 One Command to Freedom

pip install webssh-hole
webssh-hole --url https://ssh.corp.lockdown.com --proxy 1080

Set your browser’s SOCKS proxy to `127.0.0.1:1080` and suddenly... you’re browsing the outside world.

---

## 🤔 How It Works (In Plain English)

1. **Automated Login**: The tool opens a headless browser, navigates to your web SSH page, and logs in securely (env vars or prompt).
2. **In-Session Tunnel Creation**: On the remote machine, it runs `ssh -D 9999 localhost` (or `chisel`, or `socat`), opening a SOCKS endpoint inside the server.
3. **WebSocket Bridge**: It hooks into the web terminal’s existing WebSocket — the same one that carries your keystrokes — and multiplexes an additional data channel for proxy traffic.
4. **Local SOCKS Server**: A local listener receives your browser’s requests, encodes them as special payloads through the WebSocket, and the remote server’s SOCKS endpoint forwards them to the internet.

Net result: you and your apps use `localhost:1080` as proxy, and all traffic rides the one allowed HTTPS connection, invisible to network monitors.

---

## 🚀 Quick Try (45 Seconds)

We’ve included a fully sandboxed example with Docker — no real corporate firewall needed.

bash
git clone https://github.com/you/webssh-hole.git
cd webssh-hole
docker compose up -d

This spins up a mock firewall that only allows `http://ssh.local`, and a web SSH client (Guacamole/Wetty). Then:

bash
# Install webssh-hole locally
pip install -e .
webssh-hole --url http://ssh.local --username test --password test --proxy 1080

Browse to any blocked site via `socks5://127.0.0.1:1080`. 🎉

---

## 🧱 Supported Web SSH Clients

| Platform        | Status      |
| --------------- | ----------- |
| Apache Guacamole | ✅ Full     |
| Wetty           | ✅ Full     |
| GateOne         | 🚧 Beta     |
| Shellinabox     | 🧪 Alpha    |
| Custom WebSocket| 🧩 Extensible via `--ws-type` |

If your client isn’t listed, open an issue — we love weird edge cases.

---

## ⚠️ When to Use This (and When Not To)

✅ **Good use**:  
- You’re a developer, pentester, or researcher in a restrictive environment.  
- You have legitimate access to the web SSH and need to clone repos, fetch packages, or use APIs.  
- You’re demonstrating a security concept with explicit permission.

❌ **Bad use**:  
- Bypassing firewalls without authorization — that’s unethical and likely illegal.  
- Doing anything your mother would shake her head at.

---

## 🤝 Contributing

Pull requests are pure joy. If you’d like to add support for a new web SSH client, see our [CONTRIBUTING.md](CONTRIBUTING.md) for the WebSocket protocol mapping.  

## 📜 License

MIT — because freedom should be copyable.

---

<p align="center">
  <strong>Built by devs, for devs, against walls.</strong><br>
  <sub>Star us if you’ve ever had to code in a box.</sub>
</p>


---

## 💡 Extra README Ingredients for Virality

- **Demo GIF**: I'll storyboard it for you: Start with a browser trying to reach `github.com` → Connection refused → Then run one terminal command → Same browser now loads GitHub as usual, with a little "Proxied via WebSSHole" badge in the corner.
- **Star History Graph**: Insert a dynamic star history using [star-history.com](https://star-history.com) once the repo gets going.
- **Badges**: CI (tests passing), PyPI version, license, stars, downloads.

---

## 🗣️ The "Elevator Pitch" for Social Media

When you share on Twitter/Reddit/HN, lead with this:

> “My company’s firewall only allows one website: a web-based SSH terminal.  
> I wrote a tool that turns that single page into a full internet tunnel.  
> One command, no VPN, no open ports. Meet WebSSHole 🕳️”

---

Alright, that’s the blueprint. Do you like **WebSSHole** or want another name? We can adjust the README in seconds. Then we'll jump into the script architecture together. Shoot me your thoughts!
