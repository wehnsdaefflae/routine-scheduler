# D170 phase 1 — per-call micro-VM boot cost, measured

**This directory is a probe, not a code path.** Nothing in `src/rsched` imports it, and the
Landlock sandbox (`src/rsched/sandbox.py`, `src/rsched/landlock.py`,
`src/rsched/utils_run.py`) is untouched. It exists to answer one question with numbers:
what would it cost to boot one cloud-hypervisor micro-VM per util call instead of entering
a Landlock jail?

The written-up result for the operator lives in the routine's artifact
`d170-phase1.md`; what follows is the reproduction record.

## The answer in one line

A per-call micro-VM costs **+392 ms median** over today's jail (61.8 ms → 453.9 ms,
**7.3×**) and **~179 MB** of host RSS per concurrent call. A median run makes 31 jailed
calls, so it would pay about **+12 s**; the busiest run observed makes 369, which is
**+2.4 min**. Across batches the delta ranges +392 to +557 ms (see the spread note below),
so those totals are the low end.

## What was measured

Both arms execute the *same* command — the host interpreter against one real util's
`main.py` (`gu json get <payload> name`, chosen because it declares no dependencies, needs
no network and no secret, and its output is deterministic). Four arms, N = 25 each,
one boot CPU, 512 MiB configured guest memory:

| arm | what it is | median | p90 |
|---|---|---|---|
| `bare` | the command, no isolation | 35.7 ms | 36.9 ms |
| `jail` | wrapped as `sandbox.wrap` wraps it today | **61.8 ms** | 68.3 ms |
| `vm` | boot → run the util → power off | **453.9 ms** | 518.8 ms |
| `vm_floor` | the same boot running `busybox true` | 294.1 ms | 327.2 ms |

**Across-batch spread, stated so the headline is not the flattering pick.** Three
independent N ≥ 20 batches on the same host put the jail median at 61.8–86.7 ms and the VM
median at 453.9–643.8 ms, i.e. a delta of **+392 to +557 ms** and a multiple of
**7.3× to 8.7×**. The table shows the last and tightest batch (its VM p90 is 518.8 ms
against a max of 536.5 ms); an earlier batch, taken while this measurement's own image
builds were competing for the box, ran slower in every arm. The conclusion does not turn on
which batch is used — the cost is hundreds of milliseconds per call either way.

Reading the arms against each other decomposes the per-call VM cost:

- **294 ms** hypervisor start + kernel boot + unpacking a 37 MB initramfs,
- **~160 ms** cold interpreter start inside the guest — the host pays ~36 ms for the same
  work, because the guest's page cache is empty at every boot by construction. (The earlier,
  slower batches put this component at ~240 ms; it is the part that varies most.)

How much of the floor is the image size is the one figure here that is NOT settled. A
back-to-back batch (N = 20 per image) measured a 1.5 MB busybox-only initramfs at 312 ms
against 425 ms for the 37 MB one, i.e. ~113 ms; but the 37 MB floor measured 325 ms in the
headline table above, so 312 and 325 are within each other's spread and the attribution is
somewhere between ~15 ms and ~110 ms. Either way it is a minority of the floor, and
shrinking the image cannot take the per-call cost below the ~310 ms a busybox-only guest
already costs.

Footprint and concurrency, same image:

- **179 MB** peak hypervisor RSS for one call (`VmHWM` polled on the hypervisor's own pid,
  97 samples); the guest boots the same at a 256 MiB configuration, so the figure is
  dominated by the initramfs being unpacked into guest RAM, not by the memory size.
- 12-core host: 1 → 0.66 s, 4 → 0.82 s, 8 → 1.30 s, 12 → 2.08 s, 24 → 4.76 s for the batch.
  Every VM in every batch produced correct output. Throughput saturates near **6 calls/s**,
  and per-call latency degrades 7× by 24-way, while 24 concurrent calls would hold ~4.3 GB.

## Guest assets — provenance

Not committed (binary); downloaded to the measurement host's `d170/assets` under the system
temp dir. Both come from Alpine Linux v3.21, release 3.21.10, x86_64:

