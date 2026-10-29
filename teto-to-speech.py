from dataclasses import dataclass, replace
from functools import lru_cache
from pydub import AudioSegment
from pydub.playback import play
from pydub.silence import detect_leading_silence
import argparse
import csv
import json
import math
import numpy as np
import os
import re
import shutil
import subprocess
import sys
import unicodedata

CSV_FILE = 'teto.csv'
UNITS_FILE = 'units.csv'  # every recording that could be each sound, not just the best one (make_csv.py)
LINK = '¨'

# --- settings --------------------------------------------------------------------------------------
# unit selection picks, for every sound, the recording with the lowest total cost:
#     target cost        = phonetic fit + prosodic mismatch (pitch, length, loudness) + modification penalty
#     concatenation cost = pitch jump + loudness jump at the join + switching voicebank
# --config some.json overrides any of these ({"w_f0": 1.0, ...}), --no-modification sets no_modification
CONFIG = {
    # target cost
    'w_phonetic': 2.0,          # per point of make_csv.py's fit score below the best recording for that sound
    'w_f0': 0.5,                # per semitone between the unit's own pitch and the target pitch
    'w_duration_short': 2.0,    # per octave (x2) the unit is shorter than the target: nothing stretches it
    'w_duration_long': 0.2,     # per octave it's longer than the target: it just gets cut
    'w_energy': 0.1,            # per dB between the unit's own loudness and the target loudness
    # modification penalty (part of the target cost): the pitch is moved with TD-PSOLA, which keeps the
    # length and the formants, but big shifts still sound buzzy/processed, so the further the worse
    'mod_free_semitones': 2.0,  # shifting up to this much is free
    'w_mod': 0.25,              # then it costs this per semitone, squared
    'mod_max_factor': 2.0,      # and scaling the pitch beyond this factor (2 = an octave)...
    'mod_max_penalty': 20.0,    # ...costs this much more on top
    # concatenation cost
    'w_join_f0': 0.3,           # per semitone of pitch jump where two voiced units meet (after their shifts)
    'w_join_energy': 0.05,      # per dB of loudness jump there (after the energy normalization)
    'w_bank_switch': 0.5,       # the two units come from different voicebanks (different session, different voice)
    # rendering (like utau-to-mp3/voice-synth.py)
    'max_shift_semitones': 5.0, # TD-PSOLA never moves the pitch further than this
    'smooth_fade_ms': 40,       # equal-power crossfade into a voiced sound (vowel, m, n, l...)
    'unvoiced_fade_ratio': 0.7, # into s, t, k, p...: the previous sound fades over this much of it, the consonant comes in quickly
    'render_margin_ms': 70,     # a unit keeps playing this much past its end, for the next one to fade in over
    # energy normalization
    'unit_level_db': -24.0,     # every unit is scaled so its median frame loudness is this (up to max_unit_gain_db)...
    'max_unit_gain_db': 12.0,
    'gain_smoothing_ms': 30,    # ...then the target loudness is one gain curve over the utterance, smoothed this much
    'edge_fade_ms': 5,          # tiny fade in on a unit that doesn't crossfade with anything, against clicks
    'output_level_db': -16.0,   # the finished utterance is normalized to this median loudness...
    'output_peak_db': -1.0,     # ...and peaks are softly limited to this
    # A/B test: still pick the units by prosody, but play them at their own pitch (no TD-PSOLA, they're
    # only cut to length)
    'no_modification': False,
}

# a link sound starts on the previous vowel and its own "note" only begins at the preutterance
# (from the csv), we keep just this much of the previous vowel to crossfade
KEEP_LEAD = 70
FRAME_MS = 10            # pitch and loudness are measured every 10 ms
SAMPLE_RATE = 44100      # everything is rendered at this rate
F0_RANGE = (120, 700)    # Hz, where to look for Teto's pitch

# --- prosody rules (predict_prosody) ---------------------------------------------------------------
# how long (ms) each kind of piece can last, before --speed.
# "weak" is the last syllable of a word when it's not the stressed one: short, quiet and fading,
# "last weak" is that at the end of a phrase, where it gets more time to breathe out.
# "breath" is the vowel at the end of a phrase going out in a breath, "release" is the plain fade if there's no breath
DURATION = {'tonic': 240, 'last tonic': 300, 'pre': 150, 'post': 130, 'weak': 140, 'last weak': 180,
            'epenthetic': 90, 'cluster': 60, 'w': 90, 'glide': 130, 'coda': 230, 'r': 300, 'h': 140, 'nasal': 240,
            's': 130, 'breath': 320, 'release': 260, 'rr': 90}
