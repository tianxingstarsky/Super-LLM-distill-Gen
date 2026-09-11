"""User-invoked local folder dialog; never creates folders or runs in the background."""

def choose_existing():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    try:
        return filedialog.askdirectory(parent=root, title="打开已有的数据文件夹", mustexist=True) or None
    finally:
        root.destroy()
