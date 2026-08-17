from __future__ import annotations

import queue
import threading
from pathlib import Path
from tkinter import Tk, filedialog, messagebox
from tkinter import ttk

from pbix_doc.runtime import RuntimePolicyViolation

from .pipeline import CancellationToken, ConversionCancelled, OutputFailure, generate_documentation


def status_presentation(status: str) -> tuple[str, str]:
    return {
        "OK": ("info", "Готово"),
        "PARTIAL": ("warning", "Готово с предупреждениями"),
        "FAILED": ("error", "Ошибка"),
    }.get(status, ("error", "Ошибка"))


def launch_gui() -> int:
    exit_code = 0
    root = Tk()
    root.withdraw()
    root.update_idletasks()
    input_name = filedialog.askopenfilename(
        parent=root,
        title="Выберите export Data Compass",
        filetypes=(("Data Compass export", "*.zip"), ("Все файлы", "*.*")),
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
    root.title("Data Compass Documentation")
    root.resizable(False, False)
    root.geometry("580x190")
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)
    status_text = ttk.Label(frame, text="Подготовка…", anchor="w")
    status_text.pack(fill="x", pady=(0, 12))
    progress_bar = ttk.Progressbar(frame, mode="indeterminate")
    progress_bar.pack(fill="x", pady=(0, 12))
    progress_bar.start(12)
    cancel_button = ttk.Button(frame, text="Отмена")
    cancel_button.pack(anchor="e")

    messages: queue.Queue[tuple[str, object]] = queue.Queue()
    token = CancellationToken()

    def progress(value: str) -> None:
        messages.put(("progress", value))

    def cancel() -> None:
        if token.cancel():
            cancel_button.state(["disabled"])
            status_text.configure(text="Отмена операции…")
        else:
            status_text.configure(text="Документ уже публикуется; дождитесь завершения")

    cancel_button.configure(command=cancel)
    root.protocol("WM_DELETE_WINDOW", cancel)

    def worker() -> None:
        try:
            result = generate_documentation(input_path, Path(output_name), progress, token)
            messages.put(("success", result))
        except ConversionCancelled:
            messages.put(("cancelled", None))
        except RuntimePolicyViolation as error:
            messages.put(("policy", error))
        except OutputFailure as error:
            messages.put(("output", error))
        except Exception as error:
            messages.put(("error", error))

    def finish_dialog(kind: str, title: str, text: str) -> None:
        callback = {"info": messagebox.showinfo, "warning": messagebox.showwarning, "error": messagebox.showerror}[kind]
        callback(title, text, parent=root)

    def poll() -> None:
        nonlocal exit_code
        try:
            while True:
                kind, payload = messages.get_nowait()
                if kind == "progress":
                    status_text.configure(text=str(payload))
                    continue
                progress_bar.stop()
                if kind == "success":
                    status = payload.status  # type: ignore[union-attr]
                    presentation, title = status_presentation(status)
                    exit_code = 0 if status == "OK" else 2
                    finish_dialog(presentation, title, f"Документация сохранена:\n{output_name}\n\nСтатус: {status}")
                elif kind == "cancelled":
                    exit_code = 1
                    finish_dialog("info", "Отменено", "Выходной файл не изменён.")
                elif kind == "policy":
                    exit_code = 4
                    finish_dialog("error", "Операция заблокирована", "Операция запрещена политикой безопасности VDI.")
                elif kind == "output":
                    exit_code = 3
                    finish_dialog("error", "Ошибка сохранения", "Не удалось безопасно сохранить DOCX.")
                else:
                    exit_code = 1
                    finish_dialog("error", "Ошибка входного файла", "Не удалось обработать export bundle.")
                root.destroy()
                return
        except queue.Empty:
            root.after(100, poll)

    threading.Thread(target=worker, daemon=True).start()
    root.after(100, poll)
    root.mainloop()
    return exit_code
