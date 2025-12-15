import datetime
import os
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from progressive_enroll import get_progress_for_ids, remove_user, clear_all_users
import cv2
from PIL import Image, ImageTk

from config_manager import (
    security_init,
    load_config,
    hash_password,
    generate_registration_pin as cfg_generate_registration_pin,
    remove_pins_for_id,
    wipe_all_user_pins,
)

from fingerprint.fingerprint_sensor import (
    load_used_ids,
    delete_used_id,
    clear_used_ids,
    delete_all_fingerprints,
)

from camera.camera_module import get_latest_frame_and_status

# ---------------------------
# Inicijalizacija sigurnog okruženja
# ---------------------------
security_init()

# ---------------------------
# Direktoriji / log datoteke
# ---------------------------
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

def _today_log_path() -> str:
    return os.path.join(LOG_DIR, f"log_{datetime.date.today()}.txt")

SYSTEM_LOG_FILE = _today_log_path()
if not os.path.exists(SYSTEM_LOG_FILE):
    with open(SYSTEM_LOG_FILE, "w"):
        pass

# ---------------------------
# Globalne varijable GUI-a
# ---------------------------
gui_log_area = None
config = load_config()
current_log_entries = []
status_label = None
camera_label = None

# ---------------------------
# Log helperi
# ---------------------------
def log_event(event: str, log_type: str = "G") -> None:
    """Zapiši u log i (ako GUI radi) sigurno dodaj u prikaz."""
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] [{log_type}] {event}\n"
    with open(SYSTEM_LOG_FILE, "a") as f:
        f.write(entry)
    current_log_entries.append(entry)
    if gui_log_area:
        gui_log_area.after(0, lambda e=entry: append_to_gui_log(e))

def append_to_gui_log(entry: str) -> None:
    if not gui_log_area:
        return
    gui_log_area.insert(tk.END, entry)
    gui_log_area.see(tk.END)

