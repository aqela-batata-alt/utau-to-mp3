# utau-to-mp3
convert audio files from utau to singular aliases files.

## Installation

1. Clone or download the file
2. Install required Python packages(using system packages as example):

```bash

# arch( btw):
sudo pacman -S python-pydub

# Ubuntu/Debian
sudo apt install python-pydub

# Fedora
sudo dnf install python-pydub
```

## Usage

1. Navigate to your voicebank folder

2. Copy the file in the oto.ini and audio folder:
   
teto english voicebank( the one i do care about) as example:
```
重音テト音声ライブラリー/
├── 重音テト英語音源/          (Example: Teto English Voicebank)
│   ├── audio1.wav
│   ├── ...
│   ├── audioN.wav
│   ├── oto.ini
│   └── cut.py               (Place script here)

```

3. Run the script:
```bash
python cut.py
```

4. Find your converted files in the new `mp3_cut/` folder:

The script will create MP3 files named after each alias in the oto.ini:
```
mp3_cut/
├── - @U.mp3
├── baU.mp3
└── ...
```
5. Be happy, now you can be teto i guess...

## Notes
- Original WAV files remain unchanged
- MP3 files are trimmed according to oto.ini offset and cutoff values

ToDo:
  - option to export in WAV.
  - CSV file with consonant and overlap for each alias for later usage.
  - fix some things.
  - become teto.
