#!/usr/bin/env python3
"""
Udemy Transcript Scraper - GUI Application
A beautiful interface for downloading Udemy course transcripts.
"""
import os
import sys
import threading
import time
import customtkinter as ctk
from tkinter import messagebox, filedialog
from datetime import datetime

# Add project dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scraper import UdemyScraper
from progress_tracker import ProgressTracker

# ─── Theme ───────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Colors
BG_DARK = "#1a1a2e"
BG_MID = "#16213e"
BG_CARD = "#0f3460"
ACCENT = "#e94560"
ACCENT_HOVER = "#ff6b81"
TEXT_PRIMARY = "#ffffff"
TEXT_SECONDARY = "#a0a0b0"
SUCCESS = "#00d2d3"
WARNING = "#feca57"
ERROR = "#ff6b6b"


class SectionProgress(ctk.CTkFrame):
    """A single section progress bar with label."""

    def __init__(self, master, section_title: str, total_lectures: int, **kwargs):
        super().__init__(master, fg_color=BG_CARD, corner_radius=8, **kwargs)

        self.total = total_lectures
        self.completed = 0

        # Header row
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(8, 2))

        self.title_label = ctk.CTkLabel(
            header, text=section_title, font=("", 12, "bold"),
            text_color=TEXT_PRIMARY, anchor="w"
        )
        self.title_label.pack(side="left", fill="x", expand=True)

        self.count_label = ctk.CTkLabel(
            header, text=f"0/{total_lectures}",
            font=("", 11), text_color=TEXT_SECONDARY
        )
        self.count_label.pack(side="right")

        # Progress bar
        self.progress = ctk.CTkProgressBar(
            self, height=6, corner_radius=3,
            progress_color=ACCENT, fg_color="#2a2a4a"
        )
        self.progress.pack(fill="x", padx=12, pady=(0, 8))
        self.progress.set(0)

        # Status label
        self.status_label = ctk.CTkLabel(
            self, text="Pending", font=("", 10),
            text_color=TEXT_SECONDARY, anchor="w"
        )
        self.status_label.pack(fill="x", padx=12, pady=(0, 8))

    def update_progress(self, completed: int, status: str = ""):
        self.completed = completed
        pct = completed / self.total if self.total > 0 else 0
        self.progress.set(pct)
        self.count_label.configure(text=f"{completed}/{self.total}")
        if status:
            self.status_label.configure(text=status[:80])
            if "Saved" in status:
                self.status_label.configure(text_color=SUCCESS)
            elif "No captions" in status:
                self.status_label.configure(text_color=WARNING)
            elif "Failed" in status:
                self.status_label.configure(text_color=ERROR)
            else:
                self.status_label.configure(text_color=TEXT_SECONDARY)


