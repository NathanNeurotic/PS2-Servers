# Antivirus notice

PS2 Servers is an unsigned, open-source network utility. The Windows single-file
build self-extracts before starting the application; this packaging and its
network-server behavior can trigger heuristic detections. **A detection cannot
be classified as a false positive from its name alone.** Verify the exact file
and report it for review rather than disabling protection.

The portable build avoids self-extraction but remains unsigned and can also be
flagged. On Windows choose `PS2Servers-windows-x64-portable.zip`, or the x86
portable archive for a 32-bit OS, when available on the release page. Extract
the complete folder and run `PS2Servers.exe` inside it.

1. Download from [the project releases](https://github.com/NathanNeurotic/PS2-Servers/releases).
2. Compare its SHA-256 with that release's `SHA256SUMS.txt` or per-asset checksum.
3. Verify provenance with the asset name you downloaded:

   ```sh
   gh attestation verify PS2Servers-windows-x64-portable.zip -R NathanNeurotic/PS2-Servers
   ```

Checksums establish integrity and attestations establish build provenance;
neither proves safety. Running from inspected source is another option.

The [antivirus transparency guide](docs/antivirus-transparency.md) describes
network activity, saved configuration, elevation, optional services, cleanup,
and vendor reporting. A [web summary](https://nathanneurotic.github.io/PS2-Servers/falsepositives.html)
is available too. Include the asset name, release/commit, SHA-256, antivirus
version, and full detection name in a report. Do not submit private game images,
saves, or credentials.

PS2 Servers has been submitted to Avast/Gen Threat Labs for false-positive review.
That historical submission is not an approval of every later build. Neither
portable packaging nor code signing guarantees that warnings disappear.
