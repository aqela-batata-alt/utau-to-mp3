from pydub import AudioSegment
from pydub.playback import play
from pydub.silence import detect_leading_silence
import argparse
import csv
import math
import os
import re
import shutil
import subprocess
import sys
import unicodedata

CSV_FILE = 'teto.csv'
LINK = '¨'

# median pitch of each bank (measured on the cut files), so they all sing the same note
BANK_PITCH = {'standard': 308, 'english': 279, 'edge-continuous': 292, 'extra': 279}
# a link sound starts on the previous vowel and its own "note" only begins at the preutterance
# (from the csv), we keep just this much of the previous vowel to crossfade
KEEP_LEAD = 70

# how long (ms) each kind of piece can last, before --speed.
# "weak" is the last syllable of a word when it's not the stressed one: short, quiet and fading,
# "last weak" is that at the end of a phrase, where it gets more time to breathe out.
# "breath" is the vowel at the end of a phrase going out in a breath, "release" is the plain fade if there's no breath
DURATION = {'tonic': 240, 'last tonic': 300, 'pre': 150, 'post': 130, 'weak': 140, 'last weak': 180,
            'epenthetic': 90, 'cluster': 60, 'w': 90, 'glide': 130, 'coda': 230, 'r': 300, 'nasal': 240,
            's': 130, 'breath': 420, 'release': 260, 'rr': 90}
SYLLABLE_KINDS = ('tonic', 'last tonic', 'pre', 'post', 'weak', 'last weak')
BREATH_GAIN = -4  # under the last syllable, so "fala" doesn't become "falar"
RR_GAIN = -9      # the throat r's breath, under the vowel that comes after it
PAUSES = {',': 200, ';': 250, ':': 250, '.': 450, '!': 450, '?': 450, '…': 500}

VOWEL_LETTERS = {  # letter -> (sound, accent) where accent None = ã/õ, stressed if nothing else is
    'a': ('a', False), 'á': ('a', True), 'à': ('a', False), 'â': ('a', True), 'ã': ('ã', None),
    'e': ('e', False), 'é': ('ɛ', True), 'ê': ('e', True),
    'i': ('i', False), 'í': ('i', True),
    'o': ('o', False), 'ó': ('ɔ', True), 'ô': ('o', True), 'õ': ('õ', None),
    'u': ('u', False), 'ú': ('u', True), 'ü': ('u', False),
}
FRONT = 'eéêií'
DIGRAPHS = {'ch': 'ʃ', 'lh': 'ʎ', 'nh': 'ɲ', 'rr': 'h', 'ss': 's'}
SAME = {c: c for c in 'bdflmnptv'}
OBSTRUENTS = ('b', 'k', 'd', 'f', 'g', 'p', 't', 'v')
CODAS_OK = ('s', 'ɾ', 'l', 'm', 'n')
# words that lean on the next one and have no stress of their own ("de" is [dʒɪ], "o" is [ʊ])
CLITICS = {'o', 'a', 'os', 'as', 'e', 'de', 'da', 'do', 'das', 'dos', 'em', 'no', 'na', 'nos', 'nas',
           'que', 'se', 'me', 'te', 'lhe', 'lhes', 'por', 'com', 'sem', 'ao', 'aos', 'à', 'às',
           'mas', 'nem', 'ou', 'um', 'uns', 'pra', 'pro', 'num', 'numa'}

# when the exact sound isn't in the csv, try these instead
VARIANTS = {'a': ['a'], 'e': ['e', 'ɛ'], 'ɛ': ['ɛ', 'e'], 'i': ['i'], 'o': ['o', 'ɔ'], 'ɔ': ['ɔ', 'o'],
            'u': ['u'], 'ɪ': ['ɪ', 'i'], 'ʊ': ['ʊ', 'u']}
CONSONANT_FALLBACK = {'tʃ': 't', 'dʒ': 'd'}
REDUCE = {'e': 'ɪ', 'o': 'ʊ'}  # unstressed at the end of the word, the Brasília/Goiás way


def open_csv(filename):
    """{ipa: (pt_br, filename, preutterance)}, the first row of each sound wins."""
    sounds = {}
    try:
        with open(filename, 'r', newline='', encoding='utf-8') as file:
            for row in csv.DictReader(file):
                if 'preutterance' not in row:
                    sys.exit(f"Error: '{filename}' is from an older version, run make_csv.py again.")
                sounds.setdefault(row['ipa'], (row['pt_br'], row['filename'], float(row['preutterance'])))
    except FileNotFoundError:
        sys.exit(f"Error: '{filename}' not found, run make_csv.py first.")
    return sounds


