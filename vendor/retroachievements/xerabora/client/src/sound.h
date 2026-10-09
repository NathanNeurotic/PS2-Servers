/*
  Notification sounds. Defaults are embedded; a file named connect.wav,
  disconnect.wav or achievement.wav in the "sounds" folder of the config
  directory replaces the corresponding default.
*/
#ifndef XERABORA_SOUND_H
#define XERABORA_SOUND_H

enum sound_id {
    SOUND_CONNECT,
    SOUND_DISCONNECT,
    SOUND_ACHIEVEMENT,
    SOUND_COUNT
};

void sound_init(int enabled);

/* Plays asynchronously; never blocks the receive loop. */
void sound_play(enum sound_id id);

#endif
