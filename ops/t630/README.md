# Rooibos T630 first-boot preparation

**Purpose:** prepare the incoming HP T630 as a small multi-purpose home server running Proxmox VE, with separate Home Assistant and Rooibos environments.

**Current state:** preparation only. The physical host has not yet been inspected. The working hardware assumption is 8 GB RAM and a 32 GB SSD initially, with a preferred 16 GB RAM upgrade, external USB SSD for additional storage, and eventual 256 GB internal SSD. These are planning assumptions, not verified hardware facts. Manufacturer/model variant, CPU, storage layout, RAID/controller state, firmware, NICs, operating system, and current disk contents remain unknown.

## Agreed target architecture

- **Proxmox VE:** bare-metal hypervisor and host management layer.
- **Home Assistant OS VM:** Home Assistant, its Supervisor/App ecosystem, Music Assistant installed as a Home Assistant app, and the previously planned AirPlay/Shairport Sync path from the iPhone.
- **Debian VM:** Rooibos/Moby services, collector pipeline, research tools, and future development/remote-work environment. Keep this separate from Home Assistant.
- **Remote access:** private VPN access (WireGuard or Tailscale to be selected after network review) for authorised remote access to Rooibos, Home Assistant, and music services. Do not expose SSH, Proxmox, databases, internal APIs, or administration interfaces directly to the public internet.
- **Audio:** use the T630's audio output to the amplifier with the planned 3.5 mm-to-RCA connection if supported; use a USB audio adapter only if passthrough proves problematic.

### Working resource assumptions

The original planning allocation assumed approximately 3.5 GB RAM for HAOS, 2 GB for the Debian/Rooibos VM, and 1–1.5 GB for Proxmox/host overhead. These are tight estimates on an 8 GB machine; actual usable memory and workload behaviour must be checked before final VM sizing. A 16 GB upgrade is preferred where supported. Do not overcommit memory or start all workloads automatically until measured.

Initial 32 GB storage is not assumed sufficient for long-term use. Inspect actual disks and controller state first. Plan external USB SSD capacity for additional data only after validating reliability and backup needs; the eventual 256 GB internal SSD remains a planned upgrade, not an instruction to replace or erase any installed drive.

## Rooibos role and boundaries

- Initially deploy only the common A1/A2/A6/A13 collector pipeline in the Debian environment, after the host and storage gates pass.
- Not an autonomous Alpha/Beta/Gamma agent host at this stage.
- Strictly research and paper-only. No live trading, wallet access, swaps, orders, buys, or sells.
- No agent access to Moby credentials or unrestricted connector tools.
- No live Moby collection until a source and endpoint are explicitly approved. Current source registry remains deny-by-default.
- Keep `moby_market.db` as the database identity; do not rename it.
- Do not divert resources reserved for v1.8.0 and the common collector pipeline to Chart Engine work.

## Before powering on

1. Photograph or note the exact model/variant and installed component labels privately. Do not publish service tags, serial numbers, or iDRAC credentials.
2. Inspect for shipping damage; check power supplies, fans, drive carriers, memory seating indicators and controller status.
3. Record the installed drives and any existing data. **Do not initialise, clear, import, repartition, format, or rebuild a RAID virtual disk** until the existing configuration and data-retention requirements are understood.
4. Connect display and keyboard for first inspection. Use a trusted, isolated management LAN. Do not expose any management interface or remote-console service to the public internet.
5. If the host has a remote-management controller (for example, iDRAC on applicable Dell PowerEdge systems), keep it isolated until its firmware, credentials, network settings and access controls have been inspected and secured.

## First boot: inventory only

The script `inspect-host.sh` is a read-only Linux inventory helper. It does not install software, modify services, mount disks, alter RAID, or make network requests. It records OS/kernel, platform identifiers (excluding serial numbers), CPU/RAM, block devices and filesystems, PCI/USB devices, firmware/security indicators, network listeners, time state, running services, and package tooling.

Run the script only from a trusted Linux environment. If Proxmox is installed later, this inventory helper is intended for the Linux host layer; do not run it inside an unknown pre-existing OS merely to gather information.

```bash
chmod 700 inspect-host.sh
./inspect-host.sh
```

