"""Windows folder picker in a separate process, so the server stays responsive."""
import sys
import tkinter as tk
from tkinter import filedialog

sys.stdout.reconfigure(encoding='utf-8')
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
try:
    folder = filedialog.askdirectory(title='选择 E 盘保存文件夹', initialdir='E:/', parent=root)
    print(folder)
finally:
    root.destroy()
