# Security

Report vulnerabilities privately to the maintainers through the repository host's private
vulnerability reporting, not in a public issue. Include the commit, the affected service
(web, mission, vision, signalling, Unreal, TURN) and reproduction steps.

The installed services bind loopback only; Caddy on TCP 80 and TURN on 3478 plus its relay
range are the network-facing exceptions. Model credentials live in `.env`, which is never read
by the SAM container, the browser bundle or the signalling server. See `deploy/README.md` for
the port map and `web/README.md` for the session and evidence access rules.

## Known dependency constraints

The qualified demo still has upstream dependency advisories. Passing tests and secret scans do
not establish that these dependencies are free of vulnerabilities:

| Environment                            | Pinned packages with advisories                 | Upgrade constraint                                                                                                                                                                                                                                                                                                |
| -------------------------------------- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Mission host (`requirements.txt`)      | Pillow 11.0.0, Tornado 6.4.1, MessagePack 1.1.2 | The fully patched Pillow 13.0.0 and Tornado 6.6 require Python 3.11 or newer; the qualified mission host uses Python 3.10. Validate a newer host environment against Cosys, image handling and the complete tests before changing the supported runtime.                                                          |
| SAM device (`vision-requirements.txt`) | PyTorch 2.10.0, setuptools 80.10.2              | The PyTorch advisory concerns `torch.jit.script`; the setuptools advisories concern Windows wheel installation and macOS source packaging. The deployed image is Linux. Upgrading its toolchain still requires rebuilding and qualifying the image; existing throughput records do not qualify a changed runtime. |

Relevant upstream sources include the [Pillow security advisories](https://github.com/python-pillow/Pillow/security/advisories),
[Tornado security advisories](https://github.com/tornadoweb/tornado/security/advisories),
[MessagePack security advisories](https://github.com/msgpack/msgpack-python/security/advisories),
[PyTorch advisory](https://github.com/advisories/GHSA-rrmf-rvhw-rf47),
and the [Windows](https://github.com/advisories/GHSA-xmj3-9gf7-h52w) and
[macOS](https://github.com/advisories/GHSA-h35f-9h28-mq5c) setuptools advisories.
Python requirements are published by [Pillow 13.0.0](https://pypi.org/project/pillow/13.0.0/)
and [Tornado 6.6](https://pypi.org/project/tornado/6.6/).

The application obtains camera images from the simulator. The SAM device runs without network
access or credentials and verifies the checkpoint hash. These boundaries limit exposure; they
are not fixes for the dependency advisories. Use this release as a controlled hackathon demo,
and address the dependency upgrades before broadening its trust boundary or treating it as a
production deployment. GitHub dependency alerts remain enabled and are not suppressed.