class App(ctk.CTk):
    """Main application window."""

    def __init__(self):
        super().__init__()

        self.title("Udemy Transcript Scraper")
        self.geometry("900x750")
        self.minsize(800, 600)
        self.configure(fg_color=BG_DARK)

        self.scraper = None
        self.tracker = None
        self.is_running = False
        self.stop_flag = False
        self.thread = None
        self.section_widgets = []

        self._build_ui()

    def _build_ui(self):
        # ─── Main container ───────────────────────────────────
        main = ctk.CTkFrame(self, fg_color=BG_DARK)
        main.pack(fill="both", expand=True, padx=16, pady=16)

        # ─── Header ───────────────────────────────────────────
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            header, text="Udemy Transcript Scraper",
            font=("", 22, "bold"), text_color=ACCENT
        ).pack(side="left")

        ctk.CTkLabel(
            header, text="Download course transcripts via browser",
            font=("", 12), text_color=TEXT_SECONDARY
        ).pack(side="left", padx=(12, 0), pady=(4, 0))

        # ─── Input Card ───────────────────────────────────────
        input_card = ctk.CTkFrame(main, fg_color=BG_MID, corner_radius=10)
        input_card.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            input_card, text="Course URL",
            font=("", 13, "bold"), text_color=TEXT_PRIMARY
        ).pack(anchor="w", padx=16, pady=(12, 4))

        url_row = ctk.CTkFrame(input_card, fg_color="transparent")
        url_row.pack(fill="x", padx=16, pady=(0, 4))

        self.url_entry = ctk.CTkEntry(
            url_row, placeholder_text="https://www.udemy.com/course/your-course/learn",
            height=40, font=("", 13),
            fg_color="#1a1a3e", border_color="#3a3a5e",
            text_color=TEXT_PRIMARY
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.start_btn = ctk.CTkButton(
            url_row, text="Start Scraping", width=140, height=40,
            font=("", 13, "bold"), fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._on_start
        )
        self.start_btn.pack(side="right")

        self.resume_btn = ctk.CTkButton(
            url_row, text="Resume", width=100, height=40,
            font=("", 13, "bold"), fg_color="#2ecc71", hover_color="#27ae60",
            command=self._on_resume, state="disabled"
        )
        self.resume_btn.pack(side="right", padx=(0, 8))

        # ─── Output dir row ──────────────────────────────────
        dir_row = ctk.CTkFrame(input_card, fg_color="transparent")
        dir_row.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(
            dir_row, text="Save to:", font=("", 11), text_color=TEXT_SECONDARY
        ).pack(side="left")

        self.dir_entry = ctk.CTkEntry(
            dir_row, height=32, font=("", 11),
            fg_color="#1a1a3e", border_color="#3a3a5e",
            text_color=TEXT_PRIMARY
        )
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=(8, 8))
        self.dir_entry.insert(0, os.path.expanduser("~/Desktop/Udemy_Transcripts"))

        self.browse_btn = ctk.CTkButton(
            dir_row, text="Browse", width=70, height=32,
            font=("", 11), fg_color="#3a3a5e", hover_color="#4a4a6e",
            command=self._browse_directory
        )
        self.browse_btn.pack(side="right")

        # ─── Batch size row ──────────────────────────────────
        batch_row = ctk.CTkFrame(input_card, fg_color="transparent")
        batch_row.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(
            batch_row, text="Batch size:", font=("", 11), text_color=TEXT_SECONDARY
        ).pack(side="left")

        self.batch_var = ctk.StringVar(value="5")
        self.batch_slider = ctk.CTkSlider(
            batch_row, from_=1, to=15, number_of_steps=14,
            width=150,
            command=self._on_batch_change
        )
        self.batch_slider.set(5)
        self.batch_slider.pack(side="left", padx=(8, 4))

        self.batch_label = ctk.CTkLabel(
            batch_row, text="5", font=("", 11, "bold"), text_color=ACCENT, width=30
        )
        self.batch_label.pack(side="left")

        ctk.CTkLabel(
            batch_row, text="lectures per batch (higher = faster, may fail)", font=("", 10),
            text_color=TEXT_SECONDARY
        ).pack(side="left", padx=(8, 0))

        # ─── Thread count row ──────────────────────────────────
        thread_row = ctk.CTkFrame(input_card, fg_color="transparent")
        thread_row.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(
            thread_row, text="Threads:", font=("", 11), text_color=TEXT_SECONDARY
        ).pack(side="left")

        self.thread_var = ctk.StringVar(value="3")
        self.thread_slider = ctk.CTkSlider(
            thread_row, from_=1, to=6, number_of_steps=5,
            width=100,
            command=self._on_thread_change
        )
        self.thread_slider.set(3)
        self.thread_slider.pack(side="left", padx=(8, 4))

        self.thread_label = ctk.CTkLabel(
            thread_row, text="3", font=("", 11, "bold"), text_color=ACCENT, width=30
        )
        self.thread_label.pack(side="left")

        ctk.CTkLabel(
            thread_row, text="parallel workers", font=("", 10),
            text_color=TEXT_SECONDARY
        ).pack(side="left", padx=(8, 0))

        # ─── Overall Progress ────────────────────────────────
        progress_card = ctk.CTkFrame(main, fg_color=BG_MID, corner_radius=10)
        progress_card.pack(fill="x", pady=(0, 12))

        prog_header = ctk.CTkFrame(progress_card, fg_color="transparent")
        prog_header.pack(fill="x", padx=16, pady=(12, 4))

        self.overall_label = ctk.CTkLabel(
            prog_header, text="Overall Progress",
            font=("", 13, "bold"), text_color=TEXT_PRIMARY
        )
        self.overall_label.pack(side="left")

        self.overall_count = ctk.CTkLabel(
            prog_header, text="0 / 0",
            font=("", 12), text_color=TEXT_SECONDARY
        )
        self.overall_count.pack(side="right")

        self.overall_bar = ctk.CTkProgressBar(
            progress_card, height=10, corner_radius=5,
            progress_color=ACCENT, fg_color="#2a2a4a"
        )
        self.overall_bar.pack(fill="x", padx=16, pady=(0, 4))
        self.overall_bar.set(0)

        self.overall_status = ctk.CTkLabel(
            progress_card, text="Ready",
            font=("", 11), text_color=TEXT_SECONDARY
        )
        self.overall_status.pack(anchor="w", padx=16, pady=(0, 12))

        # ─── Stop Button ──────────────────────────────────────
        btn_row = ctk.CTkFrame(progress_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 12))

        self.stop_btn = ctk.CTkButton(
            btn_row, text="Stop", width=100, height=32,
            font=("", 12, "bold"), fg_color=ERROR, hover_color="#cc5555",
            command=self._on_stop, state="disabled"
        )
        self.stop_btn.pack(side="right")

        # ─── Sections Progress (Scrollable) ───────────────────
        sections_label = ctk.CTkLabel(
            main, text="Section Progress",
            font=("", 13, "bold"), text_color=TEXT_PRIMARY
        )
        sections_label.pack(anchor="w", pady=(0, 6))

        self.sections_frame = ctk.CTkScrollableFrame(
            main, fg_color=BG_MID, corner_radius=10
        )
        self.sections_frame.pack(fill="both", expand=True, pady=(0, 12))

        # ─── Log Area ────────────────────────────────────────
        log_label = ctk.CTkLabel(
            main, text="Activity Log",
            font=("", 13, "bold"), text_color=TEXT_PRIMARY
        )
        log_label.pack(anchor="w", pady=(0, 4))

        self.log_box = ctk.CTkTextbox(
            main, fg_color=BG_MID, corner_radius=10,
            font=("Consolas", 11), text_color=TEXT_SECONDARY,
            height=120
        )
        self.log_box.pack(fill="x")

    # ─── Browse Directory ────────────────────────────────────
    def _browse_directory(self):
        """Open file dialog to select output directory."""
        path = filedialog.askdirectory(
            title="Select Output Directory",
            initialdir=self.dir_entry.get().strip() or os.path.expanduser("~/Desktop")
        )
        if path:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, path)

    def _on_batch_change(self, value):
        """Update batch size label when slider moves."""
        val = int(float(value))
        self.batch_var.set(str(val))
        self.batch_label.configure(text=str(val))

    def _on_thread_change(self, value):
        """Update thread count label when slider moves."""
        val = int(float(value))
        self.thread_var.set(str(val))
        self.thread_label.configure(text=str(val))

    # ─── Logging ──────────────────────────────────────────────
    def _log(self, msg: str):
        """Thread-safe logging to the textbox."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}\n"

        def _append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.after(0, _append)

    # ─── Progress Callbacks (called from scraper thread) ──────
    def _on_progress(self, section_idx: int, lecture_idx: int, status: str, message: str):
        """Called by scraper for each lecture processed."""
        def _update():
            if section_idx >= 0 and section_idx < len(self.section_widgets):
                widget = self.section_widgets[section_idx]
                completed = lecture_idx + 1
                widget.update_progress(completed, message)

                # Update overall
                total_done = self.tracker.completed_count
                total_all = self.tracker.state.get("total_lectures", 1)
                self.overall_bar.set(total_done / total_all)
                self.overall_count.configure(text=f"{total_done} / {total_all}")
                self.overall_status.configure(text=message[:100])

        self.after(0, _update)

    # ─── Button Handlers ──────────────────────────────────────
    def _on_start(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("Input needed", "Please enter a course URL.")
            return

        output_dir = self.dir_entry.get().strip()
        if not output_dir:
            messagebox.showwarning("Input needed", "Please enter an output directory.")
            return

        self._start_scraping(url, output_dir, fresh=True)

    def _on_resume(self):
        if not self.tracker or not self.tracker.is_resumable:
            messagebox.showinfo("Nothing to resume", "No previous session found to resume.")
            return

        info = self.tracker.get_resume_info()
        url = f"https://www.udemy.com/course/{info['course_slug']}/learn"
        output_dir = self.dir_entry.get().strip() or self.tracker.state.get("output_dir", "")

        self._start_scraping(url, output_dir, fresh=False)

    def _on_stop(self):
        self.stop_flag = True
        self._log("Stopping... will stop after current lecture.")
        self.stop_btn.configure(state="disabled", text="Stopping...")

    def _start_scraping(self, url: str, output_dir: str, fresh: bool):
        """Initialize and start the scraping thread."""
        self.is_running = True
        self.stop_flag = False
        self.start_btn.configure(state="disabled")
        self.resume_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.url_entry.configure(state="disabled")

        # Clear section widgets
        for w in self.sections_frame.winfo_children():
            w.destroy()
        self.section_widgets = []

        self._log(f"Starting scrape: {url}")

        def run():
            try:
                self.scraper = UdemyScraper(log_callback=self._log)
                self.tracker = ProgressTracker(output_dir)

                # Navigate
                self._log("Connecting to browser...")
                self.scraper.connect_and_navigate(url)

                # Discover
                self._log("Discovering course structure...")
                info = self.scraper.discover_course()

                # Check for resume
                self.tracker.init_course(
                    self.scraper.course_slug,
                    self.scraper.course_id,
                    self.scraper.course_title,
                    len(self.scraper.sections),
                    sum(len(s["lectures"]) for s in self.scraper.sections),
                    output_dir,
                )

                # Create folder structure
                self.scraper.create_folder_structure(output_dir)

                # Build section progress widgets in GUI
                def _build_sections():
                    for si, section in enumerate(self.scraper.sections):
                        total = len(section["lectures"])
                        done = 0
                        # Count already done
                        for li, lec in enumerate(section["lectures"]):
                            if self.tracker.is_lecture_done(lec["id"]):
                                done += 1

                        widget = SectionProgress(
                            self.sections_frame,
                            f"{si+1}. {section['title']}",
                            total
                        )
                        widget.pack(fill="x", padx=4, pady=2)
                        if done > 0:
                            widget.update_progress(done, f"Resumed ({done}/{total} done)")
                        self.section_widgets.append(widget)

                self.after(0, _build_sections)
                time.sleep(0.5)  # Let GUI update

                # Total counts
                total_lectures = sum(len(s["lectures"]) for s in self.scraper.sections)
                completed_before = self.tracker.completed_count
                self.after(0, lambda: self.overall_count.configure(
                    text=f"{completed_before} / {total_lectures}"
                ))
                self.after(0, lambda: self.overall_status.configure(
                    text=f"Course: {self.scraper.course_title}"
                ))

                # Scrape lectures - parallel mode
                batch_size = int(self.batch_var.get())
                num_threads = int(self.thread_var.get())
                self._log(f"Starting parallel scrape: {num_threads} workers, batch size {batch_size}")

                self.scraper.scrape_parallel(
                    base_dir=output_dir,
                    progress_callback=self._on_progress,
                    stop_check=lambda: self.stop_flag,
                    batch_size=batch_size,
                    num_threads=num_threads,
                    skip_discovery=True,
                )

                # Done
                completed = self.tracker.completed_count if self.tracker else 0
                failed = self.tracker.failed_count if self.tracker else 0
                self._log(f"\nFinished! {completed} completed, {failed} failed.")
                self.after(0, lambda: self.overall_status.configure(
                    text=f"Done! {completed} completed, {failed} failed"
                ))
                self.after(0, lambda: self.overall_bar.set(1.0))

            except Exception as e:
                self._log(f"Fatal error: {e}")
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
            finally:
                self.is_running = False
                self.after(0, self._reset_buttons)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def _reset_buttons(self):
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled", text="Stop")
        self.url_entry.configure(state="normal")

        # Check if resume is possible
        if self.tracker and self.tracker.is_resumable:
            self.resume_btn.configure(state="normal")
        else:
            self.resume_btn.configure(state="disabled")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
