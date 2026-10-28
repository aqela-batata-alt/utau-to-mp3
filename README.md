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
  Closed e/o come from the japanese bank, open é/ó, "u" and the consonants japanese doesn't have (l, f, v, ʃ, ʒ...) from the english one.
- `¨` (the trema, it's dead in portuguese anyway) marks a sound that has to be glued to the previous syllable:
  - `¨ai`: starts on the "a" the last syllable ended with, slides into "i";
  - `¨as`, `¨ar`, `¨ão`...: the end of a word, starts on the vowel of the last syllable;
  - `¨a`: the vowel fading out at the end of a word;
  - `¨ar¨`: `¨` on both sides, it also glues to the next syllable (the goiano r in the middle of a word: "porta" = `po|¨or¨|ta`).

  So "pão" is `pa` + `¨ão`, and "gatos" is `ga` + `to` + `¨os`.
- The plain syllables stop while the vowel is still sounding (that's how UTAU wants them), so overlap/crossfade them a bit instead of just sticking them together.
- Accents that don't change the sound (á, ê, í, ô, ú) are not in there, strip them before looking up.

## Teto to speech

Needs `teto.csv` and the cut files (`python3 cut.py english standard edge extra` is enough, the CSV only uses those).

```bash
python3 teto-to-speech.py "Olá, eu sou a Kasane Teto!"          # -> teto.wav
python3 teto-to-speech.py --mp3 "pão de queijo"                 # -> teto.mp3
python3 teto-to-speech.py --wav --mp3 -o goiás "trem bão, sô"   # -> goiás.wav and goiás.mp3
python3 teto-to-speech.py -p "agora ela fala"                    # saves AND plays it
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
  "ou" is just [o], and an "r" closing a syllable is the goiano [ɻ] in the middle of the word ("porta" [ˈpɔɻtɐ], `¨or¨` in the CSV)
  and the throat [h] at the end of it ("mar" [mah], `¨ar`);
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
- the japanese and english banks were recorded at different notes, so each piece is nudged to the same pitch (`--pitch`, default 290 Hz).

`--speed 1.2` talks 20% faster, `--cuts mp3_cut` if you only cut mp3s.
Numbers aren't spoken yet, write them out.

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
