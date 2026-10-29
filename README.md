# utau-to-mp3
convert audio files from utau to singular aliases files.

Now it also downloads Kasane Teto's voicebanks by itself, because clicking 5 download buttons is too much work.

## Installation

1. Clone the repository or download the files.
2. Install required packages(using system packages as example):

```bash
# arch( btw ):
sudo pacman -S ffmpeg python-audioop-lts
yay -S python-pydub   # it lives in the AUR, sorry

# Ubuntu/Debian
sudo apt install python3-pydub ffmpeg

# Fedora
sudo dnf install python3-pydub ffmpeg

# anything else / i don't trust my distro
pip install pydub audioop-lts
```
`audioop-lts` is only needed on Python 3.13+ (they removed `audioop`, pydub cried). `ffmpeg` is only needed for mp3.
The text to speech also needs `numpy` (`pip install numpy`, `python-numpy`, `python3-numpy`).

## Usage

1. Run the script:
```bash
python3 cut.py
```
That's it. It will:
   - download the voicebanks into `voicebanks/` (only the first time, and if you already have the zip next to the script it uses that one);
   - unzip them (fixing the japanese file names that zip mangles);
   - read every `oto.ini` and cut one file per alias;
   - save them as `.wav`.

2. Want mp3? Use the tags:
```bash
python3 cut.py --mp3
```
You can use both at the same time:
```bash
python3 cut.py --wav --mp3
```

3. Only want some banks? Name them:
```bash
python3 cut.py english standard
```

