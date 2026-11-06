#!/usr/bin/env bash
# Run once on a fresh Ubuntu 24.04 VPS, as root (bash provision.sh) or, on clouds that log you in
# as a default user such as Oracle's "ubuntu", with sudo (sudo bash provision.sh).
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"

[ "$(id -u)" -eq 0 ] || { echo "Run as root, or with sudo." >&2; exit 1; }
# The key you logged in with. Under sudo it's the default user's: on some clouds root's file only
# holds a "please log in as ubuntu" message, and copying it would lock the deploy user out.
KEYS=/root/.ssh/authorized_keys
[ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ] && KEYS="$(getent passwd "$SUDO_USER" | cut -d: -f6)/.ssh/authorized_keys"
[ -s "$KEYS" ] || { echo "No SSH key in $KEYS. Add your public key first." >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ufw fail2ban unattended-upgrades docker.io docker-compose-v2 git cron

echo "== deploy user"
id "$DEPLOY_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$DEPLOY_USER"
usermod -aG sudo,docker "$DEPLOY_USER"
echo "$DEPLOY_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$DEPLOY_USER"
chmod 440 "/etc/sudoers.d/90-$DEPLOY_USER"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$KEYS" "/home/$DEPLOY_USER/.ssh/authorized_keys"

echo "== ssh: keys only, no root"
cat > /etc/ssh/sshd_config.d/99-hardening.conf <<CONF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
AllowUsers $DEPLOY_USER
CONF
sshd -t
systemctl restart ssh

echo "== firewall: 22, 80, 443 only"
# Oracle's Ubuntu images ship their own iptables rules (netfilter-persistent) that reject web traffic.
# ufw replaces them, so they're removed to keep one firewall.
if systemctl list-unit-files netfilter-persistent.service >/dev/null 2>&1; then
  systemctl disable --now netfilter-persistent || true
  rm -f /etc/iptables/rules.v4 /etc/iptables/rules.v6
  iptables -F INPUT && iptables -P INPUT ACCEPT
  iptables -F FORWARD && iptables -P FORWARD ACCEPT
fi
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "== fail2ban"
cat > /etc/fail2ban/jail.d/sshd.local <<CONF
[sshd]
enabled = true
backend = systemd
maxretry = 5
bantime = 1h
CONF
systemctl enable --now fail2ban
systemctl restart fail2ban

echo "== automatic security updates"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<CONF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF

echo "== swap"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

# Restarted rather than just started: it rebuilds its network rules after the firewall changes above
systemctl enable docker
systemctl restart docker

echo
echo "Done. Before closing this session, open a NEW terminal and check:"
echo "  ssh $DEPLOY_USER@<the server's public IP>"
echo "Root login and passwords are now disabled."
