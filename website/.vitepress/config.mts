import { defineConfig } from 'vitepress'

// GitHub Pages menyajikan situs di /<nama-repo>/. Override lewat DOCS_BASE
// kalau di-host di tempat lain (mis. DOCS_BASE=/ untuk server internal).
const base = process.env.DOCS_BASE ?? '/Speech-to-Text-Quality-Control-in-Bank/'

export default defineConfig({
  lang: 'id-ID',
  title: 'STT Quality Control',
  description: 'Layanan speech-to-text, diarization, dan analisis speaker untuk quality control rekaman telemarketing bank.',
  base,
  cleanUrls: true,
  lastUpdated: true,
  head: [['link', { rel: 'icon', type: 'image/svg+xml', href: `${base}logo.svg` }]],

  themeConfig: {
    logo: '/logo.svg',
    search: { provider: 'local' },

    nav: [
      { text: 'Panduan', link: '/guide/quick-start' },
      { text: 'Fitur', link: '/features/async-jobs' },
      { text: 'Referensi', link: '/reference/api' },
    ],

    sidebar: [
      {
        text: 'Panduan',
        items: [
          { text: 'Quick start', link: '/guide/quick-start' },
          { text: 'Kenapa layanan ini?', link: '/guide/why' },
          { text: 'Arsitektur', link: '/guide/architecture' },
        ],
      },
      {
        text: 'Fitur',
        items: [
          { text: 'Job transkripsi async', link: '/features/async-jobs' },
          { text: 'Endpoint ElevenLabs sinkron', link: '/features/elevenlabs-endpoint' },
          { text: 'Backend ASR', link: '/features/asr-backends' },
          { text: 'Backend diarization', link: '/features/diarization-backends' },
          { text: 'Diarization v2 (Scribe)', link: '/features/diarization-v2' },
          { text: 'Analisis speaker', link: '/features/speaker-analysis' },
          { text: 'Webhook & forwarding', link: '/features/delivery' },
          { text: 'Lightweight server & cache', link: '/features/lightweight-server' },
          { text: 'Monitoring & housekeeping', link: '/features/diagnostics' },
          { text: 'Evaluasi diarization', link: '/features/diar-eval' },
        ],
      },
      {
        text: 'Referensi',
        items: [
          { text: 'Endpoint API', link: '/reference/api' },
          { text: 'Konfigurasi (env var)', link: '/reference/configuration' },
          { text: 'Deployment', link: '/reference/deployment' },
          { text: 'Struktur kode & pattern', link: '/reference/code-structure' },
        ],
      },
    ],

    outline: { level: [2, 3], label: 'Di halaman ini' },
    docFooter: { prev: 'Sebelumnya', next: 'Berikutnya' },
    lastUpdated: { text: 'Terakhir diperbarui' },
    darkModeSwitchLabel: 'Tampilan',
    returnToTopLabel: 'Kembali ke atas',
    sidebarMenuLabel: 'Menu',

    socialLinks: [
      { icon: 'github', link: 'https://github.com/sivi-shahab/Speech-to-Text-Quality-Control-in-Bank' },
    ],

    footer: {
      message: 'Dokumentasi internal — jangan cantumkan data nasabah di halaman mana pun.',
    },
  },
})
