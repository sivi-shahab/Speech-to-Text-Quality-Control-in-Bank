# Panduan Pelabelan Ground Truth Diarization (Cashline)

- Untuk: tim QC yang melabeli 30–50 call sampel
- Tujuan: mengukur seberapa tepat sistem menandai **siapa yang bicara (Agent/Customer) dan kapan**, terutama pada kalimat yang menentukan item checklist
- Hasil dipakai oleh: `tools/diar_eval/grid.py` (DER + role accuracy) dan `metrics.item_role_accuracy`

> ⚠️ **Data nasabah (PII).** File draft berisi transkrip. Kerjakan **hanya di server**, di folder yang diberikan. Jangan disalin ke laptop pribadi, chat, atau email. Folder akan dihapus setelah evaluasi selesai (paling lambat 30 hari).

---

## 1. Yang Anda terima per call

| File | Isi | Diedit? |
|---|---|---|
| `<stem>.segments.csv` | Potongan ucapan hasil mesin: `seg_id, start, end, speaker, text` | **Ya** |
| `<stem>.items.csv` | Kosong, hanya header: `item, start, end, speaker, catatan` | **Ya** |
| Audio rekaman `<stem>` | Untuk didengarkan | Tidak |

- **`speaker` di draft sengaja bernilai `SPK_A` / `SPK_B`**, bukan Agent/Customer. Mesin bisa salah, jadi **Anda yang menentukan** role dari mendengarkan audio.
- Waktu boleh ditulis dalam detik (`12.5` atau `12,5`) atau dalam format `mm:ss.xx` seperti di PDF (`01:05.20`).
- File bisa dibuka di Excel. Pemisah `,` atau `;` keduanya diterima. Simpan tetap sebagai **CSV UTF-8**.

## 2. Langkah kerja

1. **Dengarkan audio dari awal sampai akhir** sambil membaca `segments.csv`.
2. Ganti setiap `SPK_A` / `SPK_B` dengan **`Agent`**, **`Customer`**, atau **`Other`**.
   - `Other` dipakai untuk pihak ketiga, suara IVR atau mesin penjawab, atau orang lain di dekat nasabah yang ikut bicara.
3. **Perbaiki label per baris, bukan per huruf A/B.** Kalau satu baris berisi ucapan dua orang, **pecah menjadi dua baris** di titik pergantian, lalu sesuaikan `start`/`end`.
4. **Perbaiki batas waktu** kalau melenceng lebih dari ±0,5 detik dari yang terdengar. Ketelitian ±0,25 detik sudah cukup.
5. **Ucapan yang tidak tercatat mesin:** tambahkan baris baru (`seg_id` boleh kosong). `text` boleh kosong atau diisi ringkas.
6. **Bukan ucapan** (nada sambung, musik tunggu, hening, bunyi tombol): hapus barisnya. Jangan diberi label.
7. Isi **`items.csv`** sesuai §4.

## 3. Aturan khusus

| Situasi | Aturan |
|---|---|
| Backchannel ("iya", "hmm", "oke") saat lawan bicara | Baris sendiri dengan speaker yang mengucapkannya, **hanya kalau terdengar jelas** |
| Bicara bersamaan (overlap) | Dua baris yang waktunya tumpang-tindih. Masing-masing dengan speakernya. |
| Jeda dalam ucapan satu orang > 1 detik | Boleh dipecah menjadi dua baris. Tidak wajib. |
| Nasabah mengangkat telepon dan bicara duluan ("Halo?") | Tetap `Customer`. **Jangan** mengasumsikan yang bicara pertama adalah agent. |
| Agent mengulang perkataan nasabah ("Tanggal lahir 12 Mei, ya Pak?") | `Agent` (yang mengucapkan, bukan pemilik informasi) |
| Ragu siapa yang bicara | Tulis label terbaik Anda, lalu tambahkan `RAGU` di kolom `text` |

## 4. `items.csv`: bukti item checklist

Untuk setiap item checklist dalam cakupan (daftar ditetapkan tim QC, lihat `docs/questions.md` D4), tandai **jendela waktu ucapan yang menjadi bukti** item tersebut.

| Kolom | Isi |
|---|---|
| `item` | Kode item, mis. `SC_CL_2` (agent menyebutkan nama), `SC_CL_7` (agent menjelaskan bunga) |
| `start`, `end` | Awal dan akhir kalimat bukti |
| `speaker` | Siapa yang **seharusnya** mengucapkannya menurut checklist (biasanya `Agent`; persetujuan nasabah = `Customer`) **dan memang terdengar diucapkan orang itu** |
| `catatan` | Opsional |

- Satu item bisa punya beberapa baris (disebut lebih dari sekali).
- Item yang **tidak diucapkan sama sekali** tidak perlu ditulis.

## 5. Double labeling

Sebanyak 5 call ditandai `double_label: true` di file sampel. Call ini dilabeli **dua orang secara terpisah**, tanpa saling melihat hasil. Kesepakatan keduanya diukur dengan `gt_io agreement`. Target kesepakatan role ≥ 97%. Kalau di bawah target, aturan di §3 dibahas ulang sebelum melanjutkan.

## 6. Setelah selesai

Serahkan folder ke tim teknis. Folder itu diimpor dengan:

```bash
python -m tools.diar_eval.gt_io import --in-dir <folder_draft> --gt-dir <folder_gt>
```

Baris yang salah format (speaker tidak dikenal, `end ≤ start`, waktu tidak terbaca) dilaporkan per nomor baris untuk diperbaiki. GT hasil impor **tidak menyimpan teks**, hanya waktu dan role.

## 7. Estimasi beban

Sampel default (seed `20261028`) berisi 40 call NQ Cashline dengan total **6,8 jam audio**. Durasi median 8 menit, dan call terpanjang 36 menit. Pelabelan biasanya memakan waktu 3–4× durasi audio, jadi totalnya **±24–30 jam kerja** termasuk 5 call double labeling. Kalau beban perlu dikurangi, sampler bisa membatasi durasi (mis. `--max-duration 900` untuk ≤15 menit).
