"""
Builds teto.csv: every pt-BR syllable we can make with Teto, pointing to the
cut file that sounds the most like it in a Brasília/Goiás accent
("ti" is [tʃi], "rr" and a coda "r" are the throat [h], a coda "l" is [w], ...).

Sounds that have to be glued to their neighbours carry LINK on the glued side:
    ¨ai  -> starts on the "a" the previous syllable ended with, then goes to "i"
    ¨as  -> starts on the "a" the previous syllable ended with, ends in "s" + silence
    ¨a   -> starts on the "a" the previous syllable ended with, fades to silence
"""
import csv
import cut

LINK = '¨'  # the trema is dead in pt-BR orthography, so it's free real estate
CSV_FILE = 'teto.csv'
ALL = 'aeéioóu'

# pt-BR vowel -> (IPA, X-SAMPA, english spelling)
VOWELS = {
    'a': ('a', 'a', 'ah'),
    'e': ('e', 'e', 'ay'),
    'é': ('ɛ', 'E', 'eh'),
    'i': ('i', 'i', 'ee'),
    'o': ('o', 'o', 'oh'),
    'ó': ('ɔ', 'O', 'aw'),
    'u': ('u', 'u', 'oo'),
}

# pt-BR consonant (IPA) -> (X-SAMPA, english spelling, [(pt-BR spelling, vowels it goes with)])
CONSONANTS = {
    '': ('', '', [('', ALL)]),
    'b': ('b', 'b', [('b', ALL)]),
    'k': ('k', 'k', [('c', 'aoóu'), ('qu', 'eéi')]),
    'd': ('d', 'd', [('d', 'aeéoóu')]),
    'dʒ': ('dZ', 'j', [('d', 'i')]),
    'f': ('f', 'f', [('f', ALL)]),
    'g': ('g', 'g', [('g', 'aoóu'), ('gu', 'eéi')]),
    'ʒ': ('Z', 'zh', [('j', ALL), ('g', 'eéi')]),
    'h': ('h', 'h', [('rr', ALL)]),
    'l': ('l', 'l', [('l', ALL)]),
    'ʎ': ('L', 'ly', [('lh', ALL)]),
    'm': ('m', 'm', [('m', ALL)]),
    'n': ('n', 'n', [('n', ALL)]),
    'ɲ': ('J', 'ny', [('nh', ALL)]),
    'p': ('p', 'p', [('p', ALL)]),
    'ɾ': ('4', 'r', [('r', ALL)]),
    's': ('s', 's', [('s', ALL), ('ss', ALL), ('c', 'eéi'), ('ç', 'aoóu')]),
    't': ('t', 't', [('t', 'aeéoóu')]),
    'tʃ': ('tS', 'ch', [('t', 'i'), ('tch', ALL)]),
    'v': ('v', 'v', [('v', ALL)]),
    'z': ('z', 'z', [('z', ALL)]),
    'ʃ': ('S', 'sh', [('ch', ALL), ('x', ALL)]),
}

# --- how well each voicebank sound matches a pt-BR sound (3 = spot on, 1 = meh) ---

# standard (japanese) bank: a i u e o rows
JP_ROW = 'aiueo'
JP_KANA = {
    '': ['あ', 'い', 'う', 'え', 'お'],
    'k': ['か', 'き', 'く', 'け', 'こ'],
    'g': ['が', 'ぎ', 'ぐ', 'げ', 'ご'],
    's': ['さ', 'すぃ', 'す', 'せ', 'そ'],
    'ɕ': ['しゃ', 'し', 'しゅ', 'しぇ', 'しょ'],
    'z': ['ざ', 'ずぃ', 'ず', 'ぜ', 'ぞ'],
    'dʑ': ['じゃ', 'じ', 'じゅ', 'じぇ', 'じょ'],
    't': ['た', 'てぃ', 'とぅ', 'て', 'と'],
    'tɕ': ['ちゃ', 'ち', 'ちゅ', 'ちぇ', 'ちょ'],
    'd': ['だ', 'でぃ', 'どぅ', 'で', 'ど'],
    'n': ['な', None, 'ぬ', 'ね', 'の'],  # に is [ɲi]
    'ɲ': ['にゃ', 'に', 'にゅ', 'にぇ', 'にょ'],
    'h': ['は', None, None, 'へ', 'ほ'],  # ひ is [çi], ふ is [ɸɯ]
    'ɸ': ['ふぁ', 'ふぃ', 'ふ', 'ふぇ', 'ふぉ'],
    'b': ['ば', 'び', 'ぶ', 'べ', 'ぼ'],
    'p': ['ぱ', 'ぴ', 'ぷ', 'ぺ', 'ぽ'],
    'm': ['ま', 'み', 'む', 'め', 'も'],
    'ɾ': ['ら', 'り', 'る', 'れ', 'ろ'],
    'ɾʲ': ['りゃ', 'り', 'りゅ', 'りぇ', 'りょ'],
    'v': ['ヴァ', 'ヴィ', None, 'ヴェ', 'ヴォ'],
}
JP_CONSONANT_FIT = {
    '': {'': 3}, 'b': {'b': 3}, 'k': {'k': 3}, 'd': {'d': 3}, 'g': {'g': 3},
    'm': {'m': 3}, 'n': {'n': 3}, 'p': {'p': 3}, 't': {'t': 3}, 's': {'s': 3},
    'z': {'z': 2}, 'ʃ': {'ɕ': 2}, 'ʒ': {'dʑ': 1}, 'tʃ': {'tɕ': 2}, 'dʒ': {'dʑ': 2},
    'f': {'ɸ': 1}, 'v': {'v': 1}, 'l': {'ɾ': 1}, 'ɾ': {'ɾ': 3}, 'h': {'h': 3},
    'ɲ': {'ɲ': 3}, 'ʎ': {'ɾʲ': 1},
}
# japanese う is unrounded, え/お sit between pt-BR's closed and open e/o
JP_VOWEL_FIT = {'a': ('a', 3), 'e': ('e', 3), 'é': ('e', 2), 'i': ('i', 3),
                'o': ('o', 3), 'ó': ('o', 2), 'u': ('u', 1)}

