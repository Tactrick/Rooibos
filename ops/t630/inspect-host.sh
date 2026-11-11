#!/usr/bin/env bash
# Read-only first-boot inventory for the Rooibos T630 host.
# No packages are installed, services changed, disks mounted, or network calls made.
set -u

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${1:-t630-inventory-${STAMP}.txt}"
if [ -e "$OUT" ]; then
  printf 'Refusing to overwrite existing file: %s\n' "$OUT" >&2
  exit 2
fi
umask 077
: > "$OUT" || exit 1
chmod 600 "$OUT" 2>/dev/null || true
exec > >(tee "$OUT") 2>&1

section() { printf '\n\n===== %s =====\n' "$1"; }
run() {
  printf '\n$'
  printf ' %q' "$@"
  printf '\n'
  if command -v "$1" >/dev/null 2>&1; then
    "$@" 2>&1 || printf '[command returned non-zero; inspection only]\n'
  else
    printf '[not installed / unavailable]\n'
  fi
}

printf 'Rooibos T630 read-only host inventory\n'
printf 'UTC collected at: %s\n' "$STAMP"
printf 'Output file: %s\n' "$OUT"
printf 'No secrets, disk serial numbers, MAC addresses, or wallet data are intentionally collected.\n'

section "Host and operating system"
run hostnamectl
run uname -a
if [ -r /etc/os-release ]; then
  printf '\n/etc/os-release\n'
  cat /etc/os-release
fi
for f in /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name /sys/class/dmi/id/product_version /sys/class/dmi/id/board_name; do
  if [ -r "$f" ]; then printf '%s: ' "$f"; cat "$f"; fi
done
run systemd-detect-virt

section "CPU and memory"
run lscpu
run free -h
if [ -r /proc/meminfo ]; then
  grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo || true
fi

section "Storage inventory (no writes or mounts)"
run lsblk -e7 -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,ROTA,TRAN
run findmnt -rn -o TARGET,SOURCE,FSTYPE,OPTIONS
run df -hT
run pvs
run vgs
run lvs
run mdadm --detail --scan

section "Controllers and peripherals"
run lspci -nn
run lsusb
run lspci -nn | grep -Ei 'raid|sas|sata|scsi|ethernet|network|vga|display|3d' || true

section "Firmware and platform security indicators"
if [ -d /sys/firmware/efi ]; then printf 'Boot mode: UEFI\n'; else printf 'Boot mode: Legacy BIOS or EFI information unavailable\n'; fi
run mokutil --sb-state
run tpm2_getcap properties-fixed
run fwupdmgr get-devices

section "Network overview (addresses and MACs intentionally omitted)"
run ip -brief link
run ip route
run ss -lntu
run networkctl list --no-pager

section "Time and service state"
run timedatectl status
run systemctl is-system-running
run systemctl list-units --type=service --state=running --no-pager

section "Package manager and deployment prerequisites"
run dpkg --print-architecture
run apt --version
run dnf --version
run rpm --version
run python3 --version
run sqlite3 --version
run openssh -V
run ufw status
run nft list ruleset

section "End of inventory"
printf 'Inventory completed. Review locally before sharing; redact hostnames, addresses, mount paths, and other identifying details if needed.\n'
printf 'Saved with restrictive permissions to: %s\n' "$OUT"