| bank | voicebank | output folders |
|---|---|---|
| `standard` | [TETO-tandoku-100619.zip](https://kasaneteto.jp/assets/download/utau/TETO-tandoku-100619.zip) | `standard/` |
| `whisper` | [TETO-sasayakitan-100925.zip](https://kasaneteto.jp/assets/download/utau/TETO-sasayakitan-100925.zip) | `whisper/` |
| `extra` | [TETO-extra-100619.zip](https://kasaneteto.jp/assets/download/utau/TETO-extra-100619.zip) | `extra/` |
| `edge` | [TETO-edgeongen211010.zip](https://kasaneteto.jp/assets/download/utau/TETO-edgeongen211010.zip) | `edge/`, `edge-continuous/`, `edge-breath/` |
| `english` | [TETO-English-150401.zip](https://kasaneteto.jp/assets/download/utau/TETO-English-150401.zip) | `english/` (the one i do care about) |

Old school still works too: give it any folder that has an `oto.ini`:
```bash
python3 cut.py path/to/some/voicebank
```

4. Find your converted files in the new `<extension>_cut/<bank>/` folders:

The script will create audio files named after each alias in the oto.ini.
```
# example
wav_cut/
├── english/
│   ├── - baU.wav
│   ├── baU.wav
│   └── ...
├── standard/
│   ├── か.wav
│   └── ...
└── ...
```
5. Be happy, now you can be teto i guess...

## Checking the cuts

```bash
python3 check_cuts.py            # checks wav_cut/
python3 check_cuts.py mp3_cut    # or whatever folder
```
It looks at every file and complains if it's silent, too short, or has more than one sound in it (= the cut went wrong).
Last run: 2866/2877 look like a single sound, and the other 11 are supposed to be like that: lonely consonants
like `- k` (a "k" alone is just a click), consonant clusters like `- spr`, codas like `{ p-` (the "p" has silence before the pop),
and the breath sounds (`a R'`, `u R吸`).

## The CSV (teto.csv)

```bash
python3 make_csv.py
```
Makes `teto.csv` for the text to speech: every pt-BR syllable Teto can say, with the file that sounds the most like it.

| pt_br | english | ipa | xsampa | filename |
|---|---|---|---|---|
| ca | kah | ka | ka | standard/か |
| ti | chee | tʃi | tSi | english/- tSi |
| rra | hah | ha | ha | standard/は |
| ¨ão | ¨owng | ¨ɐ̃w̃ | ¨6~w~ | english/aU N- |

- `filename` is relative to `wav_cut/` or `mp3_cut/` and has no extension, so just glue `.wav` or `.mp3` on it.
- `preutterance` (extra column, ms, straight from the oto.ini) is where the sound's own note starts inside the file:
  a `¨` sound before that point is still the previous syllable. Each bank does it differently (`o い'` starts at 600 ms, `a い'` at 300),
  so don't guess it like i did ("roído" came out as "rro-o" because of that).
- When more than one file could be the syllable, it picks the one closest to Brazilian Portuguese with the
  Brasília/Goiás accent: "ti"/"di" are [tʃi]/[dʒi], "rr" is [h], "r" between vowels is the tap [ɾ] (japanese ら is perfect for that),
  coda "r" is the goiano [ɻ] in the middle of a word and the throat [h] at the end of it (that one comes from the `extra` bank,
  a vowel ending in a breath is exactly that), coda "l" is [w], coda "s" is [s] (not the carioca [ʃ]), and a final unstressed "e"/"o" is [ɪ]/[ʊ].
  The japanese bank comes first (`BANK_BONUS` in `make_csv.py`): one voice all the way through, like `voice-synth.py`,
  so l is ら, f is ふぁ, v is ヴァ, u is う, and a vowel sliding into another is the legato `* い` crossfaded from it.
  The english bank is only used for what japanese doesn't have: final s/l (`¨as`, `¨al`), nasal endings (`¨ão`, `¨em`),
  the goiano r in the middle of a word (`¨or¨`) and "rru" (ふ is not [hu]).
- `¨` (the trema, it's dead in portuguese anyway) marks a sound that has to be glued to the previous syllable:
  - `¨ai`: starts on the "a" the last syllable ended with, slides into "i";
  - `¨as`, `¨ar`, `¨ão`...: the end of a word, starts on the vowel of the last syllable;
  - `¨a`: the vowel fading out at the end of a word;
  - `¨ar¨`: `¨` on both sides, it also glues to the next syllable (the goiano r in the middle of a word: "porta" = `po|¨or¨|ta`).

  So "pão" is `pa` + `¨ão`, and "gatos" is `ga` + `to` + `¨os`.
- The plain syllables stop while the vowel is still sounding (that's how UTAU wants them), so overlap/crossfade them a bit instead of just sticking them together.
- Accents that don't change the sound (á, ê, í, ô, ú) are not in there, strip them before looking up.

It also makes `units.csv`: every file that could be each sound, not just the best one, with its `fit`
(how well it matches, higher = better). The text to speech picks between those by prosody.

## Teto to speech

Needs `teto.csv` and the cut files (`python3 cut.py english standard edge extra` is enough, the CSV only uses those).

```bash
python3 teto-to-speech.py "Olá, eu sou a Kasane Teto!"          # -> teto.wav
python3 teto-to-speech.py --mp3 "pão de queijo"                 # -> teto.mp3
python3 teto-to-speech.py --wav --mp3 -o goiás "trem bão, sô"   # -> goiás.wav and goiás.mp3
python3 teto-to-speech.py -p "agora ela fala"                    # saves AND plays it
python3 teto-to-speech.py --accent goiano "a porta"              # the goiano r instead of the Brasília one
echo "lê da entrada também" | python3 teto-to-speech.py          # no text? reads stdin
```
It prints how it split everything, so you can see what went wrong when it goes wrong:
```
o|la|¨a,
e|¨eu so ¨oa ca|za|ni te|tu|¨o!
```

What it does to sound like someone from Brasília/Goiás and not like a GPS:
- splits the words in syllables the portuguese way (diphthong vs hiatus: "pai" is one, "pa-ís" is two, "ca-ir", "ra-i-nha"...)
  and finds the stressed one (by the accent, or by how the word ends);
- reads it like we talk, not like we write: final unstressed "e"/"o" become [ɪ]/[ʊ] ("leite" = lei|ti, "gato" = ga|tu),
  "de"/"que"/"o" lean on the next word ([dʒɪ], [kɪ], [ʊ]), "t"/"d" before [i] are [tʃ]/[dʒ], "s" between vowels is [z], "r" at the start is [h],
  "ou" is just [o], and an "r" closing a syllable depends on `--accent` (like `voice-synth.py`):
  - `brasilia` (default): the hissing r, the vowel goes out in a breath ("porta" [ˈpɔhtɐ], "mar" [mah], the `extra` bank's
    vowel + breath), and a final -ar/-er/-ir loses it ("falar" = falá, "fazer" = fazê);
  - `goiano`: the english [ɻ] in the middle of the word ("porta" [ˈpɔɻtɐ], `¨or¨` in the CSV) and the throat [h] at the end of it
    ("mar" [mah], `¨ar`);
- the "r"/"rr" at the start of a syllable gets a real breath before it (`rr¨` in the CSV, the `extra` bank's 息1),
  because the h of は/ほ lasts 20 ms and "rato" came out as "ato";
- a stressed vowel right after another vowel gets its own sound ("roído" = rro|í|du, "saída" = sa|í|da),
  only the unstressed ones slide from the previous vowel;
- consonants without a vowel get the brazilian "i" ("ritmo" = ri|tchi|mo, "pneu" = pi|neu, "advogado" = a|dji|vo|ga|du),
  and "br/pr/tr/cl..." are a tiny consonant + the tap ("abraço" = a|ba|ra|su);
- glues everything with the `¨` sounds from the CSV: vowel to vowel even between words ("eu sou a" = e|¨eu so ¨oa),
  codas (¨as, ¨ar, ¨al), nasals (¨ão, ¨ãe, ¨em) and the last vowel fading out;
- rhythm: stressed syllables are longer and a bit louder, the ones after it are short and quiet (that's the brazilian rhythm),
  and the last syllable of a word, when it's not the stressed one, barely gets out: shorter, quieter, lower and fading
  ("gato" is GA-tu, not GA-TO). `,` and `.` make pauses, and before a pause the last vowel goes on and
  out in a breath (the `extra` bank's vowel + breath, quieter than the throat r so "fala" still isn't "falar"),
  that's the `…` you see in the output;
- tune: the phrase slowly goes down, a `.` falls at the end, a `?` goes up on the last stressed syllable, `!` goes up a bit;
- every piece follows one pitch curve over the whole phrase (`--pitch`, default 300 Hz, close to the japanese bank's own ~308).

`--speed 1.2` talks 20% faster, `--cuts mp3_cut` if you only cut mp3s.
Numbers aren't spoken yet, write them out.

### How the pieces are joined (less jumpy)

Copied from how `../utau-to-mp3/voice-synth.py` does it:
- the pitch is moved with TD-PSOLA (pitch periods moved closer or further apart), not by resampling, so the length and the
  formants stay (no chipmunk) and it follows one smooth curve, instead of every piece jumping to its own note; at most 5 semitones;
- equal-power crossfades: 40 ms into a voiced sound (vowel, m, n, l...), and into s/t/k/p the vowel fades out while the
  consonant comes in quick and crisp;
- where two voiced pieces meet, the new one is moved by up to half a period so their waveforms line up;
- every piece is scaled to the same loudness (at most ±12 dB), then the target loudness is one smooth curve over the whole thing.

### Unit selection

Instead of always taking the same file for each sound, it:
- predicts a target pitch, length and loudness for every piece (the rules above, plus: long words go a bit faster);
- measures every file in `units.csv` once (pitch and loudness every 10 ms, cached in `wav_cut/_features.json`);
- picks the files with the lowest total cost (Viterbi over the whole phrase):
  - target cost = how well the file fits the sound + how far its own pitch/length/loudness are from the target
    \+ a penalty for shifting it a lot (free up to 2 semitones, then growing with the square, and a lot more past an octave);
  - concatenation cost = pitch jump and loudness jump where two files meet + switching voicebank;
- scales every piece to the same loudness, then applies the target loudness as one smooth curve over the whole thing
  (no volume steps or clicks between pieces).

Don't expect miracles: each voicebank was recorded on basically one note (standard ~308 Hz, english ~279 Hz), and with
the japanese bank first there's rarely a real choice left. The costs mostly matter if you lower `BANK_BONUS`.

```bash
python3 teto-to-speech.py --debug "pão de queijo"            # which file for each piece, its pitch vs the target, the costs
python3 teto-to-speech.py --no-modification "pão de queijo"  # A/B: same picking, but no TD-PSOLA (each piece at its own note)
python3 teto-to-speech.py --config weights.json "..."        # override any setting, like {"w_f0": 1.0, "w_bank_switch": 0}
```
All the weights and fade lengths are in `CONFIG` at the top of `teto-to-speech.py`, with what each one does.
The versions before this are in `backup/` (see `backup/README.txt`).

## Notes
- Original WAV files remain unchanged
- MP3 or WAV files are trimmed according to oto.ini offset and cutoff values
- Aliases that would make the exact same file (like `あ` and `- あ`) are only saved once
- Characters windows hates in file names (`* ? : | ...`) become their fullwidth versions (`＊ あ`)
- The edge bank's oto.ini has `- るぉ'` starting at 4882 ms on a file that is shorter than that, so it's skipped
- Teto's license does not allow redistributing the voicebank, so the cut files and zips are in `.gitignore`. Don't upload them, cut your own.

ToDo:
  - ~~fix overlap sounds, like "- baU"( it does not cut right).~~ the cutoff math was wrong, it's fixed.
  - Check other audio extensions to see if it works on more than mp3 and wav.
  - ~~CSV file with consonant and overlap for each alias for later usage.~~ teto.csv.
  - fix some things.
  - become teto.