# --- text -> sounds ---------------------------------------------------------------------------

def letters_to_phones(word):
    w = word
    w = re.sub(r'(?<![cln])h', '', w)                       # "hora" -> "ora", but keep ch/lh/nh
    w = re.sub(r'k(?=[eéêií])', 'qu', w).replace('k', 'c')
    w = w.replace('y', 'i').replace('w', 'u').replace('ñ', 'nh')
    w = re.sub(r'^ex(?=[aeiouáéíóúâêô])', 'ez', w)          # "exame" is [eˈzɐmɪ]
    w = re.sub(r'[sx]c(?=[eéêií])', 'ss', w).replace('sç', 'ss')

    phones = []
    i = 0
    while i < len(w):
        c, two = w[i], w[i:i + 2]
        prev, after = w[i - 1] if i else '', w[i + 1:i + 2]
        if c in VOWEL_LETTERS:
            sound, accent = VOWEL_LETTERS[c]
            phones.append({'v': sound, 'accent': accent})
        elif two in DIGRAPHS:
            phones.append({'c': DIGRAPHS[two], 'letter': c})
            i += 1
        elif two in ('qu', 'gu') and w[i + 2:i + 3] and w[i + 2] in VOWEL_LETTERS:
            phones.append({'c': 'k' if c == 'q' else 'g', 'letter': c})
            if w[i + 2] not in FRONT:
                phones.append({'c': 'w', 'letter': 'u'})     # "quando", "água": that u is still there
            i += 1
        elif c in 'cç':
            phones.append({'c': 's' if c == 'ç' or (after and after in FRONT) else 'k', 'letter': c})
        elif c == 'g':
            phones.append({'c': 'ʒ' if after and after in FRONT else 'g', 'letter': c})
        elif c == 'j':
            phones.append({'c': 'ʒ', 'letter': c})
        elif c == 'q':
            phones.append({'c': 'k', 'letter': c})
        elif c == 'r':
            phones.append({'c': 'h' if not prev or prev in 'nls' else 'ɾ', 'letter': c})
        elif c == 's':
            between_vowels = prev in VOWEL_LETTERS and after in VOWEL_LETTERS and prev and after
            phones.append({'c': 'z' if between_vowels else 's', 'letter': c})
        elif c == 'x':
            if not prev or (after and after in VOWEL_LETTERS):
                phones.append({'c': 'ʃ', 'letter': c})
            elif not after:
                phones += [{'c': 'k', 'letter': c}, {'c': 's', 'letter': 's'}]   # "tórax"
            else:
                phones.append({'c': 's', 'letter': 's'})                         # "texto"
        elif c == 'z':
            phones.append({'c': 'z' if after and after in VOWEL_LETTERS else 's', 'letter': c})
        elif c in SAME:
            phones.append({'c': SAME[c], 'letter': c})
        i += 1
    return phones


def mark_glides(phones):
    """Falling diphthongs: the i/u after a vowel joins its syllable ("pai", "muito"), unless it's a hiatus."""
    for i, phone in enumerate(phones):
        if 'v' not in phone or i == 0 or 'v' not in phones[i - 1] or phones[i - 1].get('glide'):
            continue
        prev = phones[i - 1]['v']
        after = phones[i + 1] if i + 1 < len(phones) else None
        after2 = phones[i + 2] if i + 2 < len(phones) else None
        if prev in ('ã', 'õ') and phone['v'] in ('o', 'e', 'i') and not phone['accent']:
            phone['glide'] = 'w' if phone['v'] == 'o' else 'j'        # "pão", "mãe", "põe"
        elif phone['v'] in ('i', 'u') and not phone['accent'] and prev != phone['v'] and prev not in ('ã', 'õ'):
            if after and after.get('c') == 'ɲ':
                continue                                              # "ra-i-nha"
            closes = after and 'c' in after and (after2 is None or 'c' in after2)
            if closes and after['letter'] != 's':
                continue                                              # "ca-ir", "ru-im", "ju-iz", "a-in-da"
            phone['glide'] = 'j' if phone['v'] == 'i' else 'w'


