from pydub import AudioSegment
import argparse
import os

FRAME = 10           # ms per analysis frame
SILENCE_DB = 35      # how far below the clip's loudest frame counts as silence
FLOOR_DB = -55       # ...but never stricter than this (dBFS)
MIN_GAP = 120        # ms of silence that splits a clip into two sounds
MIN_SOUND = 40       # ms, shorter blips are ignored (clicks, breath noise)
MIN_LENGTH = 50      # ms, anything shorter is probably a broken cut


def sounds_in(audio):
    """[(start, end)] in ms of every non-silent stretch of the clip."""
    frames = [audio[i:i + FRAME].dBFS for i in range(0, len(audio), FRAME)]
    loudest = max(frames, default=float('-inf'))
    if loudest == float('-inf'):
        return []
    threshold = max(loudest - SILENCE_DB, FLOOR_DB)

    sounds = []
    start = None
    quiet = 0
    for i, level in enumerate(frames + [float('-inf')] * (MIN_GAP // FRAME)):
        if level >= threshold:
            if start is None:
                start = i
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet * FRAME >= MIN_GAP:
                end = i - quiet + 1
                if (end - start) * FRAME >= MIN_SOUND:
                    sounds.append((start * FRAME, end * FRAME))
                start = None
                quiet = 0
    return sounds


def check(path):
    audio = AudioSegment.from_file(path)
    problems = []
    if len(audio) < MIN_LENGTH:
        problems.append(f"too short ({len(audio)} ms)")
    sounds = sounds_in(audio)
    if not sounds:
        problems.append("silent")
    elif len(sounds) > 1:
        problems.append(f"{len(sounds)} sounds: " + ', '.join(f"{a}-{b}ms" for a, b in sounds))
    return len(audio), sounds, problems


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check if every cut file holds a single sound.")
    parser.add_argument('folders', nargs='*', default=['wav_cut'], help="folders with cut files (default: wav_cut)")
    args = parser.parse_args()

    total = 0
    bad = 0
    for folder in args.folders:
        for path, _, files in sorted(os.walk(folder)):
            for file in sorted(files):
                if not file.endswith(('.wav', '.mp3')):
                    continue
                total += 1
                length, sounds, problems = check(os.path.join(path, file))
                if problems:
                    bad += 1
                    print(f"{os.path.join(path, file)} [{length} ms]: {'; '.join(problems)}")

    print(f"\n{total - bad}/{total} files look like a single sound")
