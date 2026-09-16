"""
stt_service.py
==============
Speech-to-Text service that supports:
  1. Local faster-whisper (primary, free, offline)
  2. Sarvam AI REST API (fallback, optimised for Indian languages)
  3. Graceful degradation when neither is available

Usage:
    stt = STTService()
    result = stt.transcribe(audio_bytes_or_path, language_hint='hi')
    # result = {'text': '...', 'language': 'hi', 'confidence': 0.97}
"""

import os
import io
import tempfile
import logging
import requests
from flask import current_app

log = logging.getLogger(__name__)

# Lazy-loaded Whisper model singleton so we only load once per process.
_whisper_model = None
_whisper_model_size = None


def _get_whisper_model(model_size: str = "small"):
    """Load (or reuse) the faster-whisper model."""
    global _whisper_model, _whisper_model_size
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
    return _whisper_model


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class STTService:
    """Speech-to-Text wrapper with multiple backend support."""

    def __init__(self, backend: str = None, model_size: str = None):
        """
        backend   – override config; one of 'whisper_local', 'sarvam_api', 'browser'
        model_size – Whisper model size override
        """
        try:
            cfg = current_app.config
            self.backend    = backend    or cfg.get('AI_STT_BACKEND',    'whisper_local')
            self.model_size = model_size or cfg.get('AI_STT_MODEL_SIZE', 'small')
            self.sarvam_key = cfg.get('AI_SARVAM_API_KEY', '')
        except RuntimeError:
            # Outside application context (e.g. module-level import)
            self.backend    = backend    or 'whisper_local'
            self.model_size = model_size or 'small'
            self.sarvam_key = ''

    # ------------------------------------------------------------------
    def transcribe(self, audio_input, language_hint: str = None) -> dict:
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
        # Write bytes to temp file if needed
        tmp_path = None
        if isinstance(audio_input, (bytes, bytearray)):
            tmp = tempfile.NamedTemporaryFile(
                suffix=".webm", delete=False, dir=tempfile.gettempdir()
            )
            tmp.write(audio_input)
            tmp.close()
            tmp_path = tmp.name
            audio_path = tmp_path
        else:
            audio_path = audio_input

        try:
            if self.backend == 'whisper_local':
                result = self._transcribe_whisper(audio_path, language_hint)
                if result.get('error') and self.sarvam_key:
                    log.warning("[STT] Whisper failed, falling back to Sarvam")
                    result = self._transcribe_sarvam(audio_path, language_hint)
            elif self.backend == 'sarvam_api':
                result = self._transcribe_sarvam(audio_path, language_hint)
            else:
                # 'browser' mode – should never reach server
                result = {'text': '', 'language': 'en', 'confidence': 0.0,
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
                'error': 'Whisper model not available. Check faster-whisper installation.'
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
            return {'text': '', 'language': 'en', 'confidence': 0.0, 'error': str(exc)}

    # ------------------------------------------------------------------
    def _transcribe_sarvam(self, audio_path: str, language_hint: str) -> dict:
        """Fallback: Sarvam AI speech-to-text REST API."""
        if not self.sarvam_key:
            return {
                'text': '', 'language': 'en', 'confidence': 0.0,
                'error': 'Sarvam AI API key not configured.'
            }

        try:
            with open(audio_path, 'rb') as f:
                audio_bytes = f.read()

            lang_map = {'hi': 'hi-IN', 'gu': 'gu-IN', 'en': 'en-IN'}
            lang_code = lang_map.get(language_hint, 'unknown')

            resp = requests.post(
                "https://api.sarvam.ai/speech-to-text",
                headers={"api-subscription-key": self.sarvam_key},
                files={"file": ("audio.webm", audio_bytes, "audio/webm")},
                data={
                    "model": "saaras:v3",
                    "language_code": lang_code if lang_code != 'unknown' else 'unknown',
                },
                timeout=30,
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
            return {'text': '', 'language': 'en', 'confidence': 0.0, 'error': str(exc)}

    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        """Quick health check — can we transcribe right now?"""
        if self.backend == 'whisper_local':
            return _get_whisper_model(self.model_size) is not None
        elif self.backend == 'sarvam_api':
            return bool(self.sarvam_key)
        return True  # browser mode always "available"