| file | source | bytes | sha256 |
|---|---|---|---|
| `vmlinuz-virt` | `https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/x86_64/netboot/vmlinuz-virt` | 11,744,256 | `65d640f2b04cfe36a714240a966071596b19fa60a0a9652ec76ab3a67993a823` |
| `alpine-minirootfs-3.21.10-x86_64.tar.gz` | `https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/x86_64/alpine-minirootfs-3.21.10-x86_64.tar.gz` | 3,490,079 | `d225c786541b5ae5ba006ef541701d9ecc7e732bd16317c08adfb93212345efa` |

The rootfs checksum matches the one Alpine publishes in that directory's
`latest-releases.yaml`; the kernel is served without a manifest checksum, so its hash above
is what this host received.

Only three files are taken out of the rootfs — `bin/busybox` and the two musl libraries it
links against. **Omitting `lib/ld-musl-x86_64.so.1` boots to `Failed to execute /init
(error -2)` followed by a triple-fault loop**, which costs a full timeout to diagnose; the
probe's `GUEST_BASE` exists to remember that.

## Two host facts that shaped the design

- **No virtiofsd on the box.** A production per-call VM has to show the caller's granted
  roots to the guest; there is no filesystem-sharing daemon installed, so that mechanism was
  not measurable here at all.
- **The Alpine `virt` kernel has no ext4 and no virtio-blk built in** — `/proc/filesystems`
  inside the guest lists only nodev entries, and `mount -t ext4 /dev/vda` answers `No such
  device`, because those drivers live in Alpine's modloop. A disk-image-based guest is
  therefore not available with this kernel, and the measurement uses an initramfs that
  carries the interpreter instead. An unprivileged `mkfs.ext4 -d` image builds fine
  (0.01 s for 8 MB), so the blocker is the kernel's build config, not privilege.

## Reproducing

`probe.py` is stdlib-only and runs under the host interpreter. Fetch the two assets to
`$TMPDIR/d170/assets/` as `vmlinuz-virt` and `alpine-minirootfs.tar.gz`, then:

```
python3.12 deploy/d170-microvm/probe.py checksums
python3.12 deploy/d170-microvm/probe.py image       --util-main <lib>/utils/json/main.py
python3.12 deploy/d170-microvm/probe.py boot        --util-main <lib>/utils/json/main.py
python3.12 deploy/d170-microvm/probe.py measure -n 25 --memory 512M \
                                                 --util-main <lib>/utils/json/main.py
python3.12 deploy/d170-microvm/probe.py footprint   --util-main <lib>/utils/json/main.py
python3.12 deploy/d170-microvm/probe.py concurrency --parallel 1,4,8,12,24 \
                                                 --util-main <lib>/utils/json/main.py
```

`measure` also writes `results.json` beside the images. A successful boot prints `d170` on
the guest's serial console — that string is the util's real answer to
`get <payload> name`, so it is the proof the guest ran the util rather than the proof that
a VM started.

## What this prototype does NOT do

Each of these is work a real implementation must do, and **every one of them can only make
the VM arm slower than measured**. The figures above are a floor, not an estimate.

1. **Mount the caller's granted roots.** The guest sees one payload file baked into its
   image. Real calls act on the run's read/write roots, which `sandbox.wrap` composes per
   call — needing virtiofs (absent here) or a per-call image build.
2. **Inject declared secrets.** `utils_run._child_env` passes only a util's declared
   variables; the guest gets none, and getting them in means a per-call image or a channel
   into the guest.
3. **Capture stdout/stderr as the engine does.** The arm reads the serial console, which
   mixes kernel messages with the util's output and has no second stream. The engine writes
   the two separately and spills large output to `.util_outputs/`.
4. **Honour a timeout or an abort.** `utils_run` kills a process group on deadline and on
   abort; the probe only has the hypervisor's own exit.
5. **Rebuild images when anything changes.** The 37 MB image was built once, taking seconds.
   A library whose utils change, whose dependencies are resolved by `uv` per util, would
   need an image cache with invalidation — none of which is measured.
6. **Install any util's dependencies.** The workload util declares none. A util with PEP 723
   dependencies needs `uv` and its cache in the guest, which is both more image and more
   cold-start work.
7. **Share any page cache between calls.** The ~243 ms in-guest interpreter start is paid
   in full by every call, by design: that is what per-call isolation means.

Two further caveats about the numbers themselves: the host was otherwise idle apart from
the daemon, so contention is understated; and the `jail` arm wraps the command with a
representative root set rather than a live `RunContext`'s, so a real jail launch composes
slightly more work than 65 ms reflects.
