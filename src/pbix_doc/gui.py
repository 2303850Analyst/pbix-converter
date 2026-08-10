from __future__ import annotations

import queue
import threading
from pathlib import Path
from tkinter import Tk, filedialog, messagebox
from tkinter import ttk

from .pipeline import MAX_EXAMPLE_VALUES, Options, generate_documentation
from .projector import overall_status
from .runtime import RuntimePolicyViolation


def launch_gui(example_limit: int = 5) -> int:
    exit_code = 0
    root = Tk()
    root.withdraw()
    root.update_idletasks()

    input_name = filedialog.askopenfilename(
        parent=root,
        title="Выберите отчёт Power BI",
        filetypes=(("Power BI report", "*.pbix"), ("Все файлы", "*.*")),
    )
    if not input_name:
        root.destroy()
        return 0

    input_path = Path(input_name)
    output_name = filedialog.asksaveasfilename(
        parent=root,
        title="Куда сохранить документацию",
        initialdir=input_path.parent,
        initialfile=f"{input_path.stem}_documentation.docx",
        defaultextension=".docx",
        filetypes=(("Word document", "*.docx"),),
    )
    if not output_name:
        root.destroy()
        return 0

    root.deiconify()
    root.title("PBIX Documentation")
    root.resizable(False, False)
    root.geometry("560x150")
    root.protocol("WM_DELETE_WINDOW", lambda: None)

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)
    status_text = ttk.Label(frame, text="Подготовка…", anchor="w")
    status_text.pack(fill="x", pady=(0, 12))
    progress_bar = ttk.Progressbar(frame, mode="indeterminate")
    progress_bar.pack(fill="x")
    progress_bar.start(12)

    messages: queue.Queue[tuple[str, object]] = queue.Queue()

    def progress(value: str) -> None:
        messages.put(("progress", value))

    def worker() -> None:
        try:
            model = generate_documentation(
                input_path,
                Path(output_name),
                Options(example_limit=min(MAX_EXAMPLE_VALUES, max(0, example_limit))),
                progress,
            )
            messages.put(("success", model))
        except RuntimePolicyViolation as error:
            messages.put(("policy_error", error))
        except Exception as error:
            messages.put(("error", error))

    def poll() -> None:
        nonlocal exit_code
        try:
            while True:
                kind, payload = messages.get_nowait()
                if kind == "progress":
                    status_text.configure(text=str(payload))
                elif kind == "success":
                    progress_bar.stop()
                    status = overall_status(payload)  # type: ignore[arg-type]
                    exit_code = 0 if status == "OK" else 2
                    messagebox.showinfo(
                        "Готово",
                        f"Документация сохранена:\n{output_name}\n\nСтатус: {status}",
                        parent=root,
                    )
                    root.destroy()
                    return
                elif kind == "policy_error":
                    progress_bar.stop()
                    exit_code = 4
                    messagebox.showerror("Критическая ошибка политики VDI", str(payload), parent=root)
                    root.destroy()
                    return
                elif kind == "error":
                    progress_bar.stop()
                    exit_code = 1
                    messagebox.showerror("Ошибка", str(payload), parent=root)
                    root.destroy()
                    return
        except queue.Empty:
            pass
        root.after(100, poll)

    threading.Thread(target=worker, daemon=True).start()
    root.after(100, poll)
    root.mainloop()
    return exit_code
