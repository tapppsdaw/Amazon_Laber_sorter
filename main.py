"""亚马逊外箱标签智能分组系统 - 入口文件"""

import tkinter as tk
from gui import App


def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
