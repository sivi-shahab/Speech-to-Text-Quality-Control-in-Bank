#!/usr/bin/env python3
"""
model/speech_stt/diarization_backends/nemo_backend.py

[NEW] Backend opsional: NeMo NeuralDiarizer dengan checkpoint
"diar_msdd_telephonic" — dilatih KHUSUS di ~1500 jam percakapan telepon
(Fisher Corpus), bukan model diarization generik seperti pyannote-3.1
(yang mayoritas dilatih di data meeting/broadcast).

Alur kerja (beda dari pyannote — NeMo berbasis file, bukan in-memory):
  1. Tulis "manifest" JSON (format resmi NeMo: audio_filepath, offset,
     duration, label, text, num_speakers, rttm_filepath, uem_filepath)
     ke temp dir unik per panggilan.
  2. Jalankan NeuralDiarizer(cfg).diarize() — ini MENULIS hasil ke disk
     sebagai file RTTM di <out_dir>/pred_rttms/<nama_audio>.rttm.
  3. Parse file RTTM itu jadi list segmen {start, end, speaker}.
  4. Hapus temp dir.

⚠️ RACE CONDITION YANG SUDAH DILAPORKAN RESMI KE NVIDIA:
https://github.com/NVIDIA-NeMo/NeMo/issues/12254 — NeuralDiarizer
menyimpan state internal (embedding vectors, timestamps) yang bisa
tercampur antar panggilan kalau dipanggil concurrent dari banyak thread
tanpa lock eksplisit (gejala: "Mismatch of counts between embedding
vectors and timestamps"). SEMUA panggilan `.diarize()` di sini WAJIB
lewat `get_nemo_diarizer_lock(gpu_id)` — TIDAK seperti pyannote yang
"hanya" pakai semaphore sebagai hardening tambahan (HOTFIX A), untuk
NeMo lock ini POKOK, bukan opsional, sampai NVIDIA mengonfirmasi fix.
"""

import os
import json
import shutil
import logging
import tempfile
from typing import Any, Dict, List

from model.speech_stt.diarization_backends.base import DiarizationBackend

logger = logging.getLogger(__name__)


class NeMoDiarizationBackend(DiarizationBackend):
    name = "nemo"

    def __init__(self, model_name: str, gpu_id: int):
        self.model_name = model_name
        self.gpu_id     = gpu_id
        self.cfg        = None  # template OmegaConf config, di-set load()
        self._model     = None  # NeuralDiarizer instance, lazy + reused (default)

    def load(self, gpu_id: int) -> None:
        from model.speech_stt.model_registry import get_shared_nemo_diarizer_config
        self.cfg = get_shared_nemo_diarizer_config(gpu_id, self.model_name)

    def is_available(self) -> bool:
        return self.cfg is not None

    def _get_model(self):
        from nemo.collections.asr.models.msdd_models import NeuralDiarizer
        from model.speech_stt.config import NEMO_DIARIZER_FRESH_INSTANCE_PER_CALL

        if NEMO_DIARIZER_FRESH_INSTANCE_PER_CALL or self._model is None:
            logger.info(
                "[NeMo] %s instance NeuralDiarizer (%s)...",
                "Membuat baru" if NEMO_DIARIZER_FRESH_INSTANCE_PER_CALL else "Memuat pertama kali",
                self.model_name,
            )
            self._model = NeuralDiarizer(cfg=self.cfg).to(self.cfg.device)
        return self._model

    def diarize(self, audio_path: str, num_speakers: int = 2) -> List[Dict[str, Any]]:
        from model.speech_stt.model_registry import get_nemo_diarizer_lock

        lock     = get_nemo_diarizer_lock(self.gpu_id)
        work_dir = tempfile.mkdtemp(prefix="nemo_diar_")
        try:
            manifest_path = os.path.join(work_dir, "manifest.json")
            meta = {
                "audio_filepath": audio_path,
                "offset":         0,
                "duration":       None,
                "label":          "infer",
                "text":           "-",
                "num_speakers":   num_speakers,
                "rttm_filepath":  None,
                "uem_filepath":   None,
            }
            with open(manifest_path, "w") as f:
                f.write(json.dumps(meta) + "\n")

            # Serialisasi PENUH — lihat peringatan race-condition di docstring.
            # Mutasi cfg (manifest/out_dir) dilakukan DI DALAM lock supaya
            # tidak ada request lain yang membaca cfg di tengah mutasi.
            with lock:
                self.cfg.diarizer.manifest_filepath = manifest_path
                self.cfg.diarizer.out_dir            = work_dir
                model = self._get_model()
                model.diarize()

            rttm_path = os.path.join(
                work_dir, "pred_rttms",
                os.path.splitext(os.path.basename(audio_path))[0] + ".rttm",
            )
            return self._parse_rttm(rttm_path)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    @staticmethod
    def _parse_rttm(rttm_path: str) -> List[Dict[str, Any]]:
        """
        Format RTTM (Rich Transcription Time Marked), 1 baris per segmen:
          SPEAKER <file_id> <channel> <start> <duration> <NA> <NA> <speaker_id> <NA> <NA>
        """
        segments: List[Dict[str, Any]] = []
        if not os.path.exists(rttm_path):
            logger.warning("[NeMo] RTTM output tidak ditemukan: %s", rttm_path)
            return segments
        with open(rttm_path) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 8 or parts[0] != "SPEAKER":
                    continue
                start    = float(parts[3])
                duration = float(parts[4])
                speaker  = parts[7]
                segments.append({
                    "start":   start,
                    "end":     start + duration,
                    "speaker": speaker,
                    "index":   len(segments),
                })
        return segments
