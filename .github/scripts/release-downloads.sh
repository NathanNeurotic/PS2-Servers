#!/usr/bin/env bash
# Shared release-note download rows. Keep platform guidance in docs, not prose.
release_url="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-NathanNeurotic/PS2-Servers}/releases/download/${RELEASE_TAG:?set RELEASE_TAG to the release tag}"

row() { # row <label> <preferred> <fallback> <why-the-fallback>
  local asset="$2" note=""
  if [ ! -f "$asset" ]; then
    asset="$3"
    [ -f "$asset" ] || return 0
    note=" — $4"
  fi
  printf '| %s | [`%s`](%s/%s)%s |\n' "$1" "$asset" "$release_url" "$asset" "$note"
}

row_alternative() { # row_alternative <preferred> <alternative> <label>
  if [ -f "$1" ] && [ -f "$2" ]; then
    row "$3" "$2" "" ""
  fi
}

edge_rows() {
  local f purpose
  for f in PS2ServersEdge-*.tar.gz PS2ServersEdge-*.zip; do
    [ -f "$f" ] || continue
    case "$f" in
      *pc-64bit-amd64*) purpose="Linux PC / NAS, x86_64" ;;
      *pc-32bit-i386*) purpose="Linux PC, 32-bit x86" ;;
      *arm64-pi3-pi4-pi5-nas*) purpose="Raspberry Pi 3/4/5 with a 64-bit OS, ARM64 NAS" ;;
      *armv7-pi2-32bit-routers*) purpose="Raspberry Pi 2, Pi 3/4/5 with a 32-bit OS, ARMv7 routers" ;;
      *armv6-pi1-pi-zero*) purpose="Raspberry Pi 1 / Pi Zero" ;;
      *armv5-pogoplug-sheevaplug-dockstar*) purpose="ARMv5 Kirkwood routers" ;;
      *router-mips-little-endian-mt7621*) purpose="Little-endian MIPS routers (MT7621)" ;;
      *router-mips-big-endian-ath79*) purpose="Big-endian MIPS routers (ath79)" ;;
      *edgerouter-octeon-mips64*) purpose="EdgeRouter Octeon, 64-bit MIPS" ;;
      *mips64-little-endian*) purpose="Little-endian 64-bit MIPS" ;;
      *riscv64-sbc*) purpose="RISC-V 64-bit SBCs" ;;
      *windows-64bit*) purpose="Windows, 64-bit (headless)" ;;
      *windows-32bit*) purpose="Windows, 32-bit (headless)" ;;
      *windows-arm64*) purpose="Windows on ARM (headless)" ;;
      *macos-apple-silicon*) purpose="macOS, Apple Silicon (headless)" ;;
      *macos-intel*) purpose="macOS, Intel (headless)" ;;
      *) purpose="Edge headless build" ;;
    esac
    row "$purpose" "$f" "" ""
  done
}