def split_run(consonants, first):
    """Consonants between two vowels -> (coda of the previous syllable, onset of the next)."""
    w = consonants[-1:] == ['w']
    if w:
        consonants = consonants[:-1]
    if first:
        coda, onset = [], consonants
    elif len(consonants) >= 2 and consonants[-2] in OBSTRUENTS and consonants[-1] in ('ɾ', 'l'):
        coda, onset = consonants[:-2], consonants[-2:]               # "pra", "bla"
    else:
        coda, onset = consonants[:-1], consonants[-1:]
    return coda, onset + (['w'] if w else [])


def epenthetic(consonant):
    # brazilians don't do consonants without a vowel: "ritmo" is "ri-tchi-mo", "pneu" is "pi-neu"
    return {'onset': [consonant], 'v': 'i', 'accent': False, 'glide': None, 'coda': [], 'role': 'epenthetic'}


def word_to_syllables(word):
    phones = letters_to_phones(word)
    mark_glides(phones)

    syllables = []
    run = []
    for phone in phones:
        if 'v' in phone and not phone.get('glide'):
            coda, onset = split_run(run, first=not syllables)
            if syllables:
                syllables[-1]['coda'] += coda
            syllables.append({'onset': onset, 'v': phone['v'], 'accent': phone['accent'], 'glide': None, 'coda': []})
            run = []
        elif 'v' in phone:
            syllables[-1]['glide'] = phone['glide']
        else:
            run.append(phone['c'])
    if not syllables:
        return []
    syllables[-1]['coda'] += run

    # stress: the accent says it, otherwise the end of the word does
    tonic = None
    if word not in CLITICS:
        accented = [i for i, s in enumerate(syllables) if s['accent']] or \
                   [i for i, s in enumerate(syllables) if s['accent'] is None]
        if accented:
            tonic = accented[-1]
        elif len(syllables) > 1 and re.search(r'([aeo]s?|am|em|ens)$', word):
            tonic = len(syllables) - 2
        else:
            tonic = len(syllables) - 1
    for i, syllable in enumerate(syllables):
        syllable['role'] = 'tonic' if i == tonic else 'pre' if tonic is not None and i < tonic else 'post'

    last = syllables[-1]
    if last['role'] == 'post' and last['v'] in REDUCE and not last['glide'] and last['coda'] in ([], ['s']):
        last['v'] = REDUCE[last['v']]                                 # "leite" -> [ˈlejtʃɪ], "gatos" -> [ˈgatʊs]

    # clusters, w after q/g and consonants that need a vowel
    expanded = []
    for syllable in syllables:
        onset = syllable['onset']
        syllable['w'] = onset[-1:] == ['w']
        if syllable['w']:
            onset = onset[:-1]
        syllable['cluster'] = None
        if len(onset) >= 2 and onset[-2] in OBSTRUENTS and onset[-1] in ('ɾ', 'l'):
            syllable['cluster'] = onset[-2]
            extra, onset = onset[:-2], onset[-1:]
        else:
            extra, onset = onset[:-1], onset[-1:]
        expanded += [epenthetic(c) for c in extra]
        syllable['onset'] = onset

        coda, syllable['coda'] = syllable['coda'], []
        expanded.append(syllable)
        current = syllable
        for c in coda:
            if c in CODAS_OK:
                current['coda'].append(c)
            else:
                current = epenthetic(c)
                expanded.append(current)

    for syllable in expanded:
        syllable.setdefault('w', False)
        syllable.setdefault('cluster', None)
        syllable['onset'] = syllable['onset'][0] if syllable['onset'] else ''
        if syllable['onset'] in ('t', 'd') and syllable['v'] in ('i', 'ɪ'):
            syllable['onset'] += 'ʃ' if syllable['onset'] == 't' else 'ʒ'   # "ti" is [tʃi], "de" is [dʒɪ]
    return expanded


# --- sounds -> csv entries ------------------------------------------------------------------------

