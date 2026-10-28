"""
Cache raw response Scribe dari DB (READ-ONLY) untuk evaluasi offline.

    python -m tools.diar_eval.cache export --env-file .env --campaign Cashline
    python -m tools.diar_eval.cache info
    python -m tools.diar_eval.cache purge

Isi cache memuat TEKS TRANSKRIP (PII): disimpan di DIAR_EVAL_CACHE_DIR yang
WAJIB di luar repo, dibuat dengan izin 0700, dan dihapus dengan `purge`
setelah evaluasi selesai (retensi maksimum: RETENTION_DAYS).

Tautan: dashboard.results.source_files (App B) <-> stem
dashboard.result_speech_to_text.audio_file (App A) — sama dengan audit §5.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional

RETENTION_DAYS = 30
MANIFEST = "manifest.json"
WORD_KEYS = ("text", "start", "end", "type", "speaker_id")
_REPO = Path(__file__).resolve().parents[2]

_SQL_STEMS = """
    SELECT split_part(f.name, '.', 1) AS stem, array_agg(DISTINCT r.id::text) AS result_ids
    FROM dashboard.results r
    CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(r.source_files) = 'array' THEN r.source_files ELSE '[]'::jsonb END) AS f(name)
    WHERE r.status = 'done' AND r.campaign = %(campaign)s
    GROUP BY 1
"""
_SQL_RAW = """
    SELECT DISTINCT ON (split_part(audio_file, '.', 1))
           split_part(audio_file, '.', 1) AS stem, asr_raw_response
    FROM dashboard.result_speech_to_text
    WHERE asr_backend = 'elevenlabs' AND split_part(audio_file, '.', 1) = ANY(%(stems)s)
    ORDER BY split_part(audio_file, '.', 1), created_at DESC
"""


def cache_dir(path: Optional[str] = None) -> Path:
    p = Path(path or os.environ.get("DIAR_EVAL_CACHE_DIR", "")).expanduser()
    if not str(p) or str(p) == ".":
        raise SystemExit("Set DIAR_EVAL_CACHE_DIR (atau --cache-dir) ke direktori DI LUAR repo.")
    p = p.resolve()
    if p == _REPO or _REPO in p.parents:
        raise SystemExit("DIAR_EVAL_CACHE_DIR tidak boleh di dalam repo (%s): isinya PII." % _REPO)
    return p


def slim_raw(raw: Optional[Dict]) -> Optional[Dict]:
    """Simpan hanya field yang dibutuhkan evaluasi."""
    if not raw:
        return None
    return {
        "audio_duration_secs": raw.get("audio_duration_secs"),
        "language_code": raw.get("language_code"),
        "words": [{k: w.get(k) for k in WORD_KEYS} for w in raw.get("words") or []],
    }


def write_entry(root: Path, stem: str, entry: Dict) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    path = root / ("%s.json" % stem)
    path.write_text(json.dumps(entry, ensure_ascii=False))
    os.chmod(path, 0o600)


def write_manifest(root: Path, **info) -> None:
    info["created_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    info["purge_before"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=RETENTION_DAYS)).date().isoformat()
    (root / MANIFEST).write_text(json.dumps(info, indent=1))


def iter_entries(root: Path) -> Iterator[Dict]:
    """Hanya file entri cache ({stem, raw, ...}); file lain di direktori (manifest, sampel) dilewati."""
    for path in sorted(root.glob("*.json")):
        if path.name == MANIFEST:
            continue
        data = json.loads(path.read_text())
        if isinstance(data, dict) and "stem" in data and "raw" in data:
            yield data


def purge(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)


def export(env_file: Optional[str], campaign: str, root: Path, chunk: int = 200) -> Dict:
    import psycopg2

    url = os.environ.get("DATABASE_URL")
    if not url and env_file:
        from dotenv import dotenv_values
        url = dotenv_values(env_file).get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL tidak ditemukan (env atau --env-file).")
    conn = psycopg2.connect(url)
    conn.set_session(readonly=True)
    try:
        cur = conn.cursor()
        cur.execute(_SQL_STEMS, {"campaign": campaign})
        stems: Dict[str, List[str]] = dict(cur.fetchall())
        names = sorted(stems)
        n = 0
        for i in range(0, len(names), chunk):
            cur.execute(_SQL_RAW, {"stems": names[i:i + chunk]})
            for stem, raw in cur.fetchall():
                write_entry(root, stem, {"stem": stem, "campaign": campaign,
                                         "result_ids": stems[stem], "raw": slim_raw(raw)})
                n += 1
    finally:
        conn.rollback()
        conn.close()
    info = {"campaign": campaign, "n_stems_in_results": len(stems), "n_cached": n}
    write_manifest(root, **info)
    return info


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("export", "info", "purge"))
    ap.add_argument("--cache-dir")
    ap.add_argument("--env-file")
    ap.add_argument("--campaign", default="Cashline")
    a = ap.parse_args(argv)
    root = cache_dir(a.cache_dir)
    if a.command == "export":
        print(json.dumps(export(a.env_file, a.campaign, root)))
    elif a.command == "info":
        m = root / MANIFEST
        print(m.read_text() if m.exists() else "cache kosong: %s" % root)
    else:
        purge(root)
        print("dihapus: %s" % root)


if __name__ == "__main__":
    sys.exit(main())
