"""Offline whisper.cpp resources, bundled by the desktop build (never downloaded at runtime)."""
import array
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
import time
import wave
import zipfile
from collections import Counter
from pathlib import Path


class NoSpeechDetected(ValueError):
    def __init__(self,reason):
        self.reason=reason
        super().__init__({'no_audio':'素材没有音轨，没有可识别口播',
                          'silence':'音轨为空或静音，没有可识别口播',
                          'vad_no_speech':'VAD 未检测到语音，请核对音轨或提供字幕'}[reason])


def resource_directory():
    base = Path(sys._MEIPASS) if hasattr(sys, '_MEIPASS') else Path(__file__).resolve().parents[2] / '.state'
    directory = base / 'asr'
    executable = directory / 'whisper-cli'
    if hasattr(sys, '_MEIPASS') and not executable.is_file() and (directory / 'executable.zip').is_file():
        # Like the inspected FFmpeg payload, the archive prevents PyInstaller
        # from rewriting Mach-O bytes even when they were declared as DATA.
        try:
            manifest=json.loads((directory/'manifest.json').read_text())
            with zipfile.ZipFile(directory/'executable.zip') as bundle:
                entries=bundle.infolist()
                if len(entries)!=1 or entries[0].filename!='whisper-cli' or entries[0].file_size>64*1024*1024:
                    raise ValueError('内置口播程序完整性检查失败')
                raw=bundle.read(entries[0])
            if hashlib.sha256(raw).hexdigest()!=manifest.get('binary_sha256'):
                raise ValueError('内置口播程序完整性检查失败')
            with tempfile.NamedTemporaryFile(dir=directory,prefix='asr-',delete=False) as stream:
                temporary=Path(stream.name)
                try:stream.write(raw)
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
            try:temporary.replace(executable)
            finally:temporary.unlink(missing_ok=True)
        except (OSError,KeyError,TypeError,zipfile.BadZipFile,json.JSONDecodeError) as exc:
            raise ValueError('内置口播程序完整性检查失败') from exc
    if not all((directory / name).is_file() for name in ('whisper-cli', 'ggml-small.bin', 'manifest.json')):
        raise ValueError('内置口播模型资源缺失，请重新安装完整桌面应用')
    return directory


def decode_transcription(raw, duration):
    try:
        data = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        segments = data['transcription']
        if not isinstance(segments, list): raise TypeError()
        output = []
        previous = -1
        for index, segment in enumerate(segments):
            start, end = segment['offsets']['from'], segment['offsets']['to']
            text = segment['text']
            # whisper can close its final 30-second decode window past the actual audio end.
            window_clipped=(type(end) is int and index==len(segments)-1 and end>duration+100
                            and end==math.ceil(duration/30000)*30000)
            original_end=end
            if window_clipped: end=round(duration)
            if type(start) is not int or type(end) is not int or not (0 <= start < duration and start < end <= duration + 100) or start < previous or not isinstance(text, str):
                raise ValueError('本地转写时间或文本无效')
            previous = start
            if text.strip() and not text.strip().startswith('['):
                row={'start_ms': start, 'end_ms': round(min(end, duration)), 'text': text.strip()}
                if window_clipped:
                    row.update(original_end_ms=original_end,timing_adjustment='whisper_final_window_clipped_to_media')
                output.append(row)
        if not output: raise ValueError('没有识别到可用口播，请核对音轨或手工提供字幕')
        if not isinstance(data.get('result', {}), dict): raise TypeError('本地转写语言信息无效')
        language = data.get('result', {}).get('language', 'unknown')
        if not isinstance(language, str): raise TypeError()
        return output, language
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('本地转写结果格式无效') from exc


def transcript_warnings(subtitles):
    # Normalize only for warnings; never rewrite original recognized words.
    counts=Counter(re.sub(r'[\W_]+', '', row['text'], flags=re.UNICODE).casefold() for row in subtitles)
    counts.pop('', None)
    repeated=max(counts.values(),default=0)
    if repeated>=3 and repeated*2>len(subtitles):
        return ['识别结果有较多相同片段，请核对原音频后再使用；尚未确认内容准确性。']
    return []