class Planner:
    def __init__(self, sounds):
        self.sounds = sounds

    def find(self, keys):
        for key in keys:
            if key in self.sounds:
                return key
        return None

    def base(self, consonant, vowel):
        for c in (consonant, CONSONANT_FALLBACK.get(consonant), ''):
            if c is not None:
                key = self.find(c + v for v in VARIANTS.get(vowel, [vowel]))
                if key:
                    return key
        return None

    def link(self, first, second):
        firsts = VARIANTS.get(first, [first])
        seconds = VARIANTS.get(second, [second])
        return self.find(LINK + a + b for a in firsts for b in seconds if a != b)

    def syllable(self, syllable, prev_vowel, word_end):
        """[(csv ipa key, kind)] for one syllable, and the vowel it ends on (None if it ends closed)."""
        role = syllable['role']
        pieces = []
        vowel = {'ã': 'a', 'õ': 'o'}.get(syllable['v'], syllable['v'])
        onset = syllable['onset']
        glide = syllable['glide']
        nasal = syllable['v'] in ('ã', 'õ')

        diphthong = None
        if syllable['cluster']:
            pieces.append((self.base(syllable['cluster'], vowel), 'cluster'))
        if onset == 'h':
            pieces.append((self.find(['h' + LINK]), 'rr'))                    # "rato": breathe the r out first
        if not onset:
            # a stressed vowel gets its own clean sound ("ro-Í-do"), the unstressed ones slide from the last vowel
            key = self.link(prev_vowel, vowel) if prev_vowel and role not in ('tonic', 'last tonic') else None
            pieces.append((key, role) if key else (self.base('', vowel), role))
        elif syllable['w']:
            pieces.append((self.base(onset, 'u'), 'w'))
            pieces.append((self.link('u', vowel) or self.base('', vowel), role))
        else:
            if glide and not nasal and (vowel in ('a', 'e', 'o', 'ɔ') if glide == 'j' else vowel == 'a'):
                diphthong = self.find([onset + vowel + glide])               # "pai", "bei", "boi", "pau"
            pieces.append((diphthong or self.base(onset, vowel), role))
        last = vowel
        if diphthong:
            last, glide = ('i' if glide == 'j' else 'u'), None                # the diphthong clip ends on the glide

        if glide and not nasal:
            if vowel == 'o' and glide == 'w':
                pass                                                         # "ou" is just [o] here
            else:
                second = 'i' if glide == 'j' else 'u'
                key = self.link(vowel, second)
                if key:
                    pieces.append((key, 'glide'))
                    last = second

        codas = list(syllable['coda'])
        if nasal or codas[:1] in (['m'], ['n']):
            ending = codas.pop(0) if codas[:1] in (['m'], ['n']) else None
            if nasal:                                                        # "pão", "mãe", "põem"
                nasal_key = {('a', 'w'): 'ɐ̃w̃', ('a', 'j'): 'ɐ̃j̃', ('o', 'j'): 'õj̃'}.get((vowel, glide), 'ɐ̃' if vowel == 'a' else 'õ')
            else:
                final = word_end and codas in ([], ['s'])
                nasal_key = {'a': 'ɐ̃w̃' if final and ending == 'm' else 'ɐ̃',     # "falam" vs "campo"
                             'e': 'ẽj̃' if final else 'ẽ', 'ɛ': 'ẽj̃' if final else 'ẽ',  # "também" vs "tempo"
                             'i': 'ĩ', 'o': 'õ', 'ɔ': 'õ', 'u': 'ũ'}.get(vowel, 'ɐ̃')
            pieces.append((self.find([LINK + nasal_key]), 'nasal'))
            last = None
        elif codas:
            ending = codas.pop(0)
            vowels = VARIANTS.get(last, [last])
            if ending == 'ɾ' and not word_end:
                # in the middle of the word it's the goiano r, "porta" [ˈpɔɻtɐ], and it goes into the next syllable
                pieces.append((self.find(LINK + v + 'ɻ' + LINK for v in vowels), 'coda'))
            else:
                sound = {'s': 's', 'ɾ': 'h', 'l': 'w'}[ending]              # "mar" is [mah], the throat r
                pieces.append((self.find(LINK + v + sound for v in vowels), 'r' if ending == 'ɾ' else 'coda'))
            last = None
        for coda in codas:
            if coda == 's':
                pieces.append((self.find([LINK + 's']), 's'))
        return [(key, kind) for key, kind in pieces if key], last

    def phrase(self, words, punctuation):
        """[(csv ipa key, kind, pitch in semitones, gain in dB, fade out?)] for words said in one breath."""
        syllables = []
        for number, word in enumerate(words):
            word_syllables = word_to_syllables(word)
            for i, syllable in enumerate(word_syllables):
                syllables.append((number, syllable, i == len(word_syllables) - 1))
        if not syllables:
            return [], []

        tonics = [i for i, (_, s, _) in enumerate(syllables) if s['role'] == 'tonic']
        last_tonic = tonics[-1] if tonics else len(syllables) - 1
        ending = punctuation if punctuation in ('?', '!') else '.'
        tune = {'.': (-1, -2), '?': (3, 1), '!': (2, 0)}[ending]

        pieces = []
        shown = [[] for _ in words]  # csv keys per word, just for printing
        prev_vowel = None
        for i, (number, syllable, word_end) in enumerate(syllables):
            pitch = -1.5 * i / max(len(syllables) - 1, 1)                     # a phrase slowly goes down
            role = syllable['role']
            if i == last_tonic:
                pitch += tune[0]
                role = 'last tonic' if role == 'tonic' else role
            elif i > last_tonic:
                pitch += tune[1]
            elif role == 'tonic':
                pitch += 1
            weak = word_end and role == 'post'                               # "ga-to": the "to" just goes away
            if weak:
                role = 'last weak' if i == len(syllables) - 1 else 'weak'
                weak = role == 'weak'                                        # the last one fades in the breath instead
                pitch -= 1
            gain = {'tonic': 1.5, 'last tonic': 1.5, 'post': -2, 'weak': -5, 'last weak': -4, 'epenthetic': -3}.get(role, 0)
            syllable = dict(syllable, role=role)

            units, prev_vowel = self.syllable(syllable, prev_vowel, word_end)
            # fade the end of a weak syllable, unless it ends in a consonant that still has to be heard
            fades = [weak and i == len(units) - 1 and kind in SYLLABLE_KINDS + ('glide',) for i, (_, kind) in enumerate(units)]
            pieces += [(key, kind, pitch, gain, fade) for (key, kind), fade in zip(units, fades)]
            shown[number] += [key for key, kind in units if kind != 'rr']

        if prev_vowel and pieces:                    # the last vowel goes on and out in a breath ("¨ah" is a + breath)
            vowels = VARIANTS.get(prev_vowel, [prev_vowel])
            key, kind = self.find(LINK + v + 'h' for v in vowels), 'breath'
            if not key:
                key, kind = self.find(LINK + v for v in vowels), 'release'
            if key:
                pieces.append((key, kind, pieces[-1][2], pieces[-1][3] + BREATH_GAIN, False))
                shown[-1].append('…')                # it's the "¨ar" file, but printing that would look like an r
        return pieces, shown


