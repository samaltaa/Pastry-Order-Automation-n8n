#!/usr/bin/env bash
# Run once as root on a fresh Ubuntu 24.04 VPS: bash provision.sh
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
[ -s /root/.ssh/authorized_keys ] || { echo "Add your SSH public key to /root/.ssh/authorized_keys first." >&2; exit 1; }

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
install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /root/.ssh/authorized_keys "/home/$DEPLOY_USER/.ssh/authorized_keys"

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

systemctl enable --now docker

echo
echo "Done. Before closing this session, open a NEW terminal and check:"
echo "  ssh $DEPLOY_USER@$(hostname -I | awk '{print $1}')"
echo "Root login and passwords are now disabled."