SYLLABLE_KINDS = ('tonic', 'last tonic', 'pre', 'post', 'weak', 'last weak')
WORD_LENGTH_SHORTENING = 0.05  # syllables are 5% shorter for each syllable their word has beyond 2...
WORD_LENGTH_MIN_SCALE = 0.85   # ...but never shorter than this
# pitch, in semitones from --pitch
DECLINATION = -1.5                               # from the first syllable of a phrase to the last
TUNE = {'.': (-1, -2), '?': (3, 1), '!': (2, 0)}  # on the last stressed syllable, and after it
TONIC_RISE = 1                                   # the other stressed syllables
WEAK_DROP = -1                                   # an unstressed end of a word, "ga-to": the "to" just goes away
# loudness, in dB, by what the syllable is
GAIN = {'tonic': 1.5, 'last tonic': 1.5, 'post': -2, 'weak': -5, 'last weak': -4, 'epenthetic': -3}
BREATH_GAIN = -4  # under the last syllable, so "fala" doesn't become "falar"
EXHALE_GAIN = -8  # dB on the breath part of that (after the voice stops), so it's a soft sigh
HISS_GAIN = 0     # dB on the hiss of the Brasília r in the middle of a word ("porta"), 0 = ~8 dB under the vowel
HISS_VOWEL_MS = 50  # that r keeps this much of its vowel, to blend with the syllable before...
HISS_MS = 90        # ...and then hisses this long (before --speed), however long the file's vowel is
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
# the r closing a syllable ("porta", "flor"), like utau-to-mp3/voice-synth.py does it (--accent):
#   brasilia: the hissing r, the vowel goes out in a breath ("porta" = [ˈpɔhtɐ], "mar" = [mah]), and a final
#             -ar/-er/-ir loses it ("falar" = falá, "fazer" = fazê)
#   goiano:   the english "V r" in the middle of a word, the throat [h] at the end ("mar" = [mah])
ACCENT = 'brasilia'


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
            if ACCENT == 'brasilia' and i == len(w) - 1 and len(w) > 3 and prev in 'aei':
                pass                                                    # "falar" -> falá, "fazer" -> fazê
            else:
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

@dataclass
class Target:
    """One unit of the utterance: which sound, where it is, and (after predict_prosody) how it should sound."""
    key: str             # the sound, a csv ipa key
    kind: str            # what part of the syllable it is, see DURATION
    role: str            # what its syllable is: tonic, pre, post, weak... see GAIN
    syllable: int        # which syllable of the phrase it's in...
    syllables: int       # ...out of how many
    word_syllables: int  # how many syllables its word has
    tune: str = ''       # 'nucleus' = the phrase's last stressed syllable (the tune is there), 'tail' = after it
    fade: bool = False   # its end fades out (a weak syllable)
    f0: float = 0.0      # target pitch, Hz
    duration: float = 0.0  # target length, ms
    energy: float = 0.0  # target loudness, dB above or below the usual


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

    def cluster(self, consonant, vowel):
        """The tiny consonant before the tap in "pra", "tre": its syllable with this vowel, or with a close one when
        there's none ("tri": "ti" is [tʃi] in the csv, so it's the t of "te"), but never just the vowel."""
        for v in (vowel, 'e', 'a'):
            key = self.find(consonant + w for w in VARIANTS.get(v, [v]))
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
            pieces.append((self.cluster(syllable['cluster'], vowel), 'cluster'))
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
            if ending == 'ɾ' and ACCENT == 'brasilia' and not word_end:
                # the hissing r: the vowel goes out in a breath before the next consonant, "porta" = [ˈpɔhtɐ]
                pieces.append((self.find(LINK + v + 'h' for v in vowels), 'h'))
            elif ending == 'ɾ' and not word_end:
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

    def phrase(self, words):
        """[Target] for words said in one breath (the prosody comes after, from predict_prosody)."""
        syllables = []
        for number, word in enumerate(words):
            word_syllables = word_to_syllables(word)
            for i, syllable in enumerate(word_syllables):
                syllables.append((number, syllable, i == len(word_syllables) - 1, len(word_syllables)))
        if not syllables:
            return [], []

        tonics = [i for i, (_, s, _, _) in enumerate(syllables) if s['role'] == 'tonic']
        last_tonic = tonics[-1] if tonics else len(syllables) - 1

        targets = []
        shown = [[] for _ in words]  # csv keys per word, just for printing
        prev_vowel = None
        for i, (number, syllable, word_end, word_length) in enumerate(syllables):
            role = syllable['role']
            tune = 'nucleus' if i == last_tonic else 'tail' if i > last_tonic else ''
            if tune == 'nucleus' and role == 'tonic':
                role = 'last tonic'
            weak = word_end and role == 'post'                               # "ga-to": the "to" just goes away
            if weak:
                role = 'last weak' if i == len(syllables) - 1 else 'weak'
                weak = role == 'weak'                                        # the last one fades in the breath instead
            syllable = dict(syllable, role=role)

            units, prev_vowel = self.syllable(syllable, prev_vowel, word_end)
            # fade the end of a weak syllable, unless it ends in a consonant that still has to be heard
            fades = [weak and j == len(units) - 1 and kind in SYLLABLE_KINDS + ('glide',) for j, (_, kind) in enumerate(units)]
            targets += [Target(key, kind, role, i, len(syllables), word_length, tune, fade)
                        for (key, kind), fade in zip(units, fades)]
            shown[number] += [key for key, kind in units if kind != 'rr']

        if prev_vowel and targets:                   # the last vowel goes on and out in a breath ("¨ah" is a + breath)
            vowels = VARIANTS.get(prev_vowel, [prev_vowel])
            key, kind = self.find(LINK + v + 'h' for v in vowels), 'breath'
            if not key:
                key, kind = self.find(LINK + v for v in vowels), 'release'
            if key:
                targets.append(replace(targets[-1], key=key, kind=kind, fade=False))
                shown[-1].append('…')                # it's the "¨ar" file, but printing that would look like an r
        return targets, shown


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