# english bank, X-SAMPA aliases. its "e" is the same recording as "E" and its "o" is "oU"
EN_CONSONANT_FIT = {
    '': {'': 3}, 'b': {'b': 3}, 'k': {'k': 3}, 'd': {'d': 3}, 'g': {'g': 3},
    'm': {'m': 3}, 'n': {'n': 3}, 'p': {'p': 3}, 't': {'t': 3}, 's': {'s': 3},
    'z': {'z': 3}, 'ʃ': {'S': 3}, 'ʒ': {'Z': 3}, 'tʃ': {'tS': 3}, 'dʒ': {'dZ': 3},
    'f': {'f': 3}, 'v': {'v': 3}, 'l': {'l': 3}, 'ɾ': {'r': 1}, 'h': {'h': 3},
    'ɲ': {'nj': 2}, 'ʎ': {'lj': 2, 'l': 1},
}
EN_VOWEL_FIT = {'a': {'A': 2}, 'e': {'e': 2, 'E': 1}, 'é': {'E': 3, 'e': 2},
                'i': {'i': 3, 'I': 2}, 'o': {'oU': 1, 'O': 1}, 'ó': {'O': 3},
                'u': {'u': 3, 'U': 2}}

# pt-BR diphthong -> (english bank alias, IPA, X-SAMPA, english spelling after a consonant, alone)
DIPHTHONGS = {
    'ai': ('aI', 'aj', 'aj', 'ye', 'eye'),
    'au': ('aU', 'aw', 'aw', 'ow', 'ow'),
    'ei': ('eI', 'ej', 'ej', 'ayee', 'ayee'),
    'oi': ('OI', 'oj', 'oj', 'oy', 'oy'),
    'ói': ('OI', 'ɔj', 'Oj', 'oy', 'oy'),
}

# coda -> (english bank consonant, IPA, X-SAMPA, english spelling), nasals are handled apart
CODAS = {
    's': ('s', 's', 's', 's'),
    'z': ('s', 's', 's', 's'),       # "paz" is [pas]
    'l': ('w', 'w', 'w', 'w'),       # "sal" is [saw]
}
# the coda "r" is the throat one, [h] ("mar" [mah]). the english bank only has [h] before a vowel,
# but the extra bank has every vowel ending in a breath ("ab -"), and that's exactly it
R_CODA = ('h', 'h', 'h')  # IPA, X-SAMPA, english spelling
# word final nasals -> (IPA, X-SAMPA, english spelling, {english bank vowel before "N-": fit})
NASALS = {
    'ã': ('ɐ̃', '6~', 'ung', {'V': 3, 'A': 2}),
    'an': ('ɐ̃', '6~', 'ung', {'V': 3, 'A': 2}),
    'am': ('ɐ̃w̃', '6~w~', 'owng', {'aU': 3}),    # "falam"
    'ão': ('ɐ̃w̃', '6~w~', 'owng', {'aU': 3}),
    'ãe': ('ɐ̃j̃', '6~j~', 'aing', {'aI': 3}),
    'en': ('ẽ', 'e~', 'eng', {'e': 2, 'E': 1}),
    'em': ('ẽj̃', 'e~j~', 'ayng', {'eI': 3}),    # "também"
    'in': ('ĩ', 'i~', 'eeng', {'i': 3, 'I': 2}),
    'im': ('ĩ', 'i~', 'eeng', {'i': 3, 'I': 2}),
    'on': ('õ', 'o~', 'ong', {'oU': 2, 'O': 1}),
    'om': ('õ', 'o~', 'ong', {'oU': 2, 'O': 1}),
    'õe': ('õj̃', 'o~j~', 'oyng', {'OI': 3}),
    'un': ('ũ', 'u~', 'oong', {'u': 3, 'U': 2}),
    'um': ('ũ', 'u~', 'oong', {'u': 3, 'U': 2}),
}

