# Public source export

Do not publish this deployment workspace with `git add .`. It contains licensed assets, private authoring data, authentication material, runtime releases and historical evidence.

Run the explicit allowlist export into a new directory outside the workspace:

```sh
python3 scripts/export-public-source.py /absolute/path/to/new-public-source
```

The exporter rejects symlinks, unexpected binary types, private-key markers, credential patterns, signed download URLs and deployment-specific paths/addresses. It emits SHA-256 hashes for every selected file. New files are not implicitly approved: add them to the allowlist only after review. These mechanical checks supplement human review; they do not establish licensing rights.

Included: first-party browser/server/simulation/Unreal source, runtime/editor/Fab container recipes, selected portable build/test and authentication-helper scripts, pinned dependency metadata, concise documentation, and preview CC0/OFL assets with notices. Authentication helpers contain code only; their generated sign-in URLs and account state are excluded.

Excluded: `.secrets`, `.env`, scenario data, private evaluation fixtures, raw evidence, generated web/status reports, all Unreal Content/Plugins/binaries, editor installations, releases, vendor dependencies, virtual environments, historical machine-specific docs, and generated systemd units. Nothing is deleted from the working deployment.

Before publication, the owner must select a source license, review the exact export manifest, and confirm asset/license notices. No project license is inferred or granted by this cleanup. Do not copy private deployment configuration or raw target-containing captures back into the export.
