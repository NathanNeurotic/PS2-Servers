/* Minimal libchdr declarations and hunk metadata access for CGo.
 * Adapted from pcm720/udpfsd at ee94414.
 * Copyright 2026 Ivan V (pcm720). See THIRD_PARTY_NOTICES.md for the BSD license.
 */
#ifndef CHD_LIBCHDR_H
#define CHD_LIBCHDR_H

#include <libchdr/chd.h>

#ifdef __cplusplus
extern "C" {
#endif

static unsigned int ps2_chd_hunk_bytes(chd_file *chd) {
    const chd_header *header = chd_get_header(chd);
    return header == NULL ? 0 : header->hunkbytes;
}

#ifdef __cplusplus
}
#endif

#endif
