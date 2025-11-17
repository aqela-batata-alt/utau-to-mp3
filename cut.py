from pydub import AudioSegment
import os



def get_duration_pydub(file_path):
    audio_file = AudioSegment.from_file(file_path, format="wav")
    return audio_file.duration_seconds * 1000


def parse_oto_ini_with_types(file_path):
    """
    Parse an oto.ini file with automatic type conversion for numeric values.
    """
    entries = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            for line in file:
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
                    'filename': filename.strip(),
                    'alias': params[0],
                    'offset': try_float(params[1]),
                    'consonant': try_float(params[2]),
                    'cutoff': try_float(params[3]),
                    'preutterance': try_float(params[4]),
                    'overlap': try_float(params[5])
                }
                
                entries.append(entry)
                
    except Exception as e:
        print(f"Error: {e}")
        return []
    
    return entries

if __name__ == "__main__":
    oto_data = parse_oto_ini_with_types("oto.ini")
    os.mkdir("mp3_cut")
    for i, entry in enumerate(oto_data):
        audio = AudioSegment.from_wav(entry['filename'])
        start_time = entry['offset']  # milliseconds
        end_time = get_duration_pydub(entry['filename']) + entry['cutoff']    # milliseconds
        trimmed_audio = audio[start_time:end_time]
        trimmed_audio.export(f"mp3_cut/{entry['alias']}.mp3", format="mp3")