# --- prosody: how each unit should sound ----------------------------------------------------------

def predict_prosody(targets, punctuation, pitch, speed):
    """Fills in every target's pitch (Hz), length (ms) and loudness (dB above or below normal), with rules:
    where its syllable is in the phrase, its stress, how long its word is and the punctuation."""
    up, down = TUNE.get(punctuation, TUNE['.'])
    for i, target in enumerate(targets):
        if target.kind in ('breath', 'release'):
            # the last vowel going on and out in a breath: same note, quieter
            target.f0, target.energy = targets[i - 1].f0, targets[i - 1].energy + BREATH_GAIN
            target.duration = DURATION[target.kind] / speed
            continue
        semitones = DECLINATION * target.syllable / max(target.syllables - 1, 1)  # a phrase slowly goes down
        if target.tune == 'nucleus':
            semitones += up
        elif target.tune == 'tail':
            semitones += down
        elif target.role == 'tonic':
            semitones += TONIC_RISE
        if target.role in ('weak', 'last weak'):
            semitones += WEAK_DROP
        target.f0 = pitch * 2 ** (semitones / 12)
        target.energy = GAIN.get(target.role, 0) + (RR_GAIN if target.kind == 'rr' else 0)
        # the more syllables a word has, the faster each one goes
        scale = max(WORD_LENGTH_MIN_SCALE, 1 - WORD_LENGTH_SHORTENING * max(0, target.word_syllables - 2))
        target.duration = DURATION[target.kind] * scale / speed


# --- the units: every recording that can be each sound, and how it sounds by itself ---------------

def glued(key):
    """A sound that starts on the previous vowel (¨ai, ¨as, ¨ão...). The lonely ¨s doesn't."""
    return key.startswith(LINK) and key != LINK + 's'


def cut_path(cuts, filename):
    for extension in ('wav', 'mp3'):
        path = f"{cuts}/{filename}.{extension}"
        if os.path.exists(path):
            return path
    sys.exit(f"Error: {cuts}/{filename} not found, run cut.py first.")


@lru_cache(maxsize=None)
def load_cut(cuts, filename):
    return AudioSegment.from_file(cut_path(cuts, filename))


def trim(audio, key, preutterance):
    """The part of a cut file that gets played, and how many ms at its start are still the previous vowel."""
    lead = 0
    if glued(key):
        # it starts on the previous vowel, keep just enough of it to crossfade
        start = int(max(0, preutterance - KEEP_LEAD))
        audio, lead = audio[start:], preutterance - start
    else:
        start = detect_leading_silence(audio, silence_threshold=audio.max_dBFS - 30, chunk_size=5)
        audio = audio[max(0, start - 10):]
    # word endings fade to silence, which leaves holes in the middle of a phrase
    end = detect_leading_silence(audio.reverse(), silence_threshold=audio.max_dBFS - 35, chunk_size=5)
    if end < len(audio):
        audio = audio[:len(audio) - max(0, end - 10)]
    return audio, lead


def samples(audio):
    """The audio as mono floats between -1 and 1."""
    x = np.array(audio.set_channels(1).get_array_of_samples(), dtype=float)
    return x / (1 << (8 * audio.sample_width - 1))


def frames_db(x, rate):
    """Loudness (dBFS) of every FRAME_MS frame."""
    hop = rate * FRAME_MS // 1000
    count = len(x) // hop
    rms = np.sqrt(np.mean(x[:count * hop].reshape(count, hop) ** 2, axis=1))
    return 20 * np.log10(rms + 1e-9)


