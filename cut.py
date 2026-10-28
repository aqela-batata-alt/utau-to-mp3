from pydub import AudioSegment
import argparse
import os
import shutil
import sys
import urllib.request
import zipfile

DOWNLOAD_URL = "https://kasaneteto.jp/assets/download/utau/"
VOICEBANK_DIR = "voicebanks"

# bank name -> (zip file, [(folder inside the zip, output folder name)])
BANKS = {
    'standard': ("TETO-tandoku-100619.zip", [("重音テト単独音", "standard")]),
    'whisper': ("TETO-sasayakitan-100925.zip", [("重音テトささやき単独音", "whisper")]),
    'extra': ("TETO-extra-100619.zip", [("重音テトエクストラ", "extra")]),
    'edge': ("TETO-edgeongen211010.zip", [("重音テトエッジ単独音", "edge"),
                                         ("重音テトエッジ連続音", "edge-continuous"),
                                         ("重音テト語尾息吸", "edge-breath")]),
    'english': ("TETO-English-150401.zip", [("重音テト英語音源", "english")]),
}

# characters that windows (and a few other places) won't accept in a filename,
# swapped for their fullwidth twins so "* あ" still looks like "＊ あ"
BAD_FILENAME_CHARS = str.maketrans('\\/:*?"<>|', '＼／：＊？＂＜＞｜')


def parse_oto_ini_with_types(file_path):
    """
    Parse an oto.ini file with automatic type conversion for numeric values.
    """
    entries = []

    # the japanese banks are Shift-JIS, the english one is plain ascii
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()
    except UnicodeDecodeError:
        with open(file_path, 'r', encoding='cp932') as file:
            lines = file.readlines()
    except Exception as e:
        print(f"Error: {e}")
        return []

    for line in lines:
        line = line.strip()

        if not line or line.startswith((';', '#')):
            continue

        if '=' not in line:
            continue

        filename, params_part = line.split('=', 1)
        params = [param.strip() for param in params_part.split(',')]

        while len(params) < 6:
            params.append('')

        # Convert numeric parameters to float when possible
        def try_float(value):
            try:
                return float(value) if value else 0.0
            except ValueError:
                return value

        entry = {
            'filename': filename.strip().replace('\\', '/'),
            'alias': params[0] or os.path.splitext(filename.strip())[0],
            'offset': try_float(params[1]),
            'consonant': try_float(params[2]),
            'cutoff': try_float(params[3]),
            'preutterance': try_float(params[4]),
            'overlap': try_float(params[5])
        }

        entries.append(entry)

    return entries


def safe_filename(alias):
    return alias.translate(BAD_FILENAME_CHARS).strip() or '_'


def unique_entries(oto_data):
    """
    Drop entries that would produce the exact same audio as an earlier one
    (oto.ini loves to repeat "あ" and "- あ" with the same numbers) and
    give every entry the name of the file it will be saved as.
    """
    seen_cuts = set()
    seen_names = set()
    entries = []
    for entry in oto_data:
        cut = (entry['filename'], entry['offset'], entry['cutoff'])
        name = safe_filename(entry['alias'])
        if cut in seen_cuts or name in seen_names:
            continue
        seen_cuts.add(cut)
        seen_names.add(name)
        entries.append(dict(entry, output=name))
    return entries


def cut_points(entry, duration):
    """
    UTAU cutoff rules:
      - negative cutoff: the sound is |cutoff| ms long, counted from the offset
      - positive cutoff: that many ms are thrown away from the END of the file
    """
    start_time = entry['offset']
    if entry['cutoff'] < 0:
        end_time = start_time - entry['cutoff']
    else:
        end_time = duration - entry['cutoff']
    return start_time, min(end_time, duration)


def zip_name(info):
    # japanese zips store the names as Shift-JIS without telling anyone
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode('cp437').decode('cp932')
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def download(zip_file):
    os.makedirs(VOICEBANK_DIR, exist_ok=True)
    destination = os.path.join(VOICEBANK_DIR, zip_file)
    if os.path.exists(destination):
        return destination
    # already downloaded by hand next to the script? use that one
    if os.path.exists(zip_file):
        return zip_file

    print(f"downloading {DOWNLOAD_URL}{zip_file} ...")
    request = urllib.request.Request(DOWNLOAD_URL + zip_file, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request) as response, open(destination + '.part', 'wb') as file:
        shutil.copyfileobj(response, file)
    os.rename(destination + '.part', destination)
    return destination


def unzip(zip_path):
    destination = os.path.join(VOICEBANK_DIR, os.path.splitext(os.path.basename(zip_path))[0])
    if os.path.isdir(destination):
        return destination

    print(f"unzipping {zip_path} ...")
    root = os.path.abspath(destination)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            path = os.path.abspath(os.path.join(root, zip_name(info)))
            if not path.startswith(root + os.sep):
                continue  # nope, not writing outside the folder
            if info.is_dir():
                os.makedirs(path, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with zf.open(info) as source, open(path, 'wb') as target:
                shutil.copyfileobj(source, target)
    return destination


def find_folder(root, folder_name):
    for path, folders, files in os.walk(root):
        if os.path.basename(path) == folder_name and 'oto.ini' in files:
            return path
    raise FileNotFoundError(f"{folder_name}/oto.ini not found inside {root}")


def bank_folders(bank):
    """[(folder with oto.ini, output name)] for a bank, downloading it if needed."""
    if bank not in BANKS:
        if os.path.isfile(os.path.join(bank, 'oto.ini')):
            # old school: a folder that already has an oto.ini in it
            return [(bank, os.path.basename(os.path.abspath(bank)))]
        sys.exit(f"unknown bank '{bank}', use one of: {', '.join(BANKS)} (or a folder with an oto.ini)")

    zip_file, folders = BANKS[bank]
    root = unzip(download(zip_file))
    return [(find_folder(root, folder), output) for folder, output in folders]


def cut_folder(folder, output, extensions):
    oto_data = unique_entries(parse_oto_ini_with_types(os.path.join(folder, 'oto.ini')))
    for extension in extensions:
        os.makedirs(f"{extension}_cut/{output}", exist_ok=True)

    audios = {}
    saved = 0
    for entry in oto_data:
        wav = os.path.join(folder, entry['filename'])
        if wav not in audios:
            if not os.path.exists(wav):
                print(f"  missing {wav}, skipping '{entry['alias']}'")
                audios[wav] = None
                continue
            audios[wav] = AudioSegment.from_wav(wav)
        audio = audios[wav]
        if audio is None:
            continue

        start_time, end_time = cut_points(entry, len(audio))  # milliseconds
        if end_time <= start_time:
            print(f"  '{entry['alias']}' has nothing left after cutting, skipping")
            continue
        trimmed_audio = audio[start_time:end_time]
        for extension in extensions:
            trimmed_audio.export(f"{extension}_cut/{output}/{entry['output']}.{extension}", format=extension)
        saved += 1

    print(f"  {output}: {saved} sounds")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Kasane Teto's voicebanks and cut them into one file per alias.")
    parser.add_argument('banks', nargs='*', default=list(BANKS),
                        help=f"banks to cut: {', '.join(BANKS)} (default: all) or a folder with an oto.ini")
    parser.add_argument('--wav', action='store_true', help="save as .wav (default)")
    parser.add_argument('--mp3', action='store_true', help="save as .mp3 (needs ffmpeg)")
    args = parser.parse_args()

    extensions = [extension for extension in ('wav', 'mp3') if getattr(args, extension)] or ['wav']

    for bank in args.banks:
        print(f"{bank}:")
        for folder, output in bank_folders(bank):
            cut_folder(folder, output, extensions)