def split_text(text):
    """[(words, punctuation)] phrases, punctuation is '' when the text just ends."""
    text = unicodedata.normalize('NFC', text.lower())
    if re.search(r'\d', text):
        print("numbers aren't supported yet, write them out (\"dois\", not \"2\")", file=sys.stderr)
    phrases = []
    words = []
    for token in re.findall(r"[a-záàâãéêíóôõúüçñkwy]+|[,.;:!?…]", text):
        if token in PAUSES:
            if words:
                phrases.append((words, token))
            words = []
        else:
            words.append(token)
    if words:
        phrases.append((words, ''))
    return phrases


# --- csv entries -> audio -------------------------------------------------------------------------

class Singer:
    def __init__(self, sounds, cuts, pitch, speed):
        self.sounds = sounds
        self.cuts = cuts
        self.pitch = pitch
        self.speed = speed
        self.cache = {}

    def load(self, filename):
        if filename not in self.cache:
            for extension in ('wav', 'mp3'):
                path = f"{self.cuts}/{filename}.{extension}"
                if os.path.exists(path):
                    self.cache[filename] = AudioSegment.from_file(path)
                    break
            else:
                sys.exit(f"Error: {self.cuts}/{filename} not found, run cut.py first.")
        return self.cache[filename]

    def piece(self, key, kind, pitch, gain, fade):
        _, filename, preutterance = self.sounds[key]
        bank = filename.split('/')[0]
        audio = self.load(filename)

        if key.startswith(LINK) and key != LINK + 's':
            # starts on the previous vowel, keep just enough of it to crossfade
            audio = audio[int(max(0, preutterance - KEEP_LEAD)):]
        else:
            start = detect_leading_silence(audio, silence_threshold=audio.max_dBFS - 30, chunk_size=5)
            audio = audio[max(0, start - 10):]
        # word endings fade to silence, which leaves holes in the middle of a phrase
        end = detect_leading_silence(audio.reverse(), silence_threshold=audio.max_dBFS - 35, chunk_size=5)
        if end < len(audio):
            audio = audio[:len(audio) - max(0, end - 10)]

        semitones = pitch + 12 * math.log2(self.pitch / BANK_PITCH.get(bank, self.pitch))
        if abs(semitones) > 0.05:
            rate = int(audio.frame_rate * 2 ** (semitones / 12))
            audio = audio._spawn(audio.raw_data, overrides={'frame_rate': rate}).set_frame_rate(audio.frame_rate)

        length = DURATION[kind] / self.speed
        if kind in SYLLABLE_KINDS and key.startswith(LINK):
            length += KEEP_LEAD                    # a vowel-to-vowel link is the whole syllable
        audio = audio[:int(length)]
        if kind == 'rr':
            gain += RR_GAIN
        audio = audio.apply_gain(-3 - audio.max_dBFS + gain)
        if kind == 'breath':
            audio = audio.fade_out(len(audio) // 2)         # the breath already dies out, this only smooths it
        elif kind == 'release':
            audio = audio.fade_out(len(audio) * 6 // 10)
        elif fade:
            audio = audio.fade_out(len(audio) * 4 // 10)
        elif kind == 'r':
            audio = audio.fade_out(len(audio) // 4)  # the breath trails off
        return audio

    def sing(self, phrases, planner):
        output = AudioSegment.silent(duration=80)
        for words, punctuation in phrases:
            pieces, shown = planner.phrase(words, punctuation)
            print(' '.join('|'.join(self.sounds[key][0] if key in self.sounds else key for key in keys)
                           for keys in shown if keys) + punctuation)
            phrase = None
            for i, (key, kind, pitch, gain, fade) in enumerate(pieces):
                # no fading if the next word glues to this vowel ("gato e" -> ga|tu ¨ui)
                following = pieces[i + 1][0] if i + 1 < len(pieces) else ''
                if following.startswith(LINK) and following != LINK + 's':
                    fade = False
                audio = self.piece(key, kind, pitch, gain, fade)
                if phrase is None:
                    phrase = audio
                    continue
                crossfade = 50 if key.startswith(LINK) and key != LINK + 's' else 10 if kind == 'cluster' else 15
                if pieces[i - 1][1] in ('r', 'rr'):
                    crossfade = 40                 # the next sound comes in over the end of the breath
                elif kind in ('tonic', 'last tonic') and not key.startswith(LINK) and pieces[i - 1][1] in SYLLABLE_KINDS:
                    crossfade = 30                 # "ro-í": a vowel right after a vowel, no gap between them
                crossfade = min(crossfade, len(phrase) - 1, len(audio) - 1)
                phrase = phrase.append(audio, crossfade=max(crossfade, 0))
            if phrase is not None:
                output += phrase.fade_out(40)
            output += AudioSegment.silent(duration=PAUSES.get(punctuation, 300), frame_rate=output.frame_rate)
        return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Make Kasane Teto speak brazilian portuguese.")
    parser.add_argument('text', nargs='*', help="what Teto should say (default: read from stdin)")
    parser.add_argument('-o', '--output', default='teto', help="output file name, without extension (default: teto)")
    parser.add_argument('--wav', action='store_true', help="save as .wav (default)")
    parser.add_argument('--mp3', action='store_true', help="save as .mp3 (needs ffmpeg)")
    parser.add_argument('-p', '--play', action='store_true', help="play it after saving")
    parser.add_argument('--cuts', default=None, help="folder with the cut files (default: wav_cut, or mp3_cut)")
    parser.add_argument('--pitch', type=float, default=290, help="the note Teto speaks around, in Hz (default: 290)")
    parser.add_argument('--speed', type=float, default=1.0, help="speech speed, 1.2 is 20%% faster (default: 1)")
    args = parser.parse_args()

    text = ' '.join(args.text) if args.text else sys.stdin.read()
    cuts = args.cuts or ('wav_cut' if os.path.isdir('wav_cut') else 'mp3_cut')
    sounds = open_csv(CSV_FILE)

    audio = Singer(sounds, cuts, args.pitch, args.speed).sing(split_text(text), Planner(sounds))

    output, extension = os.path.splitext(args.output)
    extensions = [e for e in ('wav', 'mp3') if getattr(args, e)] or [extension.lstrip('.').lower() if extension.lower() in ('.wav', '.mp3') else 'wav']
    for extension in extensions:
        audio.export(f"{output}.{extension}", format=extension)
        print(f"saved {output}.{extension} ({len(audio) / 1000:.1f} s)")

    if args.play:
        if shutil.which('ffplay'):
            # pydub's play() uses ffplay too, but lets it spam the terminal
            subprocess.run(['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', f"{output}.{extensions[0]}"])
        else:
            play(audio)