def frames_f0(x, rate, db):
    """Pitch (Hz) of every FRAME_MS frame, 0 where it's not voiced. YIN (de Cheveigné & Kawahara 2002)
    over 25 ms windows, which doesn't jump octaves like plain autocorrelation does."""
    hop, width = rate * FRAME_MS // 1000, rate * 25 // 1000
    low, high = rate // F0_RANGE[1], rate // F0_RANGE[0]
    size = width + high + 1
    padded = np.concatenate([x, np.zeros(size)])
    frames = padded[np.arange(len(db))[:, None] * hop + np.arange(size)]
    # difference function d(tau) = sum (x[j] - x[j + tau])^2, from the energies and the cross-correlation
    n = 1 << (2 * size - 1).bit_length()
    cross = np.fft.irfft(np.conj(np.fft.rfft(frames[:, :width], n)) * np.fft.rfft(frames, n), n)[:, :high + 1]
    energy = np.cumsum(np.pad(frames ** 2, ((0, 0), (1, 0))), axis=1)
    shifted = energy[:, width:width + high + 1] - energy[:, :high + 1]
    d = energy[:, width:width + 1] + shifted - 2 * cross
    # cumulative mean normalized difference: dips well below 1 at the period
    cmnd = np.ones_like(d)
    cmnd[:, 1:] = d[:, 1:] * np.arange(1, high + 1) / (np.cumsum(d[:, 1:], axis=1) + 1e-12)
    window = cmnd[:, low:]
    # the first dip under the threshold (the deepest one can be an octave too low), at its lowest point
    first = np.where((window < 0.15).any(axis=1), (window < 0.15).argmax(axis=1), window.argmin(axis=1))
    lag = np.array([f + window[i, f:f + f // 2 + 2].argmin() for i, f in enumerate(first)])
    voiced = (window[np.arange(len(lag)), lag] < 0.3) & (db > db.max() - 30)
    return np.where(voiced, rate / (low + lag), 0.0)


def level_db(db):
    """How loud something is: the median of its frames that aren't silence."""
    db = np.asarray(db)
    if not len(db):
        return -100.0
    return float(np.median(db[db > db.max() - 35]))


@dataclass
class Unit:
    """One recording that can be a sound, with its pitch and loudness measured frame by frame (as played)."""
    key: str
    filename: str
    fit: float          # how well it matches the sound (make_csv.py's score, higher = better)
    preutterance: float
    lead: float         # ms at its start that are still the previous vowel
    f0: np.ndarray      # pitch of every frame, 0 = unvoiced
    db: np.ndarray      # loudness of every frame

    @property
    def bank(self):
        return self.filename.split('/')[0]

    @property
    def duration(self):
        """How long its own sound lasts (ms) without stretching anything."""
        return max(len(self.db) * FRAME_MS - self.lead, FRAME_MS)

    def own(self, duration):
        """The frames of its own sound that get played for a target this long."""
        start = round(self.lead / FRAME_MS)
        return slice(start, start + max(1, round(duration / FRAME_MS)))

    def pitch(self, frames):
        """Median pitch of the loud frames in there (the vowel: consonants and breaths are quieter
        and track badly), 0 if less than a third of them is voiced."""
        f0 = self.f0[frames][self.db[frames] > self.db.max() - 15]
        voiced = f0[f0 > 0]
        return float(np.median(voiced)) if len(voiced) and len(voiced) >= len(f0) / 3 else 0.0

    @property
    def note(self):
        """The pitch of its own sound (Hz, 0 = unvoiced): what gets shifted to the target pitch."""
        return self.pitch(self.own(self.duration))

    def level(self, frames):
        """Loudness of those frames: of the voiced ones (the vowel) if there are any, so a vowel + s
        isn't measured by its s, and of the ones that aren't silence if not (breaths, s)."""
        db, f0 = self.db[frames], self.f0[frames]
        return level_db(db[f0 > 0]) if (f0 > 0).any() else level_db(db)

    def natural(self, duration):
        """(pitch Hz or 0, length ms, loudness dB) of what gets played for a target this long."""
        return self.note, self.duration, self.level(self.own(duration))

    def start(self):
        """(pitch, loudness) where it starts, lead included: what the previous unit has to meet."""
        return self.pitch(slice(0, 3)), level_db(self.db[:3])

    def end(self, duration):
        """(pitch, loudness) where it gets cut for a target this long."""
        stop = self.own(duration).stop
        frames = slice(max(0, stop - 3), stop)
        return self.pitch(frames), level_db(self.db[frames])


class Units:
    """Every unit in units.csv, measured once and cached in <cuts>/_features.json."""

    def __init__(self, filename, cuts):
        try:
            with open(filename, 'r', newline='', encoding='utf-8') as file:
                rows = list(csv.DictReader(file))
        except FileNotFoundError:
            sys.exit(f"Error: '{filename}' not found, run make_csv.py again.")

        cache_file = f"{cuts}/_features.json"
        try:
            with open(cache_file, encoding='utf-8') as file:
                cache = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            cache = {}

        self.candidates = {}
        measured = 0
        for row in rows:
            key, name, preutterance = row['ipa'], row['filename'], float(row['preutterance'])
            size = os.path.getsize(cut_path(cuts, name))
            entry = cache.get(f"{key}\t{name}")
            if not entry or entry['size'] != size or entry['preutterance'] != preutterance:
                if not measured:
                    print("measuring the pitch and loudness of every unit (only the first time)...", file=sys.stderr)
                entry = self.measure(cuts, key, name, preutterance, size)
                cache[f"{key}\t{name}"] = entry
                measured += 1
            unit = Unit(key, name, float(row['fit']), preutterance, entry['lead'],
                        np.array(entry['f0']), np.array(entry['db']))
            self.candidates.setdefault(key, []).append(unit)
        if measured:
            with open(cache_file, 'w', encoding='utf-8') as file:
                json.dump(cache, file)

        # the usual loudness of a unit: target loudness is relative to it
        self.level = float(np.median([unit.natural(300)[2] for units in self.candidates.values() for unit in units]))

    @staticmethod
    def measure(cuts, key, name, preutterance, size):
        audio, lead = trim(load_cut(cuts, name), key, preutterance)
        x = samples(audio)
        db = frames_db(x, audio.frame_rate)
        f0 = frames_f0(x, audio.frame_rate, db)
        return {'size': size, 'preutterance': preutterance, 'lead': lead,
                'f0': [round(f, 1) for f in f0.tolist()], 'db': [round(d, 1) for d in db.tolist()]}


# --- unit selection: total cost = target cost + concatenation cost -------------------------------

def modification_penalty(semitones):
    """Shifting the pitch by resampling also moves the formants and changes the length, so: free for a
    little, then growing with the square, and a flat extra past mod_max_factor."""
    cost = CONFIG['w_mod'] * max(0.0, semitones - CONFIG['mod_free_semitones']) ** 2
    if semitones > 12 * math.log2(CONFIG['mod_max_factor']):
        cost += CONFIG['mod_max_penalty']
    return cost


class Selector:
    def __init__(self, units):
        self.units = units

    def shift(self, target, unit):
        """Semitones the unit gets moved to hit the target pitch (0 = it's played as it is)."""
        if CONFIG['no_modification'] or not target.f0 or not unit.note:
            return 0.0
        limit = CONFIG['max_shift_semitones']
        return max(-limit, min(limit, 12 * math.log2(target.f0 / unit.note)))

    def target_cost(self, target, unit, best_fit):
        """How badly the unit fits its target: phonetic fit + prosodic mismatch + modification penalty."""
        f0, duration, level = unit.natural(target.duration)
        cost = CONFIG['w_phonetic'] * (best_fit - unit.fit)
        if f0 and target.f0:                      # unvoiced units (breaths, s) have no pitch to match
            semitones = abs(12 * math.log2(target.f0 / f0))
            cost += CONFIG['w_f0'] * semitones
            if not CONFIG['no_modification']:
                cost += modification_penalty(semitones)
        stretch = math.log2(duration / target.duration)  # in octaves: 1 = twice as long as needed
        cost += CONFIG['w_duration_long' if stretch > 0 else 'w_duration_short'] * abs(stretch)
        cost += CONFIG['w_energy'] * abs(level - self.units.level - target.energy)
        return cost

    def concat_cost(self, prev_target, prev, target, unit):
        """How badly two units join, as they will be played (after their pitch shift and normalization)."""
        cost = CONFIG['w_bank_switch'] if prev.bank != unit.bank else 0.0
        end_f0, end_db = prev.end(prev_target.duration)
        start_f0, start_db = unit.start()
        if end_f0 and start_f0:                   # voiced on both sides: pitch and loudness have to meet
            end_f0 *= 2 ** (self.shift(prev_target, prev) / 12)
            start_f0 *= 2 ** (self.shift(target, unit) / 12)
            cost += CONFIG['w_join_f0'] * abs(12 * math.log2(end_f0 / start_f0))
            end_level = end_db - prev.natural(prev_target.duration)[2] + prev_target.energy
            start_level = start_db - unit.natural(target.duration)[2] + target.energy
            cost += CONFIG['w_join_energy'] * abs(end_level - start_level)
        return cost

    def select(self, targets):
        """[(unit, target cost)], one per target: the cheapest path through all the candidates (Viterbi)."""
        columns = []
        for target in targets:
            if target.key not in self.units.candidates:
                sys.exit(f"Error: '{target.key}' is in {CSV_FILE} but not in {UNITS_FILE}, run make_csv.py again.")
            units = self.units.candidates[target.key]
            best_fit = max(unit.fit for unit in units)
            columns.append([(unit, self.target_cost(target, unit, best_fit)) for unit in units])

        totals = [cost for _, cost in columns[0]]
        back = []
        for i in range(1, len(targets)):
            row, pointers = [], []
            for unit, cost in columns[i]:
                options = [total + self.concat_cost(targets[i - 1], prev, targets[i], unit)
                           for total, (prev, _) in zip(totals, columns[i - 1])]
                k = min(range(len(options)), key=options.__getitem__)
                row.append(options[k] + cost)
                pointers.append(k)
            totals = row
            back.append(pointers)

        j = min(range(len(totals)), key=totals.__getitem__)
        path = [j]
        for pointers in reversed(back):
            j = pointers[j]
            path.append(j)
        return [columns[i][j] for i, j in enumerate(reversed(path))]


# --- units -> audio --------------------------------------------------------------------------------
# the way utau-to-mp3/voice-synth.py does it: every unit follows ONE pitch curve for the whole phrase with
# TD-PSOLA (so the pitch never steps at a join, and the voice doesn't get chipmunked like when resampling),
# and the joins are equal-power crossfades with the pitch periods lined up

def moving_average(x, width):
    padded = np.pad(x, (width // 2, width - width // 2 - 1), mode='edge')
    sums = np.cumsum(np.insert(padded, 0, 0))
    return (sums[width:] - sums[:-width]) / width


def smooth_gain(x, rate, levels):
    """levels: [(ms where a unit starts, its target loudness in dB)]. Each one holds until the next unit
    starts, and the whole curve is smoothed so the loudness never jumps. Then normalizes the loudness."""
    curve = np.zeros(len(x))
    for (start, db), (end, _) in zip(levels, levels[1:] + [(len(x) * 1000 / rate, 0)]):
        curve[int(start * rate / 1000):int(end * rate / 1000)] = db
    width = int(rate * CONFIG['gain_smoothing_ms'] / 1000)
    if width > 1:
        curve = moving_average(curve, width)
    x = x * 10 ** (curve / 20)
    # normalize the loudness, not the peak: one hot recording would make everything else quiet
    x *= 10 ** ((CONFIG['output_level_db'] - level_db(frames_db(x, rate))) / 20)
    # and softly limit whatever still goes past the ceiling (untouched below half of it)
    ceiling = 10 ** (CONFIG['output_peak_db'] / 20)
    knee = ceiling / 2
    over = np.abs(x) > knee
    x[over] = np.sign(x[over]) * (knee + (ceiling - knee) * np.tanh((np.abs(x[over]) - knee) / (ceiling - knee)))
    return x


def half_hann(n, rising):
    """Half of a Hann window, n samples. A rising and a falling one add up to exactly 1."""
    rise = 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / max(n, 1))
    return rise if rising else 1.0 - rise


def find_epochs(x, rate, f0):
    """Pitch marks: one per period where it's voiced (on the period's peak, so they all have the same
    phase), every 5 ms where it isn't. f0 is the unit's pitch track, one value per FRAME_MS frame."""
    hop, center = rate * FRAME_MS // 1000, rate * 25 // 2000  # frames_f0 looks at 25 ms from the frame start
    epochs, voiced = [], []
    t = 0.0
    while t < len(x) - 1:
        k = int(min(max(round((t - center) / hop), 0), len(f0) - 1)) if len(f0) else 0
        if len(f0) and f0[k] > 0:
            period = rate / f0[k]
            lo = int(max(t - period / 4, epochs[-1] + period / 2 if epochs else 0))
            hi = int(min(t + period / 4, len(x) - 1)) + 1
            if hi > lo:
                t = lo + int(np.argmax(x[lo:hi]))      # snap to the peak
            epochs.append(int(t))
            voiced.append(True)
            t = epochs[-1] + period
        else:
            epochs.append(int(t))
            voiced.append(False)
            t += rate / 200
    epochs, first = np.unique(epochs, return_index=True)
    return epochs, np.array(voiced)[first]


def psola(x, epochs, voiced, ratio_at):
    """TD-PSOLA, same length and new pitch: every pitch period is cut out around its mark with a Hann window
    and added back closer together (higher) or further apart (lower). ratio_at(sample) > 1 raises the pitch
    there. Unvoiced parts are copied as they are."""
    if len(epochs) < 2:
        return x.copy()
    widest = int(np.max(np.diff(epochs))) + 2
    y = np.zeros(len(x) + 2 * widest)
    weight = np.zeros_like(y)
    t = float(epochs[0])
    while t < len(x):
        k = int(min(np.searchsorted(epochs, t), len(epochs) - 1))  # the analysis mark closest to t
        if k > 0 and t - epochs[k - 1] < epochs[k] - t:
            k -= 1
        left = epochs[k] - epochs[k - 1] if k > 0 else epochs[1] - epochs[0]
        right = epochs[k + 1] - epochs[k] if k + 1 < len(epochs) else left
        lo, hi = epochs[k] - left, epochs[k] + right
        period = np.zeros(left + right)
        period[max(lo, 0) - lo:min(hi, len(x)) - lo] = x[max(lo, 0):min(hi, len(x))]
        window = np.concatenate([half_hann(left, True), half_hann(right, False)])
        c = int(round(t)) + widest
        y[c - left:c + right] += period * window
        weight[c - left:c + right] += window
        t += right / (ratio_at(t) if voiced[k] else 1.0)
    y, weight = y[widest:widest + len(x)], weight[widest:widest + len(x)]
    return y / np.where(weight > 0.15, weight, 1.0)


def best_lag(out, start, head, max_lag):
    """How many samples to move the new clip so its first periods line up with what's already playing."""
    width = len(head)
    lo, hi = start - max_lag, start + max_lag + width
    if max_lag < 1 or lo < 0 or hi > len(out) or width < 8:
        return 0
    ref = out[lo:hi]
    similarity = np.correlate(ref, head, mode='valid')
    energy = np.sqrt(np.convolve(ref ** 2, np.ones(width), mode='valid')) + 1e-9
    return int(np.argmax(similarity / energy)) - max_lag


def crossfade_ms(prev, target):
    """How much a unit overlaps the one before it, this sets the rhythm."""
    if prev.kind == 'h':
        return 20                                  # the consonant comes right after the hiss, without covering it
    if prev.kind in ('r', 'rr'):
        return 40                                  # the next sound comes in over the end of the breath
    if glued(target.key):
        return 50
    if target.kind in ('tonic', 'last tonic') and not target.key.startswith(LINK) and prev.kind in SYLLABLE_KINDS:
        return 30                                  # "ro-í": a vowel right after a vowel, no gap between them
    return 10 if target.kind == 'cluster' else 15


class Singer:
    def __init__(self, sounds, cuts):
        self.sounds = sounds
        self.cuts = cuts

    def clip(self, unit):
        """The played part of a unit's file as float samples, and its lead (ms)."""
        audio, lead = trim(load_cut(self.cuts, unit.filename), unit.key, unit.preutterance)
        return samples(audio.set_frame_rate(SAMPLE_RATE)), lead

    def phrase(self, targets, chosen, fades):
        """One phrase as float samples, and the sample where each unit starts in it."""
        ms = SAMPLE_RATE / 1000

        # where everything goes: each unit starts its crossfade that much before the previous one ends
        plan = []
        end = 0
        for i, (target, (unit, _)) in enumerate(zip(targets, chosen)):
            x, lead = self.clip(unit)
            f0, skip = unit.f0, 0                  # its pitch track, and how many frames of it are skipped
            length = target.duration
            if target.kind in SYLLABLE_KINDS and glued(unit.key):
                length += lead                     # a vowel-to-vowel link is the whole syllable
            if target.kind == 'h' and (unit.f0 > 0).any():
                # the Brasília r: a bit of the vowel to blend with, then the hiss, whatever the file's vowel length
                voice_end = int(np.nonzero(unit.f0 > 0)[0].max()) + 1
                skip = max(0, voice_end - HISS_VOWEL_MS // FRAME_MS)
                x, f0, lead = x[int(skip * FRAME_MS * ms):], unit.f0[skip:], max(0, lead - skip * FRAME_MS)
                length = (voice_end - skip) * FRAME_MS + HISS_MS * target.duration / DURATION['h']
            length = max(1, min(len(x), int(length * ms)))
            overlap = 0 if i == 0 else max(0, min(int(crossfade_ms(targets[i - 1], target) * ms), end - 1, length - 1))
            plan.append((target, unit, x, f0, skip, lead, length, end - overlap, overlap))
            end = end - overlap + length

        # one pitch curve for the whole phrase, through the target pitch in the middle of every unit
        times = [start + (lead * ms + length) / 2 for _, _, _, _, _, lead, length, start, _ in plan]
        semitones = [12 * math.log2(target.f0) for target, *_ in plan]

        def curve(sample):
            """Target pitch (Hz) at a sample of the phrase."""
            return 2 ** (np.interp(sample, times, semitones) / 12)

        margin = int(CONFIG['render_margin_ms'] * ms)
        out = np.zeros(end + 2 * margin)
        starts = []
        prev_end, prev_voiced = 0, False
        for i, ((target, unit, x, f0, skip, lead, length, start, overlap), fade) in enumerate(zip(plan, fades)):
            fades_out = fade or target.kind in ('breath', 'release', 'r')
            # past its end it goes on a bit, so the next unit has something to fade in over
            y = x[:length if fades_out or i == len(plan) - 1 else length + margin].astype(float)

            if not CONFIG['no_modification'] and unit.note:
                epochs, voiced = find_epochs(y, SAMPLE_RATE, f0)
                limit = CONFIG['max_shift_semitones']

                def ratio_at(t, start=start, note=unit.note):
                    shift = 12 * math.log2(curve(start + t) / note)
                    return 2 ** (max(-limit, min(limit, shift)) / 12)
                y = psola(y, epochs, voiced, ratio_at)

            # every unit at the same loudness, the target loudness comes later as one smooth curve (smooth_gain)
            gain = CONFIG['unit_level_db'] - unit.level(slice(skip, skip + math.ceil(length / ms / FRAME_MS)))
            y *= 10 ** (np.clip(gain, -CONFIG['max_unit_gain_db'], CONFIG['max_unit_gain_db']) / 20)
            if target.kind in ('breath', 'h') and (f0 > 0).any():
                # the exhale (where the voice stops): turned down at the end of a phrase, so it's a soft sigh,
                # and up in the Brasília r, so it hisses
                exhale = (int(np.nonzero(f0 > 0)[0].max()) + 1) * FRAME_MS * int(ms)
                if exhale < len(y):
                    exhale_gain = EXHALE_GAIN if target.kind == 'breath' else HISS_GAIN
                    ramp = min(len(y) - exhale, int(30 * ms))
                    y[exhale:exhale + ramp] *= 10 ** (np.linspace(0, exhale_gain, ramp) / 20)
                    y[exhale + ramp:] *= 10 ** (exhale_gain / 20)
            if fades_out:
                # breath: it already dies out, this only smooths it. r: the breath trails off
                n = int(length * {'breath': 0.5, 'release': 0.6, 'r': 0.25}.get(target.kind, 0.4))
                y[length - n:length] *= half_hann(n, False)

            s = start
            if i == 0 or prev_end <= s:
                n = min(len(y), int(CONFIG['edge_fade_ms'] * ms))
                y[:n] *= half_hann(n, True)
            else:
                head_voiced = f0[min(len(f0) - 1, int(overlap / ms / FRAME_MS))] > 0
                if prev_voiced and head_voiced:    # line up the pitch periods, moving it by up to half a period
                    period = int(SAMPLE_RATE / (unit.note if CONFIG['no_modification'] and unit.note else curve(s)))
                    s += best_lag(out, s, y[:max(overlap, 2 * period)], period // 2)
                smooth = int(CONFIG['smooth_fade_ms'] * ms)
                if head_voiced:                    # into a vowel, m, n, l...: a long blend, like coarticulation
                    fade_out = fade_in = max(overlap, smooth)
                else:                              # into s, t, k, p...: the vowel decays, the consonant stays crisp
                    fade_out = max(overlap, int(smooth * CONFIG['unvoiced_fade_ratio']))
                    fade_in = max(overlap, 1)
                fade_out = max(1, min(fade_out, prev_end - s))
                fade_in = max(1, min(fade_in, len(y)))
                out[s:s + fade_out] *= np.cos((np.arange(fade_out) + 0.5) / fade_out * np.pi / 2)
                out[s + fade_out:prev_end] = 0     # the previous unit's extra bit, past its fade
                y[:fade_in] *= np.sin((np.arange(fade_in) + 0.5) / fade_in * np.pi / 2)
            out[s:s + len(y)] += y
            starts.append(s)
            prev_end = s + len(y)
            prev_voiced = f0[min(len(f0) - 1, max(0, int(length / ms / FRAME_MS) - 1))] > 0

        out = out[:starts[-1] + plan[-1][6]]
        n = min(len(out), int(40 * ms))
        out[len(out) - n:] *= half_hann(n, False)
        return out, starts

    def sing(self, phrases, planner, selector, pitch, speed, debug=False):
        """The utterance, and for every voiced unit (semitones from its own pitch to the target, semitones shifted)."""
        ms = SAMPLE_RATE / 1000
        pieces = [np.zeros(int(80 * ms))]
        position = len(pieces[0])
        levels = []   # (ms where each unit starts in the output, its target loudness)
        shifts = []
        for words, punctuation in phrases:
            targets, shown = planner.phrase(words)
            print(' '.join('|'.join(self.sounds[key][0] if key in self.sounds else key for key in keys)
                           for keys in shown if keys) + punctuation)
            if targets:
                predict_prosody(targets, punctuation, pitch, speed)
                chosen = selector.select(targets)
                # no fading if the next word glues to this vowel ("gato e" -> ga|tu ¨ui)
                fades = [target.fade and not (i + 1 < len(targets) and glued(targets[i + 1].key))
                         for i, target in enumerate(targets)]
                for target, (unit, cost) in zip(targets, chosen):
                    shift = selector.shift(target, unit)
                    if unit.note and target.f0:
                        shifts.append((12 * math.log2(target.f0 / unit.note), shift))
                    if debug:
                        f0, duration, level = unit.natural(target.duration)
                        others = len(selector.units.candidates[target.key]) - 1
                        print(f"  {self.sounds[target.key][0] if target.key in self.sounds else target.key:6} "
                              f"{unit.filename:24} f0 {target.f0:3.0f}/{f0:3.0f} Hz shift {shift:+5.1f} st  "
                              f"dur {target.duration:3.0f}/{duration:4.0f} ms  "
                              f"energy {target.energy:+4.1f}/{level - selector.units.level:+4.1f} dB  "
                              f"cost {cost:4.2f}" + (f"  ({others} other{'s' * (others > 1)})" if others else ''))
                audio, starts = self.phrase(targets, chosen, fades)
                levels += [((position + start) / ms, target.energy) for start, target in zip(starts, targets)]
                pieces.append(audio)
                position += len(audio)
            pieces.append(np.zeros(int(PAUSES.get(punctuation, 300) * ms)))
            position += len(pieces[-1])
        x = smooth_gain(np.concatenate(pieces), SAMPLE_RATE, levels)
        audio = AudioSegment((x * 32767).astype(np.int16).tobytes(), frame_rate=SAMPLE_RATE, sample_width=2, channels=1)
        return audio, shifts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Make Kasane Teto speak brazilian portuguese.")
    parser.add_argument('text', nargs='*', help="what Teto should say (default: read from stdin)")
    parser.add_argument('-o', '--output', default='teto', help="output file name, without extension (default: teto)")
    parser.add_argument('--wav', action='store_true', help="save as .wav (default)")
    parser.add_argument('--mp3', action='store_true', help="save as .mp3 (needs ffmpeg)")
    parser.add_argument('-p', '--play', action='store_true', help="play it after saving")
    parser.add_argument('--cuts', default=None, help="folder with the cut files (default: wav_cut, or mp3_cut)")
    parser.add_argument('--pitch', type=float, default=300, help="the note Teto speaks around, in Hz (default: 300, the japanese bank sings ~308)")
    parser.add_argument('--speed', type=float, default=1.0, help="speech speed, 1.2 is 20%% faster (default: 1)")
    parser.add_argument('--accent', choices=('brasilia', 'goiano'), default=ACCENT,
                        help=f"how the r closing a syllable sounds (default: {ACCENT})")
    parser.add_argument('--no-modification', action='store_true',
                        help="A/B test: pick the units by prosody but play them at their own pitch")
    parser.add_argument('--config', help="JSON file overriding any of the settings in CONFIG")
    parser.add_argument('--debug', action='store_true', help="print the unit picked for each sound and how much it's shifted")
    args = parser.parse_args()

    if args.config:
        with open(args.config, encoding='utf-8') as file:
            overrides = json.load(file)
        unknown = sorted(set(overrides) - set(CONFIG))
        if unknown:
            sys.exit(f"Error: unknown setting(s) in {args.config}: {', '.join(unknown)}")
        CONFIG.update(overrides)
    if args.no_modification:
        CONFIG['no_modification'] = True
    ACCENT = args.accent

    text = ' '.join(args.text) if args.text else sys.stdin.read()
    cuts = args.cuts or ('wav_cut' if os.path.isdir('wav_cut') else 'mp3_cut')
    sounds = open_csv(CSV_FILE)
    selector = Selector(Units(UNITS_FILE, cuts))

    audio, shifts = Singer(sounds, cuts).sing(split_text(text), Planner(sounds), selector, args.pitch, args.speed, args.debug)
    if args.debug and shifts:
        # the A/B numbers: how far the picked units are from the target pitch, and how much of that got shifted
        off, moved = [abs(o) for o, _ in shifts], [abs(s) for _, s in shifts]
        print(f"distance to target pitch: mean {sum(off) / len(off):.2f} st, max {max(off):.1f} st | "
              f"shifted: mean {sum(moved) / len(moved):.2f} st, max {max(moved):.1f} st, "
              f"{sum(m > 12 * math.log2(CONFIG['mod_max_factor']) for m in moved)} units past x{CONFIG['mod_max_factor']:g}")

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
