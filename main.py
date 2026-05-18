"""亚马逊外箱标签智能分组系统 - 入口文件"""

import os
import tkinter as tk
from gui import App


def main():
    root = tk.Tk()
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    if os.path.exists(icon_path):
        root.iconbitmap(icon_path)
    app = App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
