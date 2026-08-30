Installation Guide -- Pi-hole -- TP-Link(Deco) -- Tailscale

- Hardware
    - Raspberry Pi 5 1GB
- OS
    - Raspberry Pi OS Lite (64-bit)

**Description:**
Network-wide ad blocking for every device on the local network, plus DNS filtering
on the tailnet from anywhere.

Need to find the things:

| Thing              | Command                         | Example                    |
| ------------------ | ------------------------------- | -------------------------- |
| Pi Hostname        | `hostnamectl hostname`          | pi-hole                    |
| Pi LAN address     | `ip -4 addr show eth0`          | `192.168.68.xx/22`         |
| Pi tailnet address | `tailscale ip -4`               | 100.x.                     |
| Unbound            | `sudo ss -lunp \| grep unbound` | `127.0.0.1#5335` on the Pi |
| DHCP pool          |                                 | `192.168.68.100` – end     |

## Prerequisites

Pi is flashed, booted, and reachable over SSH. System updated, plus the DNS tools that
Lite doesn't ship:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y dnsutils
```
`dnsutils` gets you `dig` and `nslookup`. You will want them at every verification step.

install vim:
```
sudo apt install vim
```

install docker:
```
curl -fsSL https://get.docker.com | sh
```
add user to docker group
```
sudo usermod -aG docker $USER
```
*note: need to logout nad back in*

Add this to `~/.ssh/config` on your workstation so you stop typing the IP:

```
Host pihole
    HostName 192.168.x.x
    User $USER
    IdentityFile ~/.ssh/pi-hole
    IdentitiesOnly yes
```

Then `ssh pihole`.

--- 
Note:
`IdentitiesOnly yes` matters when the key has a non-default name — SSH only auto-offers
`id_ed25519`/`id_rsa`, so a key called `pi-hole` is never tried unless you name it. That
produces `Permission denied (publickey)` even when everything else is correct.

> If you reflash the card, the Pi generates new host keys and SSH will refuse to connect
> with a host key mismatch. Clear the stale entry with `ssh-keygen -R 192.168.68.66` and
> reconnect. This is expected after a reflash, not a sign of anything wrong.


---

## Static IP

To keep a static IP set the start range (in the router for the DHCP pool) above the ip that was issued to the pi.

check:
```
nmcli device show eth0 | grep IP4.ADDRESS
nmcli -f ipv4.method con show "Wired connection 1"
```
if  method is auto, pin it:
*note: dns 1.1.1.1 until unbound set up*
```
sudo nmcli con mod "Wired connection 1" \
  ipv4.addresses 192.168.68.xx/22 \
  ipv4.gateway 192.168.68.1 \
  ipv4.dns 1.1.1.1 \
  ipv4.method manual
sudo nmcli con up "Wired connection 1"
```
### Keep the static address out of the DHCP pool

If you pin an address the router still considers leasable, it can hand that same address
to a new device months later. Two machines, one IP, and DNS dies for the whole house in
a way that's genuinely hard to diagnose.

Check the pool (Deco app → More → Advanced → DHCP Server) and either move its Start IP
above your static address, or pick a static address below the pool.

**Done here:** Start IP moved to `192.168.68.100`, reserving `.2`–`.99` as a static block
with the Pi at `.66`. Over 900 addresses remain in the pool.

> Shrinking the pool doesn't revoke existing leases. Devices holding an address in the
> reserved range keep it until their lease expires, then renew into the new range. You may
> see a device change address once over the following day. Harmless.


---
## Firewall
*Recommend using ethernet*

```
sudo apt install -y ufw
sudo ufw allow from 192.168.68.0/22 to any port 53
sudo ufw allow from 192.168.68.0/22 to any port 80 proto tcp
sudo ufw allow from 192.168.68.0/22 to any port 22 proto tcp
sudo ufw allow in on tailscale0
sudo ufw enable
```

sudo ufw status:
```
Anywhere on tailscale0     ALLOW       Anywhere
53                         ALLOW       192.168.68.0/22
80/tcp                     ALLOW       192.168.68.0/22
22/tcp                     ALLOW       192.168.68.0/22
```
*plus disable password authentication*


---

## Docker
compose.yaml
```
services:
  pihole:
    container_name: pihole
    image: "pihole/pihole:2026.07.2"
    network_mode: host
    environment:
      TZ: 'America/New_York'
      FTLCONF_webserver_api_password: ${PIHOLE_PASSWORD}
      FTLCONF_dns_listeningMode: 'all'
    volumes:
      - './etc-pihole:/etc/pihole'
    cap_add:
      - SYS_NICE
      # - NET_ADMIN     # uncomment only if Pi-hole runs DHCP (Stage 5, alt path)
    restart: unless-stopped