# at the end of a word an unstressed e/o goes [ɪ]/[ʊ] ("leite" [ˈlejtʃɪ], "gatos" [ˈgatʊs])
# -> (IPA, X-SAMPA, english spelling, {english bank vowel: fit}, (japanese vowel, fit))
REDUCED = {
    'e': ('ɪ', 'I', 'ih', {'I': 3, 'i': 2}, ('i', 2)),
    'o': ('ʊ', 'U', 'uh', {'U': 3, 'u': 2}, ('u', 2)),
}
REDUCED_CODAS = 's'  # "-es", "-os" (plurals); "-er", "-or", "-el"... are stressed

# when two banks tie, the earlier one wins (standard sounds the most natural)
BANK_ORDER = ['standard', 'english', 'edge-continuous', 'extra']


def load_outputs():
    """{output folder: {cut file name: oto entry}} for the banks used here."""
    outputs = {}
    for bank in ('standard', 'english', 'edge', 'extra'):
        for folder, output in cut.bank_folders(bank):
            if output in BANK_ORDER:
                oto = cut.parse_oto_ini_with_types(f"{folder}/oto.ini")
                outputs[output] = {entry['output']: entry for entry in cut.unique_entries(oto)}
    return outputs


def best(candidates, outputs):
    """Pick the (score, bank, alias) that exists and fits best, standard bank first on ties."""
    found = [(score, bank, cut.safe_filename(alias)) for score, bank, alias in candidates
             if cut.safe_filename(alias) in outputs[bank]]
    if not found:
        return None
    score, bank, name = max(found, key=lambda c: (c[0], -BANK_ORDER.index(c[1])))
    return f"{bank}/{name}"


def syllable_candidates(consonant, vowel):
    candidates = []
    jp_vowel, vowel_fit = JP_VOWEL_FIT[vowel]
    for label, consonant_fit in JP_CONSONANT_FIT[consonant].items():
        kana = JP_KANA[label][JP_ROW.index(jp_vowel)]
        if kana:
            candidates.append((2 * consonant_fit + vowel_fit, 'standard', kana))
    for en_consonant, consonant_fit in EN_CONSONANT_FIT[consonant].items():
        for en_vowel, vowel_fit in EN_VOWEL_FIT[vowel].items():
            candidates.append((2 * consonant_fit + vowel_fit, 'english', f"- {en_consonant}{en_vowel}"))
    return candidates


def spellings(consonant, first_vowel):
    return [spelling for spelling, vowels in CONSONANTS[consonant][2] if first_vowel in vowels]