# ---------------------------
# GUI
# ---------------------------
def start_gui() -> None:
    def update_camera_feed():
        """Periodički dohvat frame-a i statusa iz camera_module-a."""
        frame, status = get_latest_frame_and_status()

        if frame is not None and camera_label is not None:
            # BGR -> RGB, smanji za prikaz
            image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = cv2.resize(image, (320, 240))
            imgtk = ImageTk.PhotoImage(image=Image.fromarray(image))
            camera_label.imgtk = imgtk  # spriječi GC
            camera_label.config(image=imgtk)

        if status_label is not None:
            status_label.config(
                text=status,
                fg=("red" if str(status).lower().startswith("record") else "green"),
            )

        # osvježavaj ~5x u sekundi
        if camera_label is not None:
            camera_label.after(200, update_camera_feed)

    def show_main_gui():
        
        def show_progress_status():
            popup = tk.Toplevel()
            popup.title("Status progresivnog upisa")
            popup.resizable(False, False)

            cols = ("ID", "Status", "Dataset spreman")
            tree = ttk.Treeview(popup, columns=cols, show="headings", height=12)
            for c in cols:
                tree.heading(c, text=c)
                tree.column(c, width=140, anchor="center")
            tree.pack(padx=10, pady=10)

            try:
                # 1) Uzmemo sve REGISTRIRANE ID-jeve sa senzora
                ids = sorted(load_used_ids())  # npr. {1, 2, 5, ...}
                # 2) Dohvatimo napredak za te ID-jeve (i oni bez videa dobit će 0/target)
                rows = get_progress_for_ids(ids) if ids else []

                # 3) Puni tablicu u formatu: ID | "x/target" | True/False
                for r in rows:
                    status_text = f'{r["count"]}/{r["target"]}'
                    ready_text = "True" if r["ready"] else "False"
                    tree.insert("", tk.END, values=(r["user_id"], status_text, ready_text))

                if not rows:
                    # Nema ID-jeva — prikažemo prazan grid (bez errora)
                    pass

            except Exception as e:
                messagebox.showerror("Greška", f"Ne mogu učitati status.\n{e}")
        def on_generate_registration_pin():
            """Generiraj jednokratni registracijski PIN i prikaži ga korisniku."""
            try:
                pin = cfg_generate_registration_pin()
                log_event("Generiran registracijski PIN (sigurno pohranjen)", "P")

                # Custom popup (bez ponovnog prikaza nakon zatvaranja)
                popup = tk.Toplevel()
                popup.title("Registracijski PIN")
                popup.resizable(False, False)
                popup.transient()

                tk.Label(popup, text="Vaš registracijski PIN:", font=("Arial", 12)).pack(
                    padx=16, pady=(16, 4)
                )

                pin_var = tk.StringVar(value=pin)
                entry = tk.Entry(
                    popup,
                    textvariable=pin_var,
                    font=("Consolas", 20),
                    justify="center",
                    width=10,
                    state="readonly",
                )
                entry.pack(padx=16, pady=(0, 8))

                tk.Label(
                    popup,
                    text=(
                        "Zapamtite i zapišite PIN — nakon zatvaranja ovog prozora\n"
                        "PIN se više neće moći ponovno prikazati.\n"
                        "PIN vrijedi dok se ne iskoristi pri registraciji."
                    ),
                    justify="center",
                ).pack(padx=16, pady=(0, 12))

                btn_frame = tk.Frame(popup)
                btn_frame.pack(pady=(0, 16))

                def do_copy():
                    popup.clipboard_clear()
                    popup.clipboard_append(pin)
                    popup.update()  # da clipboard preživi zatvaranje
                    copy_btn.config(text="Kopirano ✓")

                copy_btn = tk.Button(btn_frame, text="Copy to clipboard", width=18, command=do_copy)
                copy_btn.grid(row=0, column=0, padx=6)

                tk.Button(btn_frame, text="Zatvori", width=12, command=popup.destroy)\
                    .grid(row=0, column=1, padx=6)

                popup.grab_set()
                popup.focus_set()

            except Exception as e:
                messagebox.showerror("Greška", f"Nije moguće generirati PIN.\nDetalji: {e}")

        def load_selected_log(_=None):
            selected = log_selector.get().strip()
            path = os.path.join(LOG_DIR, selected) if selected else SYSTEM_LOG_FILE
            if os.path.exists(path):
                with open(path, "r") as f:
                    if gui_log_area:
                        gui_log_area.delete(1.0, tk.END)
                    current_log_entries.clear()
                    current_log_entries.extend(f.readlines())
                    apply_filter()

        def apply_filter(_=None):
            selected_filter = filter_selector.get()
            if not gui_log_area:
                return
            gui_log_area.delete(1.0, tk.END)
            flt = (None if selected_filter == "Sve" else f"[{selected_filter}]")
            for entry in current_log_entries:
                if flt is None or flt in entry:
                    gui_log_area.insert(tk.END, entry)
            gui_log_area.see(tk.END)

        def manage_ids():
            """Popup za upravljanje ID-jevima + brisanje PIN-ova za ID."""
            popup = tk.Toplevel()
            popup.title("Upravljanje ID-jevima")

            tk.Label(popup, text="Zabilježeni ID-jevi:").pack(pady=(10, 0))
            listbox = tk.Listbox(popup, width=30, height=10, exportselection=False)
            listbox.pack(pady=5)

            def refresh_list():
                listbox.delete(0, tk.END)
                for fid in sorted(load_used_ids()):
                    listbox.insert(tk.END, f"ID {fid}")
                delete_button.config(state="disabled")

            def on_select(_evt):
                delete_button.config(state=("normal" if listbox.curselection() else "disabled"))

            def delete_selected():
                sel = listbox.curselection()
                if not sel:
                    return
                value = listbox.get(sel[0])
                id_to_delete = int(value.split()[1])

                # 1) otisak s uređaja + iz lokalne liste
                delete_used_id(id_to_delete)
                # 2) obriši sve korisničke PIN-ove vezane uz taj ID
                removed = remove_pins_for_id(id_to_delete)
                # 3) OBRIŠI I PROGRES za taj ID
                try:
                    remove_user(id_to_delete)
                except Exception:
                    pass

                messagebox.showinfo(
                    "Uspjeh",
                    f"ID {id_to_delete} je obrisan.\nObrisano korisničkih PIN-ova: {removed}.",
                )
                refresh_list()

            def delete_all():
                if messagebox.askyesno(
                    "Potvrda",
                    "Stvarno obrisati SVE ID-jeve, otiske i korisničke PIN-ove?",
                ):
                    delete_all_fingerprints()
                    clear_used_ids()
                    wipe_all_user_pins()
                    # počisti i progres
                    try:
                        clear_all_users()
                    except Exception:
                        pass
                    refresh_list()
                    messagebox.showinfo(
                        "Uspjeh", "Svi ID-jevi, otisci i korisnički PIN-ovi su obrisani."
                    )

            listbox.bind("<<ListboxSelect>>", on_select)
            delete_button = tk.Button(popup, text="Obriši odabrani ID", command=delete_selected, state="disabled")
            delete_button.pack(pady=5)
            tk.Button(popup, text="Obriši sve ID-jeve i otiske", command=delete_all).pack(pady=(0, 10))

            refresh_list()

        # ---------------- UI Layout ----------------
        global gui_log_area, camera_label, status_label

        root = tk.Tk()
        root.title("Security System GUI")

        main_frame = tk.Frame(root)
        main_frame.pack(padx=10, pady=10)

        left_frame = tk.Frame(main_frame)
        left_frame.grid(row=0, column=0, padx=10, sticky="n")

        right_frame = tk.Frame(main_frame)
        right_frame.grid(row=0, column=1, padx=10, sticky="n")

        # Gumbi (lijevo)
        tk.Button(
            left_frame,
            text="Generiraj registracijski PIN",
            width=30,
            command=on_generate_registration_pin,
        ).grid(row=0, column=0, pady=(0, 10))

        tk.Label(left_frame, text="Zapisnik sustava (log)").grid(row=1, column=0)
        gui_log_area = scrolledtext.ScrolledText(left_frame, width=50, height=20, wrap=tk.WORD)
        gui_log_area.grid(row=2, column=0)

        tk.Label(left_frame, text="Odaberi log datoteku:").grid(row=3, column=0, pady=(10, 0))
        files = sorted(os.listdir(LOG_DIR))
        log_selector = ttk.Combobox(left_frame, width=47, values=files)
        today_name = os.path.basename(SYSTEM_LOG_FILE)
        if today_name not in files:
            files.append(today_name)
            log_selector.configure(values=sorted(files))
        log_selector.set(today_name)
        log_selector.grid(row=4, column=0, pady=5)

        tk.Label(left_frame, text="Filtriraj po tipu poruke:").grid(row=5, column=0, pady=(10, 0))
        filter_selector = ttk.Combobox(left_frame, width=47, values=["Sve", "G", "P", "C", "F"])
        filter_selector.set("Sve")
        filter_selector.grid(row=6, column=0, pady=5)

        tk.Button(left_frame, text="Upravljanje ID-jevima", width=30, command=manage_ids)\
            .grid(row=7, column=0, pady=(10, 0))
        
        tk.Button(left_frame, text="Status progresivnog upisa", width=30, command=show_progress_status)\
            .grid(row=8, column=0, pady=(10, 0))
    
        # Kamera (desno)
        tk.Label(right_frame, text="Kamera").pack()
        camera_label = tk.Label(right_frame)
        camera_label.pack()

        status_label = tk.Label(right_frame, text="Not Recording", fg="green", font=("Arial", 12))
        status_label.pack(pady=5)

        # Bindovi i inicijalni load
        log_selector.bind("<<ComboboxSelected>>", load_selected_log)
        filter_selector.bind("<<ComboboxSelected>>", apply_filter)

        load_selected_log()
        update_camera_feed()
        log_event("GUI pokrenut", "G")
        root.mainloop()

    def try_login():
        entered_user = user_entry.get().strip()
        entered_pass = hash_password(pass_entry.get())
        if entered_user == config.get("username") and entered_pass == config.get("password_hash"):
            login_win.destroy()
            show_main_gui()
        else:
            messagebox.showerror("Greška", "Pogrešno korisničko ime ili lozinka")

    # ----- Login prozor -----
    login_win = tk.Tk()
    login_win.title("Prijava")
    tk.Label(login_win, text="Korisničko ime:").pack(pady=5)
    user_entry = tk.Entry(login_win)
    user_entry.pack(pady=5)
    tk.Label(login_win, text="Lozinka:").pack(pady=5)
    pass_entry = tk.Entry(login_win, show="*")
    pass_entry.pack(pady=5)
    tk.Button(login_win, text="Prijavi se", command=try_login).pack(pady=10)
    login_win.mainloop()