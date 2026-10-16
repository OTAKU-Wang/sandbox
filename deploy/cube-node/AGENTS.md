# deploy/cube-node/ — Cube 计算节点 bring-up (scored 8: 10 files / ~1.7k LOC, 100% bash+python, CI-gated by `.github/workflows/cube-node-smoke.yml`, and the only place the node 红线 are enforced)

Parent `deploy/AGENTS.md` owns the shared script conventions (sentinels, env-file secrets, no-pipe-exit-codes). Everything here is node-specific.

## WHERE TO LOOK
| Task | File |
|------|------|
| Pre-install hard gate — 10 independent checks, any FAIL → exit 1 | `preflight.sh` (431): kvm, data_fs (XFS+reflink), ebpf, cubelet_bin (sha vs pinned bundle), volume_sock, tls_sidecar, containerd, resources, tee_probe, **sandbox_cidr** |
| Install/upgrade, idempotent, refuses non-root | `install.sh` (256) — runs preflight, then the upstream `install-compute.sh`, then the three CDS components |
| Post-install acceptance | `verify.sh` (245): `--json` → `{"ok", "checks":{name:{status: pass|fail|skipped, detail}}, "tee"}`; exit 0 only when nothing failed |
| One-shot local node (XFS loop disk → install → verify) | `node_bringup.sh` (72) |
| Audit log tailer (disk queue, watermark resume, write-stop protection, mTLS+Bearer push) | `audit_tailer.py` (596) |
| TEE capability probe (node-local copy of the tdx/sev_snp matrix, stdlib only) | `tee_probe.py` (134) — `--json`, single-line `tee=tdx|sev_snp|none` |
| systemd units + rendered config templates | `tls-proxy.service`, `audit-tailer.service`, `tls-proxy.stunnel.conf`, `audit-tailer.env.template` |

## CONVENTIONS
- Every tunable is a `CDS_CUBE_*` env var with `${VAR:-default}`; the default is deliberately written twice (header comment + code) so node-side keys never depend on backend defaults.
- Units/config ship as templates with `__VAR__` placeholders (`__CUBELET_GRPC_PORT__`, `__TLS_PROXY_PORT__`, `__CDS_CUBE_AUDIT_TAILER_BIN__`) rendered by `install.sh`.
- Upstream version comes from `scripts/cube-bundle-manifest.json` (fallback `v0.7.3-rc2`); the pin unit is the upstream git sha.
- `CDS_CUBE_MOCK_ROOT=DIR` redirects `/dev/kvm`, `/proc/*`, `/sys/fs/bpf`, `/var/lib`, `/etc/cds`, `/etc/systemd/system` under DIR and prepends `DIR/bin` to PATH — the shared non-root rehearsal path of preflight/install/verify. Test-only; production must not set it.
- Node state lives in `/var/lib/cds/cube-node/` (`INSTALL_STATE.json`, `NODE_CAPABILITIES.json` — preflight upserts `tee=` there, and it is reported at registration). Log lines: `[cube-node-install] …`; `die()` = stderr + exit 1.
- The three CDS components (volume plugin, stunnel TLS sidecar, audit tailer) install as their OWN systemd units, next to — never inside — the stock upstream units.

## ANTI-PATTERNS (红线)
- 红线 1/2/7: the upstream dataplane stays zero-diff and these scripts only read it (the sole legal write is `fetch` / `reset --hard`, which they never execute); CDS is the only control plane — no CubeAPI/CubeMaster/CubeOps/WebUI/CubeProxy or its Redis/MySQL on a compute node.
- 红线 5: audit lines are never dropped. Queue overflow = critical log + write-stop, never "queue full → discard"; `audit_tailer.py` is fail-closed on BOTH transports (mTLS client cert AND `Authorization: Bearer`) — missing/empty TLS material exits 2 rather than falling back to plaintext.
- `preflight.sh` does not degrade: no sudo wrapper, no retries — insufficient permission is an honest FAIL for that check. `tee_probe` is the single informational check (no hardware = `tee=none`; only a probe error FAILs).
- Ignoring the sandbox-CIDR warning is how a node loses its own network: upstream's default `192.168.0.0/18` overlaps common LANs, so egress steals their next hop. Fix upstream's `CUBE_SANDBOX_NETWORK_CIDR` (e.g. `10.100.0.0/18`) in `.one-click.env`.
- Missing sidecar certs = hard fail (no silent plaintext); `CDS_CUBE_NODE_GRPC_TLS=false` is a local-low-risk-only escape with a loud warning; stunnel `verify = 2` is mandatory (0/1 forbidden in production).
- Missing `CDS_CUBE_AUDIT_SERVICE_TOKEN` = refuse to install: a guaranteed-401 audit link is worse than no node.
- Re-running `node_bringup.sh` must never destroy `/data/cubelet` (existing non-XFS filesystem → refuse); never write a volatile `/dev/vNN`/`/dev/loopN` name into fstab (legacy lines are removed with a `.bak-cds` backup).
- CI's TLS smoke mints a throwaway 2-day self-signed cert in `/tmp/cds-smoke-tls` (openssl) and passes it via `CDS_CUBE_TLS_{CERT,KEY,CA}`, then asserts only `verify-report.json → ok` — production certificates come from `certificate_service`. And `cube-node-smoke.yml` is `workflow_dispatch`-only on self-hosted `kvm` runners, so a normal push/PR never exercises this directory (its real-node counterpart stays FG-017).

## COMMANDS
```bash
bash deploy/cube-node/preflight.sh                        # read-only; non-root rehearsal: CDS_CUBE_MOCK_ROOT=DIR
sudo CUBE_UPSTREAM_DIR=/path/to/CubeSandbox bash deploy/cube-node/install.sh
bash deploy/cube-node/verify.sh --json                    # exit 0 = acceptance passed
python3 deploy/cube-node/tee_probe.py --json [--capabilities-file PATH]
# tailer runs via cds-audit-tailer.service + EnvironmentFile /etc/cds/audit-tailer.env
```