def rows(outputs):
    # plain syllables: "ba", "que", "tchau"... well, "tcha"
    for consonant, (xsampa, english, _) in CONSONANTS.items():
        for vowel, (v_ipa, v_xsampa, v_english) in VOWELS.items():
            filename = best(syllable_candidates(consonant, vowel), outputs)
            if not filename:
                continue
            for spelling in spellings(consonant, vowel):
                # english "g" before e/i turns soft, "gh" keeps it hard
                en = 'gh' if english == 'g' and vowel in 'eéi' else english
                yield spelling + vowel, en + v_english, consonant + v_ipa, xsampa + v_xsampa, filename

    # diphthongs, only the english bank has them
    for diphthong, (alias, d_ipa, d_xsampa, d_english, d_alone) in DIPHTHONGS.items():
        for consonant, (xsampa, english, _) in CONSONANTS.items():
            fits = EN_CONSONANT_FIT[consonant]
            candidates = [(fit, 'english', f"- {en_consonant}{alias}") for en_consonant, fit in fits.items() if fit >= 2]
            filename = best(candidates, outputs)
            if not filename:
                continue
            for spelling in spellings(consonant, diphthong[0]):
                yield (spelling + diphthong, english + (d_english if consonant else d_alone),
                       consonant + d_ipa, xsampa + d_xsampa, filename)

    # vowel to vowel: "¨ai" is the "a" of the last syllable sliding into "i"
    for first, (f_ipa, f_xsampa, f_english) in VOWELS.items():
        for second, (s_ipa, s_xsampa, s_english) in VOWELS.items():
            if first == second:
                continue
            # a sound that doesn't change vowel is not a transition, so skip those
            candidates = []
            for en_first, first_fit in EN_VOWEL_FIT[first].items():
                for en_second, second_fit in EN_VOWEL_FIT[second].items():
                    if en_first != en_second:
                        candidates.append((first_fit + second_fit, 'english', f"{en_first} {en_second}"))
            (jp_first, first_fit), (jp_second, second_fit) = JP_VOWEL_FIT[first], JP_VOWEL_FIT[second]
            if jp_first != jp_second:
                kana = JP_KANA[''][JP_ROW.index(jp_second)]
                candidates.append((first_fit + second_fit, 'edge-continuous', f"{jp_first} {kana}'"))
            filename = best(candidates, outputs)
            if filename:
                yield (LINK + first + second, LINK + f_english + s_english,
                       LINK + f_ipa + s_ipa, LINK + f_xsampa + s_xsampa, filename)

    # vowel fading out at the end of a word: "¨a"
    for vowel, (v_ipa, v_xsampa, v_english) in VOWELS.items():
        en_fits, (jp_vowel, jp_fit) = EN_VOWEL_FIT[vowel], JP_VOWEL_FIT[vowel]
        if vowel in REDUCED:
            v_ipa, v_xsampa, v_english, en_fits, (jp_vowel, jp_fit) = REDUCED[vowel]
        candidates = [(fit, 'english', f"{en_vowel} -") for en_vowel, fit in en_fits.items()]
        candidates.append((jp_fit, 'standard', f"* {JP_KANA[''][JP_ROW.index(jp_vowel)]}"))
        candidates.append((jp_fit, 'edge-continuous', f"{jp_vowel} R'"))
        filename = best(candidates, outputs)
        if filename:
            yield LINK + vowel, LINK + v_english, LINK + v_ipa, LINK + v_xsampa, filename

    # end of word codas: "¨as", "¨ar", "¨al"...
    for vowel in VOWELS:
        for coda, (en_coda, c_ipa, c_xsampa, c_english) in CODAS.items():
            v_ipa, v_xsampa, v_english = VOWELS[vowel]
            en_fits = EN_VOWEL_FIT[vowel]
            if vowel in REDUCED and coda in REDUCED_CODAS:
                v_ipa, v_xsampa, v_english, en_fits, _ = REDUCED[vowel]
            candidates = [(fit, 'english', f"{en_vowel} {en_coda}-") for en_vowel, fit in en_fits.items()]
            filename = best(candidates, outputs)
            if filename:
                yield (LINK + vowel + coda, LINK + v_english + c_english,
                       LINK + v_ipa + c_ipa, LINK + v_xsampa + c_xsampa, filename)
        v_ipa, v_xsampa, v_english = VOWELS[vowel]
        jp_vowel, fit = JP_VOWEL_FIT[vowel]
        filename = best([(fit, 'extra', f"{jp_vowel}b -")], outputs)
        if filename:
            yield (LINK + vowel + 'r', LINK + v_english + R_CODA[2],
                   LINK + v_ipa + R_CODA[0], LINK + v_xsampa + R_CODA[1], filename)

    # ...and the nasal ones: "¨an", "¨ão", "¨em"...
    for rime, (n_ipa, n_xsampa, n_english, fits) in NASALS.items():
        candidates = [(fit, 'english', f"{en_vowel} N-") for en_vowel, fit in fits.items()]
        filename = best(candidates, outputs)
        if filename:
            yield LINK + rime, LINK + n_english, LINK + n_ipa, LINK + n_xsampa, filename

    # the throat r at the start of a syllable ("rato", "carro"): the h of は/ほ is over in 20 ms, so a
    # real breath goes before the syllable. "rr¨" = it glues to whatever comes after it
    filename = best([(3, 'extra', "息1")], outputs)
    if filename:
        yield 'rr' + LINK, 'h' + LINK, 'h' + LINK, 'h' + LINK, filename

    # a lonely "s" after a coda that already used the vowel: "trens", "pers-pectiva"
    filename = best([(3, 'english', "- s")], outputs)
    if filename:
        yield LINK + 's', LINK + 's', LINK + 's', LINK + 's', filename


if __name__ == "__main__":
    outputs = load_outputs()
    with open(CSV_FILE, 'w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        # preutterance: where the sound's own "note" starts inside the file (ms). before that it's
        # still the previous syllable, so the text to speech knows how much of a ¨ sound to skip
        writer.writerow(['pt_br', 'english', 'ipa', 'xsampa', 'filename', 'preutterance'])
        count = 0
        for row in rows(outputs):
            bank, name = row[-1].split('/', 1)
            writer.writerow(row + (f"{outputs[bank][name]['preutterance']:g}",))
            count += 1
    print(f"{CSV_FILE}: {count} sounds")
