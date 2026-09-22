"""
stt_service.py
==============
Speech-to-Text service that supports local Whisper, Groq Whisper, Sarvam, and
browser recognition. Groq uses the same server-side key as the chat provider.

Usage:
    stt = STTService()
    result = stt.transcribe(audio_bytes_or_path, language_hint='hi')
    # result = {'text': '...', 'language': 'hi', 'confidence': 0.97}
"""

import os
import tempfile
import logging
from pathlib import Path
import requests
from flask import current_app

log = logging.getLogger(__name__)

# Lazy-loaded Whisper model singleton so we only load once per process.
_whisper_model = None
_whisper_model_size = None
_whisper_failed_sizes = set()

_VALID_BACKENDS = {'whisper_local', 'sarvam_api', 'groq_api', 'browser'}
_VALID_MODEL_SIZES = {'tiny', 'base', 'small', 'medium', 'large-v3'}
_LANGUAGES = {'en', 'hi', 'gu'}


def _get_whisper_model(model_size: str = "small"):
    """Load (or reuse) the faster-whisper model."""
    global _whisper_model, _whisper_model_size
    if model_size in _whisper_failed_sizes:
        return None
    if _whisper_model is None or _whisper_model_size != model_size:
        try:
            from faster_whisper import WhisperModel
            log.info(f"[STT] Loading faster-whisper model '{model_size}' …")
            _whisper_model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
            )
            _whisper_model_size = model_size
            log.info("[STT] Whisper model loaded OK")
        except Exception as exc:
            log.error(f"[STT] Failed to load Whisper model: {exc}")
            _whisper_model = None
            _whisper_failed_sizes.add(model_size)
    return _whisper_model


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class STTService:
    """Speech-to-Text wrapper with multiple backend support."""

    def __init__(self, backend: str = None, model_size: str = None):
        """
        backend   – override config; one of 'whisper_local', 'sarvam_api', 'groq_api', 'browser'
        model_size – Whisper model size override
        """
        try:
            cfg = current_app.config
            self.backend    = backend    or cfg.get('AI_STT_BACKEND',    'whisper_local')
            self.model_size = model_size or cfg.get('AI_STT_MODEL_SIZE', 'small')
            self.sarvam_key = cfg.get('AI_SARVAM_API_KEY', '')
            self.groq_key = cfg.get('GROQ_API_KEY', '')
            self.groq_model = cfg.get('AI_GROQ_STT_MODEL', 'whisper-large-v3-turbo')
        except RuntimeError:
            # Outside application context (e.g. module-level import)
            self.backend    = backend    or 'whisper_local'
            self.model_size = model_size or 'small'
            self.sarvam_key = ''
            self.groq_key = ''
            self.groq_model = 'whisper-large-v3-turbo'

        self.backend = str(self.backend).lower()
        self.model_size = str(self.model_size).lower()
        if self.backend not in _VALID_BACKENDS:
            log.warning("[STT] Unknown backend '%s'; using browser fallback", self.backend)
            self.backend = 'browser'
        if self.model_size not in _VALID_MODEL_SIZES:
            log.warning("[STT] Unknown Whisper model '%s'; using base", self.model_size)
            self.model_size = 'base'

    # ------------------------------------------------------------------
    def transcribe(self, audio_input, language_hint: str = None, filename: str = None) -> dict:
        """
        Transcribe audio.

        audio_input – bytes (raw audio data) OR str (path to audio file)
        language_hint – ISO 639-1 code ('en', 'hi', 'gu') or None for auto

        Returns:
            {
                'text': str,
                'language': str,       # detected language code
                'confidence': float,   # 0-1
                'error': str | None,
            }
        """
        language_hint = self._normalise_language(language_hint)

        # Write bytes to a correctly suffixed temporary file.  ffmpeg (used by
        # faster-whisper) uses this hint for browser MediaRecorder recordings.
        tmp_path = None
        if isinstance(audio_input, (bytes, bytearray)):
            suffix = self._safe_suffix(filename)
            tmp = tempfile.NamedTemporaryFile(
                suffix=suffix, delete=False, dir=tempfile.gettempdir()
            )
            tmp.write(audio_input)
            tmp.close()
            tmp_path = tmp.name
            audio_path = tmp_path
        else:
            audio_path = audio_input

        try:
            if not audio_path:
                return self._error('No audio was provided.', language_hint)
            if self.backend == 'whisper_local':
                result = self._transcribe_whisper(audio_path, language_hint)
                if result.get('error') and self.sarvam_key:
                    log.warning("[STT] Whisper failed, falling back to Sarvam")
                    result = self._transcribe_sarvam(audio_path, language_hint)
            elif self.backend == 'sarvam_api':
                result = self._transcribe_sarvam(audio_path, language_hint)
            elif self.backend == 'groq_api':
                result = self._transcribe_groq(audio_path, language_hint)
            else:
                # 'browser' mode – should never reach server
                result = {'text': '', 'language': language_hint or 'en', 'confidence': 0.0,
                          'error': 'Browser STT mode — audio processed client-side'}
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        return result

    # ------------------------------------------------------------------
    def _transcribe_whisper(self, audio_path: str, language_hint: str) -> dict:
        """Use faster-whisper to transcribe locally."""
        model = _get_whisper_model(self.model_size)
        if model is None:
            return {
                'text': '', 'language': language_hint or 'en', 'confidence': 0.0,
                'error': 'Whisper STT model not loaded on server. Please use browser speech recognition or type your message.'
            }


        try:
            segments, info = model.transcribe(
                audio_path,
                language=language_hint,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            text_parts = [seg.text.strip() for seg in segments]
            text = " ".join(text_parts).strip()

            return {
                'text': text,
                'language': info.language,
                'confidence': round(info.language_probability, 3),
                'error': None if text else 'No speech detected in audio',
            }
        except Exception as exc:
            log.error(f"[STT][Whisper] Transcription error: {exc}")
            return self._error('Could not transcribe this audio. Please try again or type your message.', language_hint, exc)

    # ------------------------------------------------------------------
    def _transcribe_sarvam(self, audio_path: str, language_hint: str) -> dict:
        """Fallback: Sarvam AI speech-to-text REST API."""
        if not self.sarvam_key:
            return {
                'text': '', 'language': 'en', 'confidence': 0.0,
                'error': 'Sarvam AI API key not configured.'
            }

        try:
            lang_map = {'hi': 'hi-IN', 'gu': 'gu-IN', 'en': 'en-IN'}
            lang_code = lang_map.get(language_hint, 'unknown')

            with open(audio_path, 'rb') as audio_file:
                resp = requests.post(
                    "https://api.sarvam.ai/speech-to-text",
                    headers={"api-subscription-key": self.sarvam_key},
                    files={"file": (Path(audio_path).name, audio_file, "application/octet-stream")},
                    data={
                        "model": "saaras:v3",
                        "language_code": lang_code if lang_code != 'unknown' else 'unknown',
                    },
                    timeout=(5, 30),
                )
            resp.raise_for_status()
            data = resp.json()

            return {
                'text': data.get('transcript', ''),
                'language': data.get('language_code', language_hint or 'en').split('-')[0],
                'confidence': data.get('confidence', 1.0),
                'error': None,
            }
        except Exception as exc:
            log.error(f"[STT][Sarvam] API error: {exc}")
            return self._error('Voice transcription is temporarily unavailable. Please type your message.', language_hint, exc)

    # ------------------------------------------------------------------
    def _transcribe_groq(self, audio_path: str, language_hint: str) -> dict:
        """Use Groq's OpenAI-compatible Whisper endpoint without exposing the key."""
        if not self.groq_key:
            return self._error('Voice transcription is not configured yet.', language_hint)

        data = {'model': self.groq_model}
        if language_hint:
            data['language'] = language_hint

        try:
            with open(audio_path, 'rb') as audio_file:
                response = requests.post(
                    'https://api.groq.com/openai/v1/audio/transcriptions',
                    headers={'Authorization': f'Bearer {self.groq_key}'},
                    files={'file': (Path(audio_path).name, audio_file, 'application/octet-stream')},
                    data=data,
                    timeout=(5, 45),
                )
            if response.status_code == 401:
                return self._error('Voice transcription key is invalid or missing.', language_hint)
            if response.status_code == 429:
                return self._error('The free voice-transcription limit has been reached. Please type your message.', language_hint)
            response.raise_for_status()
            transcript = response.json().get('text', '').strip()
            return {
                'text': transcript,
                'language': language_hint or 'en',
                'confidence': 1.0,
                'error': None if transcript else 'No speech detected in the recording.',
            }
        except (OSError, requests.RequestException, ValueError) as exc:
            log.error('[STT][Groq] API error: %s', exc)
            return self._error('Voice transcription is temporarily unavailable. Please type your message.', language_hint, exc)

    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        """Cheap health check that never downloads/loads a Whisper model."""
        if self.backend == 'whisper_local':
            return _whisper_model is not None and _whisper_model_size == self.model_size
        elif self.backend == 'sarvam_api':
            return bool(self.sarvam_key)
        elif self.backend == 'groq_api':
            return bool(self.groq_key)
        return True  # browser mode always "available"

    @staticmethod
    def _normalise_language(language_hint):
        language = (language_hint or '').split('-')[0].lower()
        return language if language in _LANGUAGES else None

    @staticmethod
    def _safe_suffix(filename):
        suffix = Path(filename or '').suffix.lower()
        return suffix if suffix in {'.webm', '.wav', '.mp3', '.m4a', '.ogg', '.opus', '.mp4'} else '.webm'

    @staticmethod
    def _error(message, language_hint, exception=None):
        if exception:
            log.debug('[STT] Internal transcription failure: %s', exception)
        return {'text': '', 'language': language_hint or 'en', 'confidence': 0.0, 'error': message}
