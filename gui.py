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


security_init()

# Directories / log files
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

def _today_log_path() -> str:
    return os.path.join(LOG_DIR, f"log_{datetime.date.today()}.txt")

SYSTEM_LOG_FILE = _today_log_path()
if not os.path.exists(SYSTEM_LOG_FILE):
    with open(SYSTEM_LOG_FILE, "w"):
        pass


# GUI globals
gui_log_area = None
config = load_config()
current_log_entries = []
status_label = None
camera_label = None


# Log helpers
def log_event(event: str, log_type: str = "G") -> None:
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


# GUI
def start_gui() -> None:
    def update_camera_feed():
        frame, status = get_latest_frame_and_status()

        if frame is not None and camera_label is not None:
            # BGR -> RGB, shrink for display
            image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = cv2.resize(image, (320, 240))
            imgtk = ImageTk.PhotoImage(image=Image.fromarray(image))
            camera_label.imgtk = imgtk  # prevent GC
            camera_label.config(image=imgtk)

        if status_label is not None:
            status_label.config(
                text=status,
                fg=("red" if str(status).lower().startswith("record") else "green"),
            )

        # refresh ~5x per second
        if camera_label is not None:
            camera_label.after(200, update_camera_feed)

    def show_main_gui():
        
        def show_progress_status():
            popup = tk.Toplevel()
            popup.title("Progressive enrollment status")
            popup.resizable(False, False)

            cols = ("ID", "Status", "Dataset ready")
            tree = ttk.Treeview(popup, columns=cols, show="headings", height=12)
            for c in cols:
                tree.heading(c, text=c)
                tree.column(c, width=140, anchor="center")
            tree.pack(padx=10, pady=10)

            try:
                # 1) Fetch all REGISTERED IDs from the sensor
                ids = sorted(load_used_ids())  # e.g., {1, 2, 5, ...}
                # 2) Get progress for those IDs (IDs without video will get 0/target)
                rows = get_progress_for_ids(ids) if ids else []

                # 3) Fill the table: ID | "x/target" | True/False
                for r in rows:
                    status_text = f'{r["count"]}/{r["target"]}'
                    ready_text = "True" if r["ready"] else "False"
                    tree.insert("", tk.END, values=(r["user_id"], status_text, ready_text))

                if not rows:
                    # No IDs - show empty grid without error
                    pass

            except Exception as e:
                messagebox.showerror("Error", f"Cannot load status.\n{e}")
        def on_generate_registration_pin():
            try:
                pin = cfg_generate_registration_pin()
                log_event("Registration PIN generated (securely stored)", "P")

                # Custom popup (no re-show after closing)
                popup = tk.Toplevel()
                popup.title("Registration PIN")
                popup.resizable(False, False)
                popup.transient()

                tk.Label(popup, text="Your registration PIN:", font=("Arial", 12)).pack(
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
                        "Remember and write down the PIN - after closing this window\n"
                        "the PIN cannot be shown again.\n"
                        "The PIN stays valid until it is used during enrollment."
                    ),
                    justify="center",
                ).pack(padx=16, pady=(0, 12))

                btn_frame = tk.Frame(popup)
                btn_frame.pack(pady=(0, 16))

                def do_copy():
                    popup.clipboard_clear()
                    popup.clipboard_append(pin)
                    popup.update()  # keep clipboard after closing
                    copy_btn.config(text="Copied")

                copy_btn = tk.Button(btn_frame, text="Copy to clipboard", width=18, command=do_copy)
                copy_btn.grid(row=0, column=0, padx=6)

                tk.Button(btn_frame, text="Close", width=12, command=popup.destroy)\
                    .grid(row=0, column=1, padx=6)

                popup.grab_set()
                popup.focus_set()

            except Exception as e:
                messagebox.showerror("Error", f"Unable to generate PIN.\nDetails: {e}")

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
            flt = (None if selected_filter == "All" else f"[{selected_filter}]")
            for entry in current_log_entries:
                if flt is None or flt in entry:
                    gui_log_area.insert(tk.END, entry)
            gui_log_area.see(tk.END)

        def manage_ids():
            popup = tk.Toplevel()
            popup.title("Manage IDs")

            tk.Label(popup, text="Recorded IDs:").pack(pady=(10, 0))
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

                # 1) delete fingerprint from device and local list
                delete_used_id(id_to_delete)
                # 2) delete all user PINs tied to that ID
                removed = remove_pins_for_id(id_to_delete)
                # 3) ALSO CLEAR PROGRESS for that ID
                try:
                    remove_user(id_to_delete)
                except Exception:
                    pass

                messagebox.showinfo(
                    "Success",
                    f"ID {id_to_delete} deleted.\nUser PINs removed: {removed}.",
                )
                refresh_list()

            def delete_all():
                if messagebox.askyesno(
                    "Confirm",
                    "Really delete ALL IDs, fingerprints, and user PINs?",
                ):
                    delete_all_fingerprints()
                    clear_used_ids()
                    wipe_all_user_pins()
                    # clear progress as well
                    try:
                        clear_all_users()
                    except Exception:
                        pass
                    refresh_list()
                    messagebox.showinfo(
                        "Success", "All IDs, fingerprints, and user PINs have been deleted."
                    )

            listbox.bind("<<ListboxSelect>>", on_select)
            delete_button = tk.Button(popup, text="Delete selected ID", command=delete_selected, state="disabled")
            delete_button.pack(pady=5)
            tk.Button(popup, text="Delete all IDs and fingerprints", command=delete_all).pack(pady=(0, 10))

            refresh_list()

        # UI Layout
        global gui_log_area, camera_label, status_label

        root = tk.Tk()
        root.title("Security System GUI")

        main_frame = tk.Frame(root)
        main_frame.pack(padx=10, pady=10)

        left_frame = tk.Frame(main_frame)
        left_frame.grid(row=0, column=0, padx=10, sticky="n")

        right_frame = tk.Frame(main_frame)
        right_frame.grid(row=0, column=1, padx=10, sticky="n")

        # Buttons (left)
        tk.Button(
            left_frame,
            text="Generate registration PIN",
            width=30,
            command=on_generate_registration_pin,
        ).grid(row=0, column=0, pady=(0, 10))

        tk.Label(left_frame, text="System log").grid(row=1, column=0)
        gui_log_area = scrolledtext.ScrolledText(left_frame, width=50, height=20, wrap=tk.WORD)
        gui_log_area.grid(row=2, column=0)

        tk.Label(left_frame, text="Select log file:").grid(row=3, column=0, pady=(10, 0))
        files = sorted(os.listdir(LOG_DIR))
        log_selector = ttk.Combobox(left_frame, width=47, values=files)
        today_name = os.path.basename(SYSTEM_LOG_FILE)
        if today_name not in files:
            files.append(today_name)
            log_selector.configure(values=sorted(files))
        log_selector.set(today_name)
        log_selector.grid(row=4, column=0, pady=5)

        tk.Label(left_frame, text="Filter by message type:").grid(row=5, column=0, pady=(10, 0))
        filter_selector = ttk.Combobox(left_frame, width=47, values=["All", "G", "P", "C", "F"])
        filter_selector.set("All")
        filter_selector.grid(row=6, column=0, pady=5)

        tk.Button(left_frame, text="Manage IDs", width=30, command=manage_ids)\
            .grid(row=7, column=0, pady=(10, 0))
        
        tk.Button(left_frame, text="Progressive enrollment status", width=30, command=show_progress_status)\
            .grid(row=8, column=0, pady=(10, 0))
    
        # Camera (right)
        tk.Label(right_frame, text="Camera").pack()
        camera_label = tk.Label(right_frame)
        camera_label.pack()

        status_label = tk.Label(right_frame, text="Not Recording", fg="green", font=("Arial", 12))
        status_label.pack(pady=5)

        # Bindings and initial load
        log_selector.bind("<<ComboboxSelected>>", load_selected_log)
        filter_selector.bind("<<ComboboxSelected>>", apply_filter)

        load_selected_log()
        update_camera_feed()
        log_event("GUI started", "G")
        root.mainloop()

    def try_login():
        entered_user = user_entry.get().strip()
        entered_pass = hash_password(pass_entry.get())
        if entered_user == config.get("username") and entered_pass == config.get("password_hash"):
            login_win.destroy()
            show_main_gui()
        else:
            messagebox.showerror("Error", "Incorrect username or password")

    # Login window 
    login_win = tk.Tk()
    login_win.title("Login")
    tk.Label(login_win, text="Username:").pack(pady=5)
    user_entry = tk.Entry(login_win)
    user_entry.pack(pady=5)
    tk.Label(login_win, text="Password:").pack(pady=5)
    pass_entry = tk.Entry(login_win, show="*")
    pass_entry.pack(pady=5)
    tk.Button(login_win, text="Sign in", command=try_login).pack(pady=10)
    login_win.mainloop()
