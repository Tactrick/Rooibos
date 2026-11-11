# Rooibos T630 first-boot preparation

**Purpose:** prepare the incoming T630 as dedicated, secure infrastructure for the common Rooibos A1/A2/A6/A13 collector pipeline.

**Current state:** preparation only. The physical host has not yet been inspected. Manufacturer/model variant, CPU, RAM, storage layout, RAID controller, firmware, NICs, operating system, and current disk contents are unknown. No OS, RAID, network, or service configuration is assumed.

## Role and boundaries

- Dedicated collector infrastructure, initially A1/A2/A6/A13 only.
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

After booting a trusted Linux environment, run:

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

### G1 — Establish a trusted host

Choose a supported Linux distribution/version only after hardware and firmware compatibility are known. Apply firmware/OS updates through a controlled maintenance step. Establish a named administrator, SSH key authentication, verified SSH host key, no direct root login, and a host firewall. Keep management access on the trusted LAN/VPN; no public port forwarding.

Do not disable Secure Boot or TPM protections merely to make setup easier. Document any compatibility exception before changing it.

### G2 — Host isolation and service identities

Create dedicated unprivileged service identities for `rooibos-a1`, `rooibos-a2`, `rooibos-a6`, `rooibos-a13`, monitoring and backup. Separate service files, logs, configuration and data permissions. Services must not run as root and must not share credentials unnecessarily.

Use systemd supervision only after the host baseline is accepted. Services start disabled until their individual configuration and tests pass.

### G3 — Database and storage

Preserve `moby_market.db`. Inspect the actual v1.8.0 source, migrations and tests before deploying. Keep the database, WAL and SHM files under a protected directory with least-privilege access. Validate free space, storage health and backup destination. Never place secrets in the database or logs.

### G4 — Configuration and network policy

- Default deny for collection endpoints; no production source is approved yet.
- Allow only individually reviewed research-read endpoints when later authorised.
- Block Moby wallet, trading, swap, order, buy and sell capabilities technically, not merely by prompt.
- Keep credentials outside agent-readable paths and logs.
- Validate configuration before enabling any service.
- Separate DEV, TEST, RESEARCH and PAPER state; do not let test fixtures become production data.

### G5 — Recovery

Create a consistent SQLite backup and perform a restore into a separate test location. Verify database integrity, foreign keys, migration state, and access permissions. Record recovery steps and prove they work before enabling scheduled collectors.

### G6 — Collector deployment

Deploy incrementally in the agreed order: common pipeline, A6 vertical slice, A2 integration, A1 integration, then A13 validation/acceptance. Use synthetic fixtures first. No live endpoint is exercised without explicit approval and a verified source mapping. Keep the agent runtime separate and out of scope until the collector host is stable.

## First-session acceptance

The host is not considered paired or ready merely because it boots or accepts SSH. G0 inventory must be reviewed; G1–G5 must be tested; only then can G6 begin. Record every accepted change and retain a rollback path. Do not merge or deploy repository changes automatically from this preparation branch.
