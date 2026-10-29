# Diarization v2 (Scribe)

Diarization v2 adalah post-processing baru untuk output ElevenLabs Scribe. Tujuan utamanya memperbaiki role **Agent/Customer yang tertukar**. Jalur lama memakai aturan "yang bicara duluan = Agent", yang ternyata salah pada sebagian besar kasus role tertukar di data Cashline.

Kodenya ada di paket terpisah `model/speech_stt/diarization_v2/`. `engine.py` hanya mendapat satu cabang `if`, dan saat flag `off` jalur lama tidak berubah sama sekali (dijaga oleh golden test).

## Mode

| `DIARIZATION_V2_MODE` | Perilaku |
|---|---|
| `off` (default) | Jalur lama apa adanya. Nilai yang tidak dikenal juga dianggap `off`. |
| `shadow` | v2 dihitung dari raw response yang sama, hasilnya hanya masuk metadata. Output dan PDF tidak berubah, dan tidak ada biaya API tambahan. |
| `on` | Output memakai hasil v2 |

Rekomendasinya: jalankan `shadow` di produksi, bandingkan metadata dengan jalur lama, baru pindah ke `on`.

## Rangkaian post-processing

```text
words
  → collapse_to_two            gabung cluster minor ke 2 speaker utama
  → smooth_single_word_flips   perbaiki flip 1 kata tanpa jeda
  → group_utterances           susun jadi utterance
  → assign_roles               cue skrip + dominansi bicara
  → label Agent / Customer
  → merge_same_speaker_simple  (fungsi jalur lama, tidak diubah)
```

Metadata yang dihasilkan hanya berisi angka, label, dan timestamp, **tanpa teks transkrip**, sehingga aman untuk log dan kolom DB.

## Penentuan role

Dua sinyal digabung untuk menentukan label mana yang Agent:

1. **Cue skrip** dari `role_cues.yaml`. Skor per label = `2 × intro` (di N utterance pertama) `+ 1 × script − 1 × customer`.
2. **Dominansi**: label dengan jumlah kata terbanyak.

| Kondisi | Keputusan | `role_confident` |
|---|---|---|
| Cue jelas dan sama dengan dominan | cue | ya |
| Cue jelas, beda dengan dominan, margin ≥ `STRONG_MARGIN` (2) | cue | ya |
| Cue jelas, beda dengan dominan, margin lemah | cue | tidak |
| Cue tidak ada atau seri (margin < `MIN_MARGIN` 1) | dominan | tidak |
| Hanya 1 label | label itu | tidak |

Call dengan `role_confident = false` adalah kandidat fallback LLM on-prem (fase berikutnya).

## Mode diarization Scribe

Hanya dipakai saat `DIARIZATION_V2_MODE=on`.

| `SCRIBE_DIAR_MODE` | Parameter API |
|---|---|
| `fixed2` (default) | `num_speakers=2` |
| `threshold` | `num_speakers` kosong + `SCRIBE_DIAR_THRESHOLD`. Bisa menghasilkan > 2 cluster, sehingga `collapse_to_two` wajib. |
| `roles` | `detect_speaker_roles` native dari Scribe (+10% biaya) |

`SCRIBE_ENABLE_LOGGING` mengatur retensi di sisi ElevenLabs: kosong = default akun, `false` = zero-retention (khusus enterprise).

## Status rollout

Rencana lengkap per fase, risiko, dan rollback ada di `docs/diarization-impl-plan.md`. Hasil audit ada di `docs/diarization-audit.md`. Deploy menunggu keputusan tim.