It creates a local inventory file with restrictive permissions. Review it locally before sharing; redact hostnames, IP addresses, mount paths, and other identifying details. Run without sudo first. Missing privileged-only details are acceptable at this stage.

If the host has no trusted OS, stop after firmware/boot-device inventory. Do not boot an unknown installation or mount unfamiliar disks read-write.

## Pairing sequence (do not skip gates)

### G0 — Identify the actual host

Collect and review the inventory. Confirm:
- Exact manufacturer/model and firmware versions.
- CPU architecture and available cores.
- Installed and usable RAM, including ECC status where available.
- Disk count, capacities, health, controller and current RAID/virtual-disk layout.
- NIC count/speed and management-controller separation.
- UEFI/legacy mode, Secure Boot and TPM availability.
- Current OS and support status, or whether a clean installation is required.
- Existing data and whether any disks must be preserved.

No installation or destructive storage action before G0 is reviewed.

### G1 — Establish a trusted Proxmox host

Choose a supported Proxmox VE release only after hardware and firmware compatibility are known. Apply firmware/host updates through a controlled maintenance step. Establish a named administrator, SSH key authentication, verified SSH host key, no direct root SSH login, and a host firewall. Keep Proxmox management on the trusted LAN/VPN; no public port forwarding.

Do not disable Secure Boot or TPM protections merely to make setup easier. Document any compatibility exception before changing it.

### G2 — Build isolated virtual machines

Create the HAOS VM and Debian VM as separate guests. Keep Home Assistant add-ons/apps inside the HAOS ecosystem; keep Rooibos, research tools, and collector services inside Debian. Use separate unprivileged service identities within Debian for `rooibos-a1`, `rooibos-a2`, `rooibos-a6`, `rooibos-a13`, monitoring and backup. Separate service files, logs, configuration and data permissions. Services must not run as root and must not share credentials unnecessarily.

Use systemd supervision only after the Debian baseline is accepted. Rooibos services start disabled until their individual configuration and tests pass. Keep VM memory allocations conservative on the assumed 8 GB host and revisit them after the preferred RAM upgrade.

### G3 — Database and storage

Preserve `moby_market.db`. Inspect the actual v1.8.0 source, migrations and tests before deploying. Keep the database, WAL and SHM files under a protected directory with least-privilege access. Validate free space, storage health and backup destination. Never place secrets in the database or logs. Keep music-library storage and backups separate from the Rooibos database and its recovery path.

### G4 — Configuration and network policy

- Default deny for collection endpoints; no production source is approved yet.
- Allow only individually reviewed research-read endpoints when later authorised.
- Block Moby wallet, trading, swap, order, buy and sell capabilities technically, not merely by prompt.
- Keep credentials outside agent-readable paths and logs.
- Validate configuration before enabling any service.
- Separate DEV, TEST, RESEARCH and PAPER state; do not let test fixtures become production data.
- Use private VPN access for remote services. No direct public exposure of Proxmox, SSH, Home Assistant administration, music administration, databases, or internal APIs.
- Keep Proxmox host, HAOS guest, and Debian guest boundaries explicit; do not grant Home Assistant access to Rooibos secrets or databases.

### G5 — Recovery

Create consistent backups for Proxmox guest configuration, HAOS, the Debian VM, Rooibos SQLite data, and the music library as appropriate. Perform restores into separate test locations. Verify database integrity, foreign keys, migration state, and access permissions. Record recovery steps and prove they work before enabling scheduled collectors or relying on the server for home automation.

### G6 — Service deployment

Bring up Proxmox and validate networking/storage first. Then deploy HAOS and validate Home Assistant, Music Assistant and the planned audio path. Deploy the Debian VM separately. Within Debian, deploy incrementally in the agreed Rooibos order: common pipeline, A6 vertical slice, A2 integration, A1 integration, then A13 validation/acceptance. Use synthetic fixtures first. No live endpoint is exercised without explicit approval and a verified source mapping. Keep the agent runtime separate and out of scope until the collector host is stable.

## First-session acceptance

The host is not considered paired or ready merely because it boots or accepts SSH. G0 inventory must be reviewed; G1–G5 must be tested; only then can service deployment begin. Record every accepted change and retain a rollback path. Do not merge or deploy repository changes automatically from this preparation branch.
