import os
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from typing import Optional, Dict, Any
from datetime import datetime


class PDFBuilder:
    """Utility class for building PDF documents from transcribed text."""

    def __init__(self, pagesize=letter):
        self.pagesize           = pagesize
        self.width, self.height = pagesize

    def create_transcription_pdf(
        self,
        transcribed_text: str,
        audio_filename:   Optional[str]            = None,
        metadata:         Optional[Dict[str, Any]] = None,
    ) -> BytesIO:
        pdf_buffer = BytesIO()
        c          = canvas.Canvas(pdf_buffer, pagesize=self.pagesize)
        c.setFont("Helvetica", 11)
        self._add_header(c)
        self._add_transcribed_text(c, transcribed_text)
        c.save()
        pdf_buffer.seek(0)
        return pdf_buffer

    def _add_header(self, canvas_obj: canvas.Canvas):
        """
        Header hanya berisi judul 'Transcription Report'.
        Semua metadata (Audio File, Generated, Mode, Language, Speakers,
        Proc.Time, Durasi Bicara, Profil Emosi) sudah ada di transcribed_text
        yang dihasilkan oleh format_output_with_speakers().
        """
        y = self.height - 1.0 * inch
        canvas_obj.setFont("Helvetica-Bold", 14)
        canvas_obj.drawString(1 * inch, y, "Transcription Report")
        y -= 0.3 * inch
        self._header_bottom_y = y

    def _add_transcribed_text(self, canvas_obj: canvas.Canvas, transcribed_text: str):
        left_margin  = 1 * inch
        right_margin = 1 * inch
        y_position   = getattr(self, "_header_bottom_y", self.height - 1.6 * inch)
        line_height  = 0.2 * inch
        max_width    = self.width - left_margin - right_margin

        canvas_obj.setFont("Helvetica", 11)

        for paragraph in transcribed_text.split("\n"):
            if paragraph.strip() == "":
                y_position -= line_height
                if y_position < 1 * inch:
                    self._add_new_page(canvas_obj)
                    y_position = self.height - 1 * inch
                continue

            words        = paragraph.split(" ")
            current_line = ""

            for word in words:
                chunks = (
                    self._split_word_to_fit(canvas_obj, word, max_width)
                    if canvas_obj.stringWidth(word, "Helvetica", 11) > max_width
                    else [word]
                )
                for chunk in chunks:
                    test_line = (current_line + " " + chunk) if current_line else chunk
                    if canvas_obj.stringWidth(test_line, "Helvetica", 11) <= max_width:
                        current_line = test_line
                    else:
                        canvas_obj.drawString(left_margin, y_position, current_line)
                        y_position -= line_height
                        if y_position < 1 * inch:
                            self._add_new_page(canvas_obj)
                            y_position = self.height - 1 * inch
                        current_line = chunk

            if current_line:
                canvas_obj.drawString(left_margin, y_position, current_line)
                y_position -= line_height
                if y_position < 1 * inch:
                    self._add_new_page(canvas_obj)
                    y_position = self.height - 1 * inch

    def _split_word_to_fit(self, canvas_obj, word, max_width):
        chunks, start_idx = [], 0
        while start_idx < len(word):
            low, high, best = 1, len(word) - start_idx, 1
            while low <= high:
                mid    = (low + high) // 2
                substr = word[start_idx : start_idx + mid]
                if canvas_obj.stringWidth(substr, "Helvetica", 11) <= max_width:
                    best = mid; low = mid + 1
                else:
                    high = mid - 1
            chunks.append(word[start_idx : start_idx + best])
            start_idx += best
        return chunks

    def _add_new_page(self, canvas_obj):
        canvas_obj.showPage()
        canvas_obj.setFont("Helvetica", 11)


def create_transcription_pdf(
    transcribed_text: str,
    audio_filename:   Optional[str]            = None,
    metadata:         Optional[Dict[str, Any]] = None,
) -> BytesIO:
    builder = PDFBuilder()
    return builder.create_transcription_pdf(transcribed_text, audio_filename, metadata)