```

```
vim .env
```

```
PIHOLE_PASSWORD=your-new-password-here
```

```
docker compose up -d
```


---
## Unbound

install:
```
sudo apt install -y unbound
```

Check:
```
systemctl status unbound --no-pager
```
current port:
```
sudo ss -lnup | grep unbound
```

Check if config file exists:
```
ls -la /etc/unbound/unbound.conf.d/
```
if not create it:
```
sudo vim /etc/unbound/unbound.conf.d/pi-hole.conf
```

Minimal Content:
```
server:
    verbosity: 0
    interface: 127.0.0.1
    port: 5335
    do-ip4: yes
    do-udp: yes
    do-tcp: yes
    do-ip6: no
    prefer-ip6: no
    harden-glue: yes
    harden-dnssec-stripped: yes
    use-caps-for-id: no
    edns-buffer-size: 1232
    prefetch: yes
    num-threads: 1
    so-rcvbuf: 1m
    private-address: 192.168.0.0/16
    private-address: 169.254.0.0/16
    private-address: 172.16.0.0/12
    private-address: 10.0.0.0/8
    private-address: fd00::/8
    private-address: fe80::/10
```

check for errors
```
sudo unbound-checkconf
```
restart:
```
sudo systemctl restart unbound
```
check port again: need 5335, nothing on 53
```
sudo ss -lnup | grep unbound
```
 can test:
 ```
 dig google.com @127.0.0.1 -p 5335
 ```


---
## Tailscale DNS
Enable HTTPS 
	Admin console -> DNS -> scroll to HTTPS Certificates -> enable
- Enabled for entire tailnet

```
sudo tailscale serve --bg 80
```

verify:
```
tailscale serve status
```

- Add Global name server -> tailscale ip of pihole device
- Toggle on the DNS override


*Not recommended to run pi-hole inside the cluster. More beneficial to be stand-alone container*

---
## Set Upstream DNS Server - Unbound

pihole admin ui -> settings -> DNS -> custom DNS -> 127.0.0.1#5335

save & apply

Verify:
```
dig google.com @<pi-ip>
dig doubleclick.net @<pi-ip>
```
doubleclick.net should return 0.0.0.0

---
## Point the Pi at its Pi-hole
Run only after the container is healthy and holding port 53:
```
sudo ss -lnup | grep :53
```

Should show pihole-FTL. Then:
```
sudo nmcli con mod "Wired connection 1" ipv4.dns 127.0.0.1
sudo nmcli con up "Wired connection 1"
```

Verify:
```
cat /etc/resolv.conf
ping -c2 github.com
```
*want: nameserver 127.0.0.1*

*Note: Pi-hole must own port 53  before this is safe
Pointing at 127.0.0.1 when nothing is listening breaks name resolution
	apt, curl, and ping hostname all fail 
	"Temporary failure resolving."
	This is why static ip starts resolving over 1.1.1.1 and flips to 127.0.0.1 at the end*

---
## Point the Router at Pi-hole
*for TP-Link Deco*
Deco app → More → Advanced → DHCP Server → Primary DNS: `<pi-ip>`, Secondary DNS: 1.1.1.1

dashboard → Upstream servers panel should show `127.0.0.1#5335`, not `dns.google#53`

*Note: Reboot the Deco to force lease renewal. Secondary is just in case. If the Pi dies the house degrades to unfiltered browsing rather than no internet.*

```
resolvectl status | grep "DNS Server"
dig doubleclick.net
```
doubleclick.net should return 0.0.0.0

---

**To disable pi-hole**

 Delete Tailscale DNS nameserver pi-hhole

TP-Link Deco
More -> Advanced -> DHCP Server -> Primary DNS: 1.1.1.1
*resolves DNS directly throughh the router again*