def speech_ranges(samples, sample_rate):
    """Split >=1 second of near-zero PCM, retaining original time and 200 ms padding.

    This conservative amplitude gate is not a speech classifier or another model.
    """
    frame, gap, pad = max(1, sample_rate // 50), sample_rate, sample_rate // 5
    ranges, start, last_active = [], None, None
    for offset in range(0, len(samples), frame):
        block = samples[offset:offset + frame]
        active = sum(sample * sample for sample in block) >= 32 * 32 * len(block)
        if active:
            if start is not None and offset - last_active >= gap:
                ranges.append((max(0, start - pad), min(len(samples), last_active + pad)))
                start = None
            if start is None:
                start = offset
            last_active = offset + len(block)
    if start is not None:
        ranges.append((max(0, start - pad), min(len(samples), last_active + pad)))
    return ranges


# Public configuration is frozen into cache keys and audit provenance. No model
# swapping or repeated A/B decode is performed after a failed transcription.
WHISPER_COMMIT = 'd1be6fde11ac6e0407606b4e42fe72d34add8037'
VAD_SHA256 = '2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987'
VAD_LICENSE_SHA256 = '2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b'

DECODE_CONFIG = {'text_context_tokens': 0, 'sample_rate': 16000, 'channels': 1,
    'sample_format': 'pcm_s16le', 'threads': 4, 'gpu': False, 'temperature_fallback': False,
    'vad': {'name': 'silero-v6.2.0', 'threshold': 0.5, 'min_speech_duration_ms': 250,
            'min_silence_duration_ms': 100, 'max_speech_duration_s': 30,
            'speech_pad_ms': 30, 'samples_overlap': 0.1}}


class TranscriptionCancelled(ValueError):
    pass


def _digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def runtime_signature():
    """Validate pinned local VAD; never download missing assets at runtime."""
    directory = resource_directory()
    try:
        manifest = json.loads((directory / 'manifest.json').read_text())
        vad = manifest['vad']
        filename = vad['filename']
        if (filename != 'ggml-silero-v6.2.0.bin' or vad['compatible_whisper_commit'] != manifest['commit']
                or manifest['version'] != 'v1.9.5' or manifest['commit'] != WHISPER_COMMIT
                or vad['sha256'] != VAD_SHA256 or vad['license_sha256'] != VAD_LICENSE_SHA256
                or _digest(directory / 'whisper-cli') != manifest['binary_sha256']
                or _digest(directory / filename) != vad['sha256']
                or _digest(directory / 'silero-vad-LICENSE.txt') != vad['license_sha256']):
            raise ValueError('VAD asset mismatch')
        # The archived payload keeps the prepared bytes intact. Its private
        # extraction has no executable bit. Grant that bit
        # only after the complete resource/version validation above succeeds.
        if hasattr(sys, '_MEIPASS') and sys.platform != 'win32':
            (directory / 'whisper-cli').chmod(0o700)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise ValueError('内置 VAD 模型、许可或运行版本不匹配，请安装完整桌面应用') from exc
    return {'pipeline_version': 'local-asr-v2-official-vad', 'version': manifest['version'],
        'commit': manifest['commit'], 'binary_sha256': manifest['binary_sha256'],
        'model_sha256': manifest['model_sha256'], 'vad_sha256': vad['sha256'],
        'decode_config': DECODE_CONFIG}


def _run_process(args, *, timeout, cancelled=None):
    """Cancellation terminates only this child process and reaps it."""
    if cancelled is None:
        return subprocess.run(args, capture_output=True, check=False, timeout=timeout)
    if cancelled(): raise TranscriptionCancelled('本地转写已取消')
    started = time.monotonic()
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            if cancelled(): raise TranscriptionCancelled('本地转写已取消')
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0: raise subprocess.TimeoutExpired(args, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        process.terminate()
        try: process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill(); process.communicate()
        raise


def transcribe(path, duration, ffmpeg, *, language='auto', cancelled=None):
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('素材时长无效，请先完成本地媒体处理')
    if not isinstance(language, str) or not re.fullmatch(r'auto|[a-z]{2,3}', language):
        raise ValueError('口播语言设置无效，请选择有效语言或自动识别')
    started = time.monotonic()
    directory = resource_directory()
    if cancelled and cancelled(): raise TranscriptionCancelled('本地转写已取消')
    try:
        with tempfile.TemporaryDirectory(prefix='creative-asr-') as temporary:
            audio = Path(temporary) / 'audio.wav'
            extracted = _run_process([str(ffmpeg), '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                '-i', str(path), '-map', '0:a:0', '-vn', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(audio)],
                timeout=600, cancelled=cancelled)
            if extracted.returncode:
                error=extracted.stderr.decode('utf-8',errors='replace') if isinstance(extracted.stderr,bytes) else str(extracted.stderr or '')
                if re.search(r"Stream map '[^']*' matches no streams",error):raise NoSpeechDetected('no_audio')
                raise ValueError('素材没有可读取的音轨或音频编码无效')
            with wave.open(str(audio), 'rb') as stream:
                if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, 16000):
                    raise ValueError('本地音频转换格式无效')
                audio_duration = stream.getnframes() / 16
                samples = array.array('h', stream.readframes(stream.getnframes()))
            if sys.byteorder != 'little': samples.byteswap()
            if not samples or max(map(abs, samples)) < 32:
                raise NoSpeechDetected('silence')
            signature = runtime_signature()
            manifest = json.loads((directory / 'manifest.json').read_text())
            prefix = Path(temporary) / 'transcript'
            vad = DECODE_CONFIG['vad']
            # v1.9.5 performs real Silero VAD and maps output timestamps back
            # to original media. The old amplitude chunks are not used here.
            result = _run_process([str(directory / 'whisper-cli'), '-m', str(directory / 'ggml-small.bin'),
                '-f', str(audio), '-l', language, '-mc', '0', '-nf', '-oj', '-of', str(prefix), '-ng', '-t', '4',
                '--vad', '--vad-model', str(directory / manifest['vad']['filename']),
                '--vad-threshold', str(vad['threshold']), '--vad-min-speech-duration-ms', str(vad['min_speech_duration_ms']),
                '--vad-min-silence-duration-ms', str(vad['min_silence_duration_ms']),
                '--vad-max-speech-duration-s', str(vad['max_speech_duration_s']),
                '--vad-speech-pad-ms', str(vad['speech_pad_ms']), '--vad-samples-overlap', str(vad['samples_overlap'])],
                timeout=max(600, audio_duration / 1000 * 30), cancelled=cancelled)
            if result.returncode: raise ValueError('内置口播识别失败，请核对音轨后重试')
            stderr = result.stderr.decode('utf-8', errors='replace') if isinstance(result.stderr, bytes) else str(result.stderr or '')
            ranges = [{'start_ms': max(0, round(float(match[1]) * 1000)),
                       'end_ms': min(round(duration), round(float(match[2]) * 1000))}
                      for match in re.finditer(r'Including segment \d+: ([\d.]+) - ([\d.]+)', stderr)]
            # A successful process with no VAD speech is not successful text.
            if re.search(r'Final speech segments after filtering: 0\b', stderr):
                raise NoSpeechDetected('vad_no_speech')
            subtitles, detected = decode_transcription(prefix.with_suffix('.json').read_text(), min(duration, audio_duration))
            warnings = transcript_warnings(subtitles)
            if not ranges:
                warnings.append('VAD 语音范围缺失，台词需核对后使用。')
            if ranges and any(not any(row['start_ms'] < r['end_ms'] and row['end_ms'] > r['start_ms'] for r in ranges) for row in subtitles):
                warnings.append('识别文本落在 VAD 语音范围之外，请核对台词。')
            from .evidence_policy import transcript_policy
            provenance = {'execution_state': 'succeeded', 'quality_state': 'suspect' if warnings else 'usable',
                'review_state': 'unreviewed', 'needs_review': True, 'reviewed': False, 'warnings': warnings,
                'method': 'whisper.cpp-local', 'version': manifest['version'], 'model': 'small-multilingual',
                'model_sha256': manifest['model_sha256'], 'language': detected, 'runtime_signature': signature,
                'audio_duration_ms': round(audio_duration), 'elapsed_seconds': round(time.monotonic() - started, 3),
                'failure_reason': None, 'decode_config': {**DECODE_CONFIG, 'requested_language': language,
                    'vad_sha256': manifest['vad']['sha256'], 'speech_ranges': ranges}}
            evaluated = transcript_policy({'metadata': {'duration_ms': duration, 'speech_language': language,
                'transcript_source': 'whisper.cpp-local', 'subtitles': subtitles, 'transcription': provenance}})
            provenance['quality_state'] = evaluated['quality_state']
            provenance['quality_reasons'] = evaluated['reasons']
            return subtitles, provenance
    except TranscriptionCancelled:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('内置口播识别无法运行或处理超时') from exc
